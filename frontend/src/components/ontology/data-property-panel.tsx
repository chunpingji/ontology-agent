"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createDataProperty,
  deleteDataProperty,
  listDataProperties,
  updateDataProperty,
  type TBoxDataProperty,
} from "@/lib/api";
import { RiskAttributeWizard } from "@/components/ontology/risk-attribute-wizard";
import { Field } from "@/components/ontology/field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";
const DATATYPES = ["string", "integer", "decimal", "boolean", "date", "dateTime", "anyURI"];

type Mode = "list" | "create" | "edit" | "risk";
type FormState = {
  slpra_iri: string;
  label: string;
  domain_iri: string;
  datatype: string;
  unit: string;
  vocab_key: string;
  vocab_values: string[];
};

const emptyForm = (domainIri: string | null): FormState => ({
  slpra_iri: MANAGED_PREFIX,
  label: "",
  domain_iri: domainIri ?? "",
  datatype: "string",
  unit: "",
  vocab_key: "",
  vocab_values: [],
});

/**
 * 数据属性面板（T033）。以「列表 + 列表元素增删改」组织：
 * 默认展示当前类的数据属性列表，每行可编辑 / 删除；顶部按钮新增数据属性或风险属性。
 * 风险属性走受控词表向导（{@link RiskAttributeWizard}），其余字段属性走通用表单。
 */
export function DataPropertyPanel({
  selectedClassIri,
  onChanged,
}: {
  selectedClassIri: string | null;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<TBoxDataProperty[]>([]);
  const [mode, setMode] = useState<Mode>("list");
  const [editing, setEditing] = useState<TBoxDataProperty | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm(selectedClassIri));
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [vocabInput, setVocabInput] = useState("");

  const load = useCallback(() => {
    listDataProperties(selectedClassIri ?? undefined, true)
      .then((rows) =>
        // 本类直接声明的排在前，继承来的排在后
        setItems([...rows].sort((a, b) => Number(!!a.inherited_from_iri) - Number(!!b.inherited_from_iri))),
      )
      .catch((e) => setError(String(e)));
  }, [selectedClassIri]);

  // selectedClassIri 改变时由父级以 key 重挂载（mode 经 useState 初值复位为 "list"）；
  // effect 只负责加载，避免在 effect 体内同步 setState 触发级联渲染。
  useEffect(() => {
    load();
  }, [load]);

  const backToList = () => {
    setMode("list");
    setEditing(null);
    setError(null);
  };

  const afterWrite = (message: string) => {
    setMsg(message);
    load();
    onChanged();
    backToList();
  };

  const startCreate = () => {
    setEditing(null);
    setForm(emptyForm(selectedClassIri));
    setError(null);
    setMsg(null);
    setMode("create");
  };

  const startEdit = (dp: TBoxDataProperty) => {
    setEditing(dp);
    const cv = dp.controlled_vocab as Record<string, unknown> | null;
    setForm({
      slpra_iri: dp.slpra_iri,
      label: dp.label ?? "",
      domain_iri: dp.domain_iri ?? "",
      datatype: dp.datatype,
      unit: dp.unit ?? "",
      vocab_key: (cv?.vocab as string) ?? "",
      vocab_values: Array.isArray(cv?.values) ? (cv.values as string[]) : [],
    });
    setError(null);
    setMsg(null);
    setMode("edit");
  };

  const submitForm = async () => {
    setError(null);
    const controlled_vocab =
      form.vocab_values.length > 0
        ? { vocab: form.vocab_key || undefined, values: form.vocab_values }
        : null;
    try {
      if (mode === "edit" && editing) {
        await updateDataProperty(editing.slpra_iri, {
          label: form.label,
          domain_iri: form.domain_iri || null,
          datatype: form.datatype,
          unit: form.unit || null,
          controlled_vocab,
          expected_version: editing.version,
        });
        afterWrite("已更新数据属性");
      } else {
        await createDataProperty({
          slpra_iri: form.slpra_iri,
          label: form.label,
          domain_iri: form.domain_iri || null,
          datatype: form.datatype,
          unit: form.unit || null,
          controlled_vocab,
        });
        afterWrite("已创建数据属性");
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (dp: TBoxDataProperty) => {
    if (!confirm(`删除数据属性「${dp.label || dp.slpra_iri}」？`)) return;
    setError(null);
    try {
      await deleteDataProperty(dp.slpra_iri, dp.version);
      afterWrite("已删除数据属性");
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">
          数据属性
          <span className="ml-1 text-xs font-normal text-muted-foreground">({items.length})</span>
        </h3>
        {mode === "list" && (
          <div className="flex gap-2">
            <Button
              onClick={startCreate}
              size="sm"
              className="h-auto rounded px-2.5 py-1 text-xs"
            >
              + 数据属性
            </Button>
            <Button
              onClick={() => {
                setError(null);
                setMsg(null);
                setMode("risk");
              }}
              size="sm"
              className="h-auto rounded bg-destructive px-2.5 py-1 text-xs text-destructive-foreground hover:bg-destructive/90"
            >
              + 风险属性
            </Button>
          </div>
        )}
      </div>

      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}
      {msg && mode === "list" && (
        <p className="rounded bg-success/10 px-2 py-1 text-xs text-success">{msg}</p>
      )}

      {/* 列表视图 */}
      {mode === "list" && (
        <ul className="divide-y rounded border text-sm">
          {items.map((dp) => {
            const isRisk = dp.controlled_vocab != null;
            const inherited = dp.inherited_from_iri != null;
            return (
              <li
                key={dp.id}
                className={`flex items-center justify-between gap-2 px-2 py-1.5 ${inherited ? "bg-muted/60" : ""}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {dp.slpra_iri.split("/").pop()}
                    </span>
                    {dp.label && <span className="truncate text-foreground">{dp.label}</span>}
                    {isRisk && (
                      <span className="shrink-0 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive">
                        风险
                      </span>
                    )}
                    {inherited && (
                      <span
                        className="shrink-0 rounded bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
                        title={`继承自 ${dp.inherited_from_label ?? dp.inherited_from_iri}`}
                      >
                        继承自 {dp.inherited_from_label ?? dp.inherited_from_iri?.split("/").pop()}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                    <span className="rounded bg-muted px-1.5 py-0.5">{dp.datatype}</span>
                    {dp.unit && <span className="rounded bg-muted px-1.5 py-0.5">{dp.unit}</span>}
                    {isRisk && (dp.controlled_vocab as Record<string, unknown>)?.vocab && (
                      <span className="rounded bg-muted px-1.5 py-0.5">
                        词表: {String((dp.controlled_vocab as Record<string, unknown>).vocab)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {inherited ? (
                    <span className="rounded border border-dashed px-2 py-0.5 text-xs text-muted-foreground">
                      只读
                    </span>
                  ) : (
                    <>
                      <Button
                        onClick={() => startEdit(dp)}
                        variant="outline"
                        size="sm"
                        className="h-auto rounded px-2 py-0.5 text-xs text-muted-foreground"
                      >
                        编辑
                      </Button>
                      <Button
                        onClick={() => remove(dp)}
                        variant="outline"
                        size="sm"
                        className="h-auto rounded border-destructive/40 px-2 py-0.5 text-xs text-destructive hover:bg-destructive/10"
                      >
                        删除
                      </Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
          {items.length === 0 && (
            <li className="px-2 py-3 text-center text-xs text-muted-foreground">
              {selectedClassIri ? "该类暂无数据属性" : "暂无数据属性"}
            </li>
          )}
        </ul>
      )}

      {/* 新建 / 编辑表单 */}
      {(mode === "create" || mode === "edit") && (
        <div className="space-y-2 rounded border bg-muted p-2">
          <p className="text-xs font-medium text-muted-foreground">
            {mode === "edit" ? "编辑数据属性" : "新建数据属性"}
          </p>
          <Field label="IRI" hint="数据属性唯一标识">
            <Input
              placeholder="IRI"
              value={form.slpra_iri}
              disabled={mode === "edit"}
              onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
              className="h-auto rounded px-2 py-1 font-mono text-xs disabled:bg-muted disabled:text-muted-foreground"
            />
          </Field>
          <Field label="标签" hint="显示名称">
            <Input
              placeholder="标签"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="h-auto rounded px-2 py-1 text-sm"
            />
          </Field>
          <Field label="定义域 domain" hint="属性所属的类 IRI">
            <Input
              placeholder="domain IRI"
              value={form.domain_iri}
              onChange={(e) => setForm({ ...form, domain_iri: e.target.value })}
              className="h-auto rounded px-2 py-1 font-mono text-xs"
            />
          </Field>
          <div className="flex gap-2">
            <Field label="数据类型 datatype" className="w-1/2">
              <Select
                value={form.datatype}
                onValueChange={(value) => setForm({ ...form, datatype: value })}
              >
                <SelectTrigger className="h-auto rounded px-2 py-1 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DATATYPES.map((d) => (
                    <SelectItem key={d} value={d}>
                      {d}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="单位" hint="可选" className="w-1/2">
              <Input
                placeholder="单位（可选）"
                value={form.unit}
                onChange={(e) => setForm({ ...form, unit: e.target.value })}
                className="h-auto rounded px-2 py-1 text-sm"
              />
            </Field>
          </div>
          <Field label="受控词表" hint="可选，定义属性的合法取值列表">
            <Input
              placeholder="词表标识（可选，如 OEB）"
              value={form.vocab_key}
              onChange={(e) => setForm({ ...form, vocab_key: e.target.value })}
              className="mb-1 h-auto rounded px-2 py-1 text-xs"
            />
            <div className="flex gap-1">
              <Input
                placeholder="输入取值后按 Enter 添加"
                value={vocabInput}
                onChange={(e) => setVocabInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const v = vocabInput.trim();
                    if (v && !form.vocab_values.includes(v)) {
                      setForm({ ...form, vocab_values: [...form.vocab_values, v] });
                    }
                    setVocabInput("");
                  }
                }}
                className="h-auto rounded px-2 py-1 text-xs"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-auto shrink-0 rounded px-2 py-1 text-xs"
                onClick={() => {
                  const v = vocabInput.trim();
                  if (v && !form.vocab_values.includes(v)) {
                    setForm({ ...form, vocab_values: [...form.vocab_values, v] });
                  }
                  setVocabInput("");
                }}
              >
                添加
              </Button>
            </div>
            {form.vocab_values.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {form.vocab_values.map((val) => (
                  <Badge
                    key={val}
                    variant="secondary"
                    className="gap-1 rounded px-1.5 py-0.5 text-xs font-normal"
                  >
                    {val}
                    <button
                      type="button"
                      className="ml-0.5 text-muted-foreground hover:text-foreground"
                      onClick={() =>
                        setForm({
                          ...form,
                          vocab_values: form.vocab_values.filter((v) => v !== val),
                        })
                      }
                    >
                      &times;
                    </button>
                  </Badge>
                ))}
              </div>
            )}
          </Field>
          <div className="flex gap-2">
            <Button
              onClick={submitForm}
              size="sm"
              className="h-auto rounded px-3 py-1.5 text-sm"
            >
              {mode === "edit" ? "保存" : "创建数据属性"}
            </Button>
            <Button
              onClick={backToList}
              variant="outline"
              size="sm"
              className="h-auto rounded px-3 py-1.5 text-sm text-muted-foreground"
            >
              取消
            </Button>
          </div>
        </div>
      )}

      {/* 新建风险属性（受控词表向导） */}
      {mode === "risk" && (
        <div className="rounded border bg-muted p-2">
          <RiskAttributeWizard
            selectedClassIri={selectedClassIri}
            onChanged={() => {
              load();
              onChanged();
            }}
            onClose={backToList}
          />
        </div>
      )}
    </div>
  );
}
