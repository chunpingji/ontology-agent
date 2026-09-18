"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { reportPost, type OutputUnit, type SemanticPatch, type SemanticSourceOption, type TemplateV2 } from "@/lib/reporting-v2";

const fieldClass = "mt-1 block w-full min-w-0 rounded border bg-background p-2 text-sm";

export function SlotSemanticsEditor({ templateId, sourceJobId, template, unit, options, onApply, onChange, onRefresh }: {
  templateId?: string; sourceJobId?: string | null; template: TemplateV2; unit: OutputUnit;
  options: SemanticSourceOption[]; onApply: (baseline: TemplateV2, patch: SemanticPatch) => void;
  onChange: (change: (next: TemplateV2) => void) => void;
  onRefresh: () => void;
}) {
  const [sourceKey, setSourceKey] = useState("");
  const [fields, setFields] = useState<string[]>([]);
  const [form, setForm] = useState<"narrative" | "table">("narrative");
  const [required, setRequired] = useState(true);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
  const option = options.find((item) => item.key === sourceKey);
  const configured = unit.inputs.length > 0;
  const prompt = unit.render.prompt as { instructions?: string } | undefined;
  async function configure() {
    if (!templateId || !option || !fields.length || busy) return;
    const baseline = structuredClone(template);
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    try {
      const patch = await reportPost<SemanticPatch>(`ast-templates/${templateId}/configure-slot`, {
        draft_schema: baseline, source_job_id: sourceJobId || null,
        choice: { output_id: unit.output_id, sources: [{ source_key: sourceKey, fields }],
          render: form, instructions, required },
      }, controller.signal);
      if (!controller.signal.aborted) onApply(baseline, patch);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "关联失败"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section aria-label="Slot 语义配置" className="space-y-3 rounded border bg-muted/20 p-3">
    <p className="text-sm font-medium">内容项（Slot）· {configured ? "已关联数据" : "待关联数据"}</p>
    <Button size="sm" variant="ghost" onClick={onRefresh}>刷新来源目录与当前值</Button>
    {configured && <div className="space-y-1 text-xs text-muted-foreground">
      {unit.bindings.map((ref) => <p key={ref.binding_ref}>{String(template.definitions.bindings[ref.binding_ref]?.label || "已配置来源")}</p>)}
      <p>使用字段：{unit.inputs.map((ref) => template.definitions.inputs[ref.input_ref]?.label || ref.alias).join("、")}</p>
    </div>}
    <details open={!configured}><summary className="cursor-pointer text-sm">{configured ? "重新选择来源与输出" : "选择图谱或外部数据"}</summary>
      <label className="mt-2 block text-sm">依据什么<select aria-label="Slot 数据来源" className={fieldClass} value={sourceKey}
        onChange={(event) => {
          setSourceKey(event.target.value); setFields([]);
          const source = options.find((item) => item.key === event.target.value);
          setForm(source?.kind === "mock" || (source?.available_count ?? 0) > 1 ? "table" : "narrative");
        }}><option value="">选择来源</option>{options.map((source) => <option key={source.key} value={source.key}>{source.label}</option>)}</select></label>
      {option && <>
        <p className="my-2 text-xs text-muted-foreground">{option.kind === "mock" ? "生成时读取本地 Mock，名单不代表签署。"
          : option.state !== "ready" ? "当前来源尚未就绪；可配置字段，预览会显示实际缺口。"
          : `当前图谱含 ${option.available_count} 个对象。`}</p>
        <div className="max-h-56 overflow-y-auto space-y-1">{option.fields.map((field) => <label className="flex items-center gap-2 text-sm" key={field.key}>
          <input type="checkbox" checked={fields.includes(field.key)} onChange={(event) => setFields((previous) => event.target.checked ? [...previous, field.key] : previous.filter((key) => key !== field.key))} />
          {field.label}{field.available !== undefined && <span className="text-xs text-muted-foreground">（{field.available} 项有值）</span>}
          {fields.includes(field.key) && !!field.preview_values?.length && <span className="max-w-64 truncate text-xs text-muted-foreground" title={field.preview_values.join("；")}>当前值：{field.preview_values.join("；")}</span>}
        </label>)}</div>
      </>}
      <label className="mt-2 block text-sm">怎样呈现<select aria-label="Slot 输出形式" className={fieldClass} value={form} onChange={(e) => setForm(e.target.value as typeof form)}>
        <option value="narrative" disabled={option?.kind === "mock"}>AI 段落行文（单对象）</option><option value="table">明细表格（每对象一行）</option>
      </select></label>
      {form === "narrative" && <label className="block text-sm">行文要求<textarea aria-label="Slot 行文要求" className={fieldClass} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="说明本段需要表达的内容，实际值来自已选择字段" /></label>}
      <label className="my-2 flex gap-2 text-sm"><input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />所选字段为必填材料</label>
      <Button size="sm" disabled={busy || !templateId || !option || !fields.length} onClick={configure}>{busy ? "正在关联…" : configured ? "应用新关联" : "关联到内容项"}</Button>
    </details>
    {prompt && <label className="block text-sm">当前行文要求<textarea className={fieldClass} value={prompt.instructions || ""} onChange={(e) => onChange((next) => {
      for (const section of next.sections) {
        const visit = (groups: typeof section.groups) => { for (const group of groups) {
          const target = group.units.find((item) => item.output_id === unit.output_id);
          if (target) target.render.prompt = { ...(target.render.prompt as object), instructions: e.target.value };
          visit(group.groups || []);
        } }; visit(section.groups);
      }
    })} /></label>}
    {unit.bindings.map(({ binding_ref }) => {
      const binding = template.definitions.bindings[binding_ref];
      const slot = String(binding?.scope?.record_slot || "");
      const config = template.record_sources?.[slot];
      if (!config) return null;
      const source = options.find((item) => item.provider === config.provider);
      const filterKeys = config.provider === "equipment_schedules" ? ["equipment_id", "product_code", "start_date", "end_date"]
        : config.provider === "equipment" ? ["equipment_id", "workshop_code"]
        : config.provider === "production_areas" ? ["code"] : ["role_code", "department"];
      return <div key={slot} className="space-y-2 border-t pt-2">
        <p className="text-sm font-medium">{source?.label} · 筛选范围</p>
        {filterKeys.map((key) => <label key={key} className="block text-xs">{source?.fields.find((f) => f.key === key)?.label || ({ start_date: "开始日期", end_date: "结束日期" } as Record<string, string>)[key] || key}
          {!key.endsWith("_date") && <select aria-label={`Mock 筛选输入 ${key}`} className={fieldClass} value={config.input_filters?.[key]?.input_id || ""}
            onChange={(e) => onChange((next) => {
              const target = next.record_sources![slot];
              target.input_filters ??= {};
              if (e.target.value) { target.input_filters[key] = { input_id: e.target.value }; delete target.filters[key]; }
              else delete target.input_filters[key];
            })}><option value="">手工指定筛选值</option>{Object.values(template.definitions.inputs).filter((input) => input.binding_ref !== binding_ref).map((input) => <option key={input.input_id} value={input.input_id}>{input.label || input.name}</option>)}</select>}
          {config.input_filters?.[key] && <input aria-label={`Mock 筛选字段路径 ${key}`} className={fieldClass} placeholder="集合列/字段路径（如 equipment_id）" value={config.input_filters[key].field_path?.join(".") || ""}
            onChange={(e) => onChange((next) => { next.record_sources![slot].input_filters![key].field_path = e.target.value.split(".").filter(Boolean); })} />}
          {!config.input_filters?.[key] && <input aria-label={`Mock 筛选 ${key}`} type={key.endsWith("_date") ? "date" : "text"} className={fieldClass} value={String(config.filters[key] || "")}
            placeholder={config.provider === "equipment_schedules" ? "必填" : "不填表示当前提供方全部记录"}
            onChange={(e) => onChange((next) => {
              const filters = next.record_sources![slot].filters;
              if (e.target.value) filters[key] = e.target.value; else delete filters[key];
            })} />}
        </label>)}
      </div>;
    })}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </section>;
}
