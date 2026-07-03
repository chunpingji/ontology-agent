"use client";

import { useEffect, useState } from "react";
import {
  createPropertyBinding,
  deletePropertyBinding,
  getPropertyBindings,
  validateBinding,
  type BindingValidationReport,
  type PropertyBinding,
  type PropertyBindingInput,
  type TBoxMapping,
} from "@/lib/api";
import { Field } from "@/components/ontology/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

const TRANSFORM_TYPES = ["none", "controlled_vocab", "pattern", "cast"];
const OBJECT_RESOLUTIONS = ["id_reference", "nested_object"];

const TRANSFORM_HINT: Record<string, string> = {
  controlled_vocab: '受控词表映射，如 {"map": {"高": "HighRisk"}} 或 {"vocab": "oeb"}',
  pattern: '正则校验，如 {"pattern": "^国药准字[HZSBTFJ]\\\\d{8}$"}',
  cast: '类型转换，如 {"to": "integer"}（string|integer|decimal|boolean|date|dateTime|anyURI）',
};

const EMPTY: PropertyBindingInput = {
  property_iri: "",
  property_kind: "data",
  source_path: "",
  transform_type: "none",
  is_identifier: false,
  is_label: false,
  object_resolution: "id_reference",
  target_class_iri: "",
  target_id_path: "",
};

/**
 * 属性绑定编辑器（014 T020）：为源实体类绑定（db_table / api_endpoint / doc_pattern）
 * 声明「本体属性 → 源字段」的绑定——源字段路径、逐值 transform、标识符/标签标记、
 * 对象属性解析方式。附「校验」面板（FR-005）展示 health + 错误/告警，抽取前即暴露
 * 定义域越界、重复标识符、对象形状缺失、transform 配置非法等问题。
 */
export function PropertyBindingEditor({
  mapping,
  onChanged,
}: {
  mapping: TBoxMapping;
  onChanged?: () => void;
}) {
  const [bindings, setBindings] = useState<PropertyBinding[]>([]);
  const [form, setForm] = useState<PropertyBindingInput>(EMPTY);
  const [configText, setConfigText] = useState("");
  const [report, setReport] = useState<BindingValidationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    getPropertyBindings(mapping.id).then(setBindings).catch((e) => setError(String(e)));
    validateBinding(mapping.id).then(setReport).catch(() => {});
    onChanged?.();
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refresh, [mapping.id]);

  const add = async () => {
    setError(null);
    let transform_config: Record<string, unknown> | null = null;
    if (form.transform_type && form.transform_type !== "none" && configText.trim()) {
      try {
        transform_config = JSON.parse(configText);
      } catch {
        setError("transform_config 不是合法 JSON");
        return;
      }
    }
    const payload: PropertyBindingInput = {
      property_iri: form.property_iri.trim(),
      property_kind: form.property_kind,
      source_path: form.source_path.trim(),
      transform_type: form.transform_type,
      transform_config,
      is_identifier: form.is_identifier,
      is_label: form.is_label,
    };
    if (form.property_kind === "object") {
      payload.object_resolution = form.object_resolution;
      payload.target_class_iri = form.target_class_iri?.trim() || null;
      payload.target_id_path = form.target_id_path?.trim() || null;
    }
    try {
      await createPropertyBinding(mapping.id, payload);
      setForm(EMPTY);
      setConfigText("");
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (b: PropertyBinding) => {
    setError(null);
    try {
      await deletePropertyBinding(b.id, b.version);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const healthTone =
    report?.health === "ok"
      ? "bg-success/10 text-success"
      : report?.health === "drift"
        ? "bg-warning/10 text-warning"
        : "bg-destructive/10 text-destructive";

  const isObject = form.property_kind === "object";

  return (
    <div className="mt-2 space-y-3 rounded border border-border bg-background p-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-foreground">
          属性绑定 <span className="font-mono text-muted-foreground">{mapping.target}</span>
        </p>
        {report && (
          <span className={`rounded px-2 py-0.5 text-xs ${healthTone}`}>
            {report.health}
          </span>
        )}
      </div>

      {error && (
        <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>
      )}

      <ul className="divide-y text-sm">
        {bindings.map((b) => (
          <li key={b.id} className="flex items-center justify-between gap-2 py-1.5">
            <span className="min-w-0 text-xs">
              <Badge variant="secondary" className="font-normal">{b.property_kind}</Badge>
              <span className="ml-2 font-mono text-muted-foreground">{b.source_path}</span>
              <span className="mx-1 text-muted-foreground">→</span>
              <span className="font-mono">{b.property_iri.split(/[/#]/).pop()}</span>
              {b.is_identifier && <Badge className="ml-1" variant="outline">id</Badge>}
              {b.is_label && <Badge className="ml-1" variant="outline">label</Badge>}
              {b.transform_type !== "none" && (
                <Badge className="ml-1" variant="outline">{b.transform_type}</Badge>
              )}
              {b.object_resolution && (
                <Badge className="ml-1" variant="outline">{b.object_resolution}</Badge>
              )}
            </span>
            <Button
              variant="link"
              onClick={() => remove(b)}
              className="h-auto shrink-0 p-0 text-xs text-destructive hover:underline"
            >
              删除
            </Button>
          </li>
        ))}
        {bindings.length === 0 && (
          <li className="py-2 text-xs text-muted-foreground">暂无属性绑定</li>
        )}
      </ul>

      {(report?.errors?.length || report?.warnings?.length) ? (
        <div className="space-y-1 rounded bg-muted p-2 text-xs">
          {report.errors.map((i, idx) => (
            <p key={`e${idx}`} className="text-destructive">✕ [{i.code}] {i.message}</p>
          ))}
          {report.warnings.map((i, idx) => (
            <p key={`w${idx}`} className="text-warning">⚠ [{i.code}] {i.message}</p>
          ))}
        </div>
      ) : null}

      <div className="space-y-2 rounded border border-border bg-muted p-2">
        <p className="text-xs font-medium text-muted-foreground">新建属性绑定</p>
        <div className="flex gap-2">
          <Field label="属性种类" className="w-1/3">
            <Select
              value={form.property_kind}
              onValueChange={(v) => setForm({ ...form, property_kind: v })}
            >
              <SelectTrigger className="h-auto w-full px-2 py-1 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="data">data</SelectItem>
                <SelectItem value="object">object</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="源字段 source_path" hint="表列名 / JSON 路径" className="w-2/3">
            <Input
              placeholder="approval_no"
              value={form.source_path}
              onChange={(e) => setForm({ ...form, source_path: e.target.value })}
              className="h-auto w-full px-2 py-1 text-sm"
            />
          </Field>
        </div>

        <Field label="属性 IRI property_iri" hint="目标本体属性（受定义域校验）">
          <Input
            placeholder="https://…/approvalNumber"
            value={form.property_iri}
            onChange={(e) => setForm({ ...form, property_iri: e.target.value })}
            className="h-auto w-full px-2 py-1 text-sm"
          />
        </Field>

        <div className="flex gap-2">
          <Field label="转换 transform" className="w-1/3">
            <Select
              value={form.transform_type}
              onValueChange={(v) => setForm({ ...form, transform_type: v })}
            >
              <SelectTrigger className="h-auto w-full px-2 py-1 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRANSFORM_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {form.transform_type !== "none" && (
            <Field
              label="transform_config (JSON)"
              hint={TRANSFORM_HINT[form.transform_type ?? ""]}
              className="w-2/3"
            >
              <Input
                placeholder='{"map": {"高": "HighRisk"}}'
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                className="h-auto w-full px-2 py-1 font-mono text-xs"
              />
            </Field>
          )}
        </div>

        {isObject && (
          <div className="flex gap-2">
            <Field label="对象解析" className="w-1/3">
              <Select
                value={form.object_resolution ?? "id_reference"}
                onValueChange={(v) => setForm({ ...form, object_resolution: v })}
              >
                <SelectTrigger className="h-auto w-full px-2 py-1 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {OBJECT_RESOLUTIONS.map((t) => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="目标类 IRI" className="w-1/3">
              <Input
                placeholder="…/Manufacturer"
                value={form.target_class_iri ?? ""}
                onChange={(e) => setForm({ ...form, target_class_iri: e.target.value })}
                className="h-auto w-full px-2 py-1 text-sm"
              />
            </Field>
            <Field label="目标 id 路径" className="w-1/3">
              <Input
                placeholder="mfr_code"
                value={form.target_id_path ?? ""}
                onChange={(e) => setForm({ ...form, target_id_path: e.target.value })}
                className="h-auto w-full px-2 py-1 text-sm"
              />
            </Field>
          </div>
        )}

        <div className="flex items-center gap-4 text-xs">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={form.is_identifier}
              onChange={(e) => setForm({ ...form, is_identifier: e.target.checked })}
            />
            标识符 is_identifier
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={form.is_label}
              onChange={(e) => setForm({ ...form, is_label: e.target.checked })}
            />
            标签 is_label
          </label>
        </div>

        <div className="flex gap-2">
          <Button onClick={add} size="sm" className="text-sm">添加绑定</Button>
          <Button
            onClick={() => validateBinding(mapping.id).then(setReport).catch(() => {})}
            size="sm"
            variant="secondary"
            className="text-sm"
          >
            校验
          </Button>
        </div>
      </div>
    </div>
  );
}
