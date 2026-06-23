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

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";
const DATATYPES = ["string", "integer", "float", "boolean", "date", "dateTime"];

type Mode = "list" | "create" | "edit" | "risk";
type FormState = {
  slpra_iri: string;
  label: string;
  domain_iri: string;
  datatype: string;
  unit: string;
};

const emptyForm = (domainIri: string | null): FormState => ({
  slpra_iri: MANAGED_PREFIX,
  label: "",
  domain_iri: domainIri ?? "",
  datatype: "string",
  unit: "",
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

  const load = useCallback(() => {
    listDataProperties(selectedClassIri ?? undefined, true)
      .then((rows) =>
        // 本类直接声明的排在前，继承来的排在后
        setItems([...rows].sort((a, b) => Number(!!a.inherited_from_iri) - Number(!!b.inherited_from_iri))),
      )
      .catch((e) => setError(String(e)));
  }, [selectedClassIri]);

  useEffect(() => {
    load();
    setMode("list");
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
    setForm({
      slpra_iri: dp.slpra_iri,
      label: dp.label ?? "",
      domain_iri: dp.domain_iri ?? "",
      datatype: dp.datatype,
      unit: dp.unit ?? "",
    });
    setError(null);
    setMsg(null);
    setMode("edit");
  };

  const submitForm = async () => {
    setError(null);
    try {
      if (mode === "edit" && editing) {
        await updateDataProperty(editing.slpra_iri, {
          label: form.label,
          domain_iri: form.domain_iri || null,
          datatype: form.datatype,
          unit: form.unit || null,
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
        <h3 className="text-sm font-semibold text-gray-700">
          数据属性
          <span className="ml-1 text-xs font-normal text-gray-400">({items.length})</span>
        </h3>
        {mode === "list" && (
          <div className="flex gap-2">
            <button
              onClick={startCreate}
              className="rounded bg-blue-600 px-2.5 py-1 text-xs text-white hover:bg-blue-700"
            >
              + 数据属性
            </button>
            <button
              onClick={() => {
                setError(null);
                setMsg(null);
                setMode("risk");
              }}
              className="rounded bg-rose-600 px-2.5 py-1 text-xs text-white hover:bg-rose-700"
            >
              + 风险属性
            </button>
          </div>
        )}
      </div>

      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}
      {msg && mode === "list" && (
        <p className="rounded bg-green-50 px-2 py-1 text-xs text-green-600">{msg}</p>
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
                className={`flex items-center justify-between gap-2 px-2 py-1.5 ${inherited ? "bg-gray-50/60" : ""}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-xs text-gray-500">
                      {dp.slpra_iri.split("/").pop()}
                    </span>
                    {dp.label && <span className="truncate text-gray-800">{dp.label}</span>}
                    {isRisk && (
                      <span className="shrink-0 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] text-rose-700">
                        风险
                      </span>
                    )}
                    {inherited && (
                      <span
                        className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
                        title={`继承自 ${dp.inherited_from_label ?? dp.inherited_from_iri}`}
                      >
                        继承自 {dp.inherited_from_label ?? dp.inherited_from_iri?.split("/").pop()}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[11px] text-gray-400">
                    <span className="rounded bg-gray-100 px-1.5 py-0.5">{dp.datatype}</span>
                    {dp.unit && <span className="rounded bg-gray-100 px-1.5 py-0.5">{dp.unit}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {inherited ? (
                    <span className="rounded border border-dashed px-2 py-0.5 text-xs text-gray-400">
                      只读
                    </span>
                  ) : (
                    <>
                      <button
                        onClick={() => startEdit(dp)}
                        className="rounded border px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => remove(dp)}
                        className="rounded border border-red-200 px-2 py-0.5 text-xs text-red-600 hover:bg-red-50"
                      >
                        删除
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
          {items.length === 0 && (
            <li className="px-2 py-3 text-center text-xs text-gray-400">
              {selectedClassIri ? "该类暂无数据属性" : "暂无数据属性"}
            </li>
          )}
        </ul>
      )}

      {/* 新建 / 编辑表单 */}
      {(mode === "create" || mode === "edit") && (
        <div className="space-y-2 rounded border bg-gray-50 p-2">
          <p className="text-xs font-medium text-gray-600">
            {mode === "edit" ? "编辑数据属性" : "新建数据属性"}
          </p>
          <Field label="IRI" hint="数据属性唯一标识">
            <input
              placeholder="IRI"
              value={form.slpra_iri}
              disabled={mode === "edit"}
              onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs disabled:bg-gray-100 disabled:text-gray-400"
            />
          </Field>
          <Field label="标签" hint="显示名称">
            <input
              placeholder="标签"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="w-full rounded border px-2 py-1 text-sm"
            />
          </Field>
          <Field label="定义域 domain" hint="属性所属的类 IRI">
            <input
              placeholder="domain IRI"
              value={form.domain_iri}
              onChange={(e) => setForm({ ...form, domain_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs"
            />
          </Field>
          <div className="flex gap-2">
            <Field label="数据类型 datatype" className="w-1/2">
              <select
                value={form.datatype}
                onChange={(e) => setForm({ ...form, datatype: e.target.value })}
                className="w-full rounded border px-2 py-1 text-sm"
              >
                {DATATYPES.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="单位" hint="可选" className="w-1/2">
              <input
                placeholder="单位（可选）"
                value={form.unit}
                onChange={(e) => setForm({ ...form, unit: e.target.value })}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </Field>
          </div>
          <div className="flex gap-2">
            <button
              onClick={submitForm}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
            >
              {mode === "edit" ? "保存" : "创建数据属性"}
            </button>
            <button
              onClick={backToList}
              className="rounded border px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 新建风险属性（受控词表向导） */}
      {mode === "risk" && (
        <div className="rounded border bg-gray-50 p-2">
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
