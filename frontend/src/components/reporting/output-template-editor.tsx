"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  compileTemplate, consumersOf, groupsIn, moveUnit, reportGet, reportPost, unitsIn,
  syncBindingDependencies, changeSourceType, resolveModelBinding, automaticChecks, type OntologyClassContract,
  type BindingDefinition, type Compilation, type ContentNode, type InputDefinition,
  type OutputGroup, type OutputRender, type OutputUnit,
  type RegisteredContract, type TemplateV2,
} from "@/lib/reporting-v2";
import { SemanticBindingFields, SemanticProjectionFields } from "./semantic-binding-fields";
import { TemplateReportPreview } from "./template-report-preview";
import { TemplateSlotEditor, type TemplateMeta, type TemplateVersionEntry } from "@/components/extraction/template-slot-editor";
import { type TemplateOrigin, type TiptapContent } from "@/lib/api";

const fieldClass = "w-full min-w-0 rounded border bg-background px-3 py-2 text-sm";

export function ContractJson<T>({ value, onApply, label }: {
  value: T; onApply: (next: T) => void; label: string;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  return <details className="rounded border p-3"><summary>{label}</summary>
    <textarea aria-label={label} className={fieldClass + " font-mono mt-3 min-h-48"}
      value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
    <Button variant="outline" size="sm" onClick={() => {
      try { onApply(JSON.parse(text) as T); setError(""); }
      catch { setError("请输入有效的 JSON。"); }
    }}>应用配置</Button>{error && <p role="alert">{error}</p>}
  </details>;
}

function initialRender(kind: OutputRender["kind"], inputId = ""): OutputRender {
  if (kind === "table") return { kind, rows: { input_id: inputId },
    columns: [{ column_id: crypto.randomUUID(), title: "字段", field_ref: "field" }] };
  if (kind === "form") return { kind, fields: [] };
  if (kind === "list") return { kind, items: { input_id: inputId }, nodes: [] };
  if (kind === "static") return { kind, nodes: [], approval_ref: "unresolved:static" };
  return { kind, mode: "composed", nodes: [] };
}

export function OutputTemplateEditor({ schema, templateId, schemaHash, saving, onSave, onCancel,
  defaultSourceJobId, versions = [], onVersionSwitch, sampleContentJson, sampleText, meta, onMetaSaved }: {
  schema: TemplateV2; templateId?: string; schemaHash?: string; saving?: boolean;
  defaultSourceJobId?: string | null;
  onSave: (next: TemplateV2) => void; onCancel: () => void;
  versions?: TemplateVersionEntry[]; onVersionSwitch?: (id: string) => void;
  sampleContentJson?: TiptapContent | null; sampleText?: string | null;
  meta?: TemplateMeta; onMetaSaved?: () => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(schema));
  const [selected, setSelected] = useState("");
  const [tab, setTab] = useState<"bindings" | "inputs" | "render">("render");
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<Compilation | null>(null);
  const [busy, setBusy] = useState(false);
  const contracts = useQuery({ queryKey: ["report-contracts"], queryFn: () => reportGet<RegisteredContract[]>("report-contracts") });
  const model = useQuery({ queryKey: ["report-model-context", draft.ontology_release_ref],
    queryFn: () => reportGet<RegisteredContract>("report-model-context?ref=" + encodeURIComponent(draft.ontology_release_ref)) });
  const ontology = (model.data?.definition.classes ?? {}) as Record<string, OntologyClassContract>;
  const resolvedSchema = { ...draft, calculation_checks: automaticChecks(draft, contracts.data ?? []) };
  const primarySource = draft.source_slots.find((s) => s.kind === "document");
  const unit = unitsIn(draft).find((u) => u.output_id === selected);
  function edit(fn: (next: TemplateV2) => void) {
    setDraft((current) => { const next = structuredClone(current); fn(next); return syncBindingDependencies(next); });
    setPlan(null);
  }
  function editUnit(fn: (next: OutputUnit) => void) {
    edit((next) => { const target = unitsIn(next).find((u) => u.output_id === selected); if (target) fn(target); });
  }
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  function addUnit(groupId: string) {
    const id = crypto.randomUUID();
    edit((next) => {
      next.sections.flatMap((s) => groupsIn(s.groups)).find((g) => g.group_id === groupId)?.units.push({
        output_id: id, title: "新内容", bindings: [], inputs: [], render: initialRender("narrative"),
      });
    });
    setSelected(id);
  }
  function inputSelect(value: string, change: (id: string) => void) {
    return <select aria-label="选择输入" className={fieldClass} value={value} onChange={(e) => change(e.target.value)}>
      <option value="">选择输入</option>
      {unit?.inputs.map((ref) => <option key={ref.input_ref} value={ref.input_ref}>
        {draft.definitions.inputs[ref.input_ref]?.label || ref.alias}</option>)}
    </select>;
  }
  function groupTree(group: OutputGroup) {
    return <details open key={group.group_id} className="rounded border p-2 my-3"
      onDragOver={(e) => e.preventDefault()} onDrop={(e) => {
        e.preventDefault(); e.stopPropagation();
        const id = e.dataTransfer.getData("output");
        if (id) { setDraft(moveUnit(draft, id, group.group_id, group.units.length)); setPlan(null); }
      }}>
      <summary className="cursor-pointer text-sm font-medium">
      <input aria-label="分组标题" placeholder="未命名分组" className="w-[calc(100%-1.5rem)] bg-transparent font-medium text-sm" value={group.title}
        onChange={(e) => edit((next) => {
          const target = next.sections.flatMap((s) => groupsIn(s.groups)).find((g) => g.group_id === group.group_id);
          if (target) target.title = e.target.value;
        })} />
      </summary>
      {group.units.map((item, index) => <div key={item.output_id} className="my-1 rounded border">
        <div draggable onDragStart={(e) => e.dataTransfer.setData("output", item.output_id)}
          className={"flex items-center gap-1 rounded px-2 py-1 " + (selected === item.output_id ? "bg-accent" : "")}>
        <button aria-expanded={selected === item.output_id} className="flex-1 min-w-0 text-left text-sm py-1"
          onClick={() => setSelected(selected === item.output_id ? "" : item.output_id)}>{item.title || "未命名内容"}</button>
        <button aria-label="上移内容" disabled={index === 0} onClick={() => {
          setDraft(moveUnit(draft, item.output_id, group.group_id, index - 1)); setPlan(null);
        }}>↑</button>
        <button aria-label="下移内容" disabled={index === group.units.length - 1} onClick={() => {
          setDraft(moveUnit(draft, item.output_id, group.group_id, index + 1)); setPlan(null);
        }}>↓</button>
        </div>
        {selected === item.output_id && <div className="border-t p-3">{unitEditor}</div>}
      </div>)}
      <button className="text-xs text-primary py-1" onClick={() => addUnit(group.group_id)}>＋添加内容</button>
      {group.groups?.map(groupTree)}
    </details>;
  }
  function addBinding() {
    const id = crypto.randomUUID();
    const slot = draft.source_slots[0];
    const binding: BindingDefinition = {
      binding_id: id, kind: "facts", contract_ref: {
        kind: "ontology", release_ref: "template.ontology_release_ref",
        root_class_iri: "auto:class", result_class_iri: "auto:class",
      }, scope: { source_slot: slot?.source_slot_id || "source", predicate_path: [] },
    };
    edit((next) => {
      next.definitions.bindings[id] = binding;
      unitsIn(next).find((u) => u.output_id === selected)?.bindings.push({ binding_ref: id });
    });
  }
  function addInput() {
    const id = crypto.randomUUID();
    const input: InputDefinition = {
      input_id: id, name: "input_" + id, label: "新输入", binding_ref: unit?.bindings[0]?.binding_ref || "",
      projection: { kind: "property", property_iri: "" },
    };
    edit((next) => {
      next.definitions.inputs[id] = input;
      unitsIn(next).find((u) => u.output_id === selected)?.inputs.push({ input_ref: id, alias: input.name });
    });
  }
  function addToken(node: ContentNode) {
    editUnit((next) => { next.render.nodes = [...(next.render.nodes ?? []), node]; });
  }

  const unitEditor = <div className="space-y-4 min-w-0">{!unit ? <p className="text-muted-foreground">选择或添加一项报告内容，配置绑定、输入和呈现。</p> : <>
          <input aria-label="内容标题" className={fieldClass + " font-semibold"} value={unit.title}
            onChange={(e) => editUnit((next) => { next.title = e.target.value; })} />
          <Tabs value={tab} onValueChange={(value) => setTab(value as typeof tab)}>
            <TabsList aria-label="内容配置" className="grid w-full grid-cols-3">
              {(["bindings", "inputs", "render"] as const).map((key, i) => <TabsTrigger key={key} value={key} className="px-2 text-xs">
                {["数据绑定", "输入变量", "呈现方式"][i]}
              </TabsTrigger>)}
            </TabsList>
          <TabsContent value="bindings" className="space-y-4">
            <p className="text-sm text-muted-foreground">绑定确定版本、主体、时间与来源范围，使用精确本体 IRI。</p>
            {unit.bindings.map((ref) => {
              const original = draft.definitions.bindings[ref.binding_ref];
              const value = original ? resolveModelBinding(original, draft, ontology) : original;
              const affectedInputs = Object.values(draft.definitions.inputs).filter((i) => i.binding_ref === ref.binding_ref);
              const affected = [...new Set(affectedInputs.flatMap((i) => consumersOf(draft, i.input_id).map((u) => u.title)))];
              const update = (changed: BindingDefinition) => edit((next) => { next.definitions.bindings[ref.binding_ref] = { ...changed, binding_id: ref.binding_ref }; });
              return value ? <div key={ref.binding_ref} className="border rounded p-4 space-y-3">
                <p className="text-xs text-muted-foreground">修改影响：{affected.join("、") || "当前内容"}</p>
                <SemanticBindingFields binding={value} schema={resolvedSchema} classes={ontology} contracts={contracts.data ?? []} onChange={update} />
                <ContractJson key={ref.binding_ref + JSON.stringify(value)} label="高级范围与依赖配置" value={value} onApply={update} />
              </div> : <p key={ref.binding_ref}>绑定未定义：{ref.binding_ref}</p>;
            })}
            <select aria-label="引用共享绑定" className={fieldClass} value="" onChange={(e) => {
              if (e.target.value) editUnit((next) => { next.bindings.push({ binding_ref: e.target.value }); });
            }}><option value="">引用共享绑定</option>{Object.values(draft.definitions.bindings).filter((b) => !unit.bindings.some((u) => u.binding_ref === b.binding_id))
              .map((b) => <option key={b.binding_id} value={b.binding_id}>{b.kind} · {b.binding_id}</option>)}</select>
            <Button onClick={addBinding}>新建绑定</Button>
          </TabsContent>
          <TabsContent value="inputs" className="space-y-4">
            {unit.inputs.map((ref) => {
              const input = draft.definitions.inputs[ref.input_ref];
              if (!input) return <p key={ref.input_ref}>输入未定义：{ref.input_ref}</p>;
              const update = (fn: (next: InputDefinition) => void) => edit((next) => fn(next.definitions.inputs[input.input_id]));
              return <div key={input.input_id} className="border rounded p-4 space-y-3">
                <p className="text-xs text-muted-foreground">共享影响：{consumersOf(draft, input.input_id).map((u) => u.title).join("、")}</p>
                <label className="block">显示名称<input className={fieldClass} value={input.label} onChange={(e) => update((next) => { next.label = e.target.value; })} /></label>
                <label className="block">变量名称<input className={fieldClass} value={input.name} onChange={(e) => update((next) => { next.name = e.target.value; })} /></label>
                <label className="block">数据绑定<select className={fieldClass} value={input.binding_ref} onChange={(e) => update((next) => { next.binding_ref = e.target.value; })}>
                  <option value="">选择绑定</option>{unit.bindings.map((b) => <option key={b.binding_ref}>{b.binding_ref}</option>)}</select></label>
                <label className="flex gap-2"><input type="checkbox" checked={input.required ?? false} onChange={(e) => update((next) => { next.required = e.target.checked; })} />正式报告必需</label>
                <SemanticProjectionFields input={input} binding={draft.definitions.bindings[input.binding_ref] ? resolveModelBinding(draft.definitions.bindings[input.binding_ref], draft, ontology) : undefined} classes={ontology} contracts={contracts.data ?? []}
                  onChange={(value) => update((next) => { next.projection = value; })} />
                {plan?.input_types?.[input.input_id] !== undefined && <details><summary>推导类型与最低材料要求</summary>
                  <pre className="text-xs whitespace-pre-wrap">{JSON.stringify({ type: plan.input_types[input.input_id], requirements: plan.requirements?.filter((r) => r.input_id === input.input_id) }, null, 2)}</pre>
                </details>}
                <ContractJson key={input.input_id + JSON.stringify(input.projection)} label="投影与逐字段约束" value={input.projection} onApply={(value) => update((next) => { next.projection = value; })} />
                <ContractJson key={input.input_id + ":constraints"} label="数量、范围及完整性约束" value={input.constraints ?? {}} onApply={(value) => update((next) => { next.constraints = value; })} />
              </div>;
            })}
            <select aria-label="引用共享输入" className={fieldClass} value="" onChange={(e) => {
              const input = draft.definitions.inputs[e.target.value];
              if (input) editUnit((next) => {
                next.inputs.push({ input_ref: input.input_id, alias: input.name });
                if (!next.bindings.some((b) => b.binding_ref === input.binding_ref)) next.bindings.push({ binding_ref: input.binding_ref });
              });
            }}><option value="">引用共享输入</option>{Object.values(draft.definitions.inputs).filter((i) => !unit.inputs.some((u) => u.input_ref === i.input_id))
              .map((i) => <option key={i.input_id} value={i.input_id}>{i.label}</option>)}</select>
            <Button disabled={!unit.bindings.length} onClick={addInput}>新建输入</Button>
          </TabsContent>
          <TabsContent value="render" className="space-y-4">
            <label>呈现类型<select className={fieldClass} value={unit.render.kind} onChange={(e) => editUnit((next) => {
              next.render = initialRender(e.target.value as OutputRender["kind"], next.inputs[0]?.input_ref);
            })}>{(["narrative", "table", "form", "list", "static"] as const).map((kind, i) => <option key={kind} value={kind}>{["段落", "表格", "表单", "列表", "审核固定内容"][i]}</option>)}</select></label>
            {unit.render.kind === "table" && <>
              <label className="block">每行对应输入记录{inputSelect(unit.render.rows?.input_id ?? "", (id) => editUnit((next) => { next.render.rows = { input_id: id }; }))}</label>
              {unit.render.columns?.map((column, i) => <div key={column.column_id} className="flex gap-2">
                <input aria-label="列标题" className={fieldClass} value={column.title} onChange={(e) => editUnit((next) => { next.render.columns![i].title = e.target.value; })} />
                <select aria-label="记录字段" className={fieldClass} value={column.field_ref ?? ""} onChange={(e) => editUnit((next) => { next.render.columns![i].field_ref = e.target.value; })}>
                  <option value={column.field_ref ?? ""}>{column.field_ref || "选择记录字段"}</option>
                  {Object.keys(draft.definitions.inputs[unit.render.rows?.input_id ?? ""]?.projection.fields ?? {}).filter((key) => key !== column.field_ref).map((key) => <option key={key}>{key}</option>)}
                </select>
                <Button variant="ghost" onClick={() => editUnit((next) => { next.render.columns!.splice(i, 1); })}>删除列</Button>
              </div>)}
              <Button variant="outline" onClick={() => editUnit((next) => { next.render.columns!.push({ column_id: crypto.randomUUID(), title: "新列", field_ref: "field" }); })}>＋列</Button>
            </>}
            {(unit.render.kind === "narrative" || unit.render.kind === "static" || unit.render.kind === "list") && <>
              <div className="flex gap-2"><Button variant="outline" onClick={() => addToken({ kind: "text", text: "" })}>＋文本</Button>
                {unit.render.kind !== "static" && inputSelect("", (id) => { if (id) addToken({ kind: "input_ref", input_id: id, scope: unit.render.kind === "list" ? "item" : "input" }); })}</div>
              {unit.render.nodes?.map((node, i) => <div key={i} className="flex gap-2 items-center">
                {node.kind === "text" ? <textarea aria-label="段落文本" className={fieldClass} value={node.text} onChange={(e) => editUnit((next) => { next.render.nodes![i].text = e.target.value; })} />
                  : <span className="rounded bg-accent px-3 py-2 text-sm">{node.kind === "input_ref" ? "〈" + (draft.definitions.inputs[node.input_id!]?.label || node.input_id) + "〉" : node.kind}</span>}
                <Button variant="ghost" onClick={() => editUnit((next) => { next.render.nodes!.splice(i, 1); })}>移除</Button>
              </div>)}
            </>}
            <ContractJson key={selected + ":" + JSON.stringify(unit.render)} label="条件、重复、格式和行文配置" value={unit.render} onApply={(value) => editUnit((next) => { next.render = value; })} />
          </TabsContent>
          </Tabs>
          <details><summary>原文定位与稳定标识</summary><pre className="text-xs whitespace-pre-wrap">{JSON.stringify({ output_id: unit.output_id, origin: unit.origin }, null, 2)}</pre></details>
        </>}</div>;
  const settings = <section className="space-y-3">
    <details className="border rounded p-3" open><summary>数据来源与模型</summary>
      <div className="grid gap-3 mt-3">
        <p className="text-sm text-muted-foreground">关联文档类型决定语义根类型，模型版本在保存新修订时自动固定。</p>
        {model.isPending ? <p>正在解析模型…</p> : model.error ? <p role="alert" className="text-destructive">{model.error.message}</p> : <>
          <p className="text-sm">模型状态：{model.data?.status === "published" ? "已发布" : "已记录结构快照，待本体发布"}</p>
          <details><summary className="text-xs">版本追溯</summary><p className="text-xs break-all">{model.data?.contract_id}</p></details>
        </>}
        <Button variant="outline" size="sm" onClick={() => edit((next) => {
          const previous = next.ontology_release_ref;
          next.ontology_release_ref = "auto:ontology";
          for (const binding of Object.values(next.definitions.bindings)) if (binding.kind === "facts" && typeof binding.contract_ref === "object"
            && [previous, "unresolved:ontology"].includes(String(binding.contract_ref.release_ref))) binding.contract_ref.release_ref = "template.ontology_release_ref";
        })}>在新修订中使用当前模型</Button>
        {draft.source_slots.map((slot) => <div className="rounded border p-3 space-y-2" key={slot.source_slot_id}>
          <p className="text-sm font-medium">{slot.source_slot_id}{slot === primarySource ? " · 主要来源（继承关联文档类型）" : " · 附加来源"}</p>
          {slot === primarySource ? <p className="text-sm">{ontology[slot.class_iri]?.label || slot.class_iri}</p> : <label>来源语义类型<select className={fieldClass} value={slot.class_iri}
            onChange={(event) => { setDraft(changeSourceType(draft, slot.source_slot_id, event.target.value)); setPlan(null); }}>
            <option value={slot.class_iri}>{ontology[slot.class_iri]?.label || slot.class_iri}</option>
            {Object.entries(ontology).map(([iri, cls]) => <option key={iri} value={iri}>{cls.label || iri}</option>)}
          </select></label>}
          <label>来源类别<select className={fieldClass} value={slot.kind} onChange={(event) => edit((next) => {
            next.source_slots.find((s) => s.source_slot_id === slot.source_slot_id)!.kind = event.target.value as "document" | "external";
          })}><option value="document">源文档</option><option value="external">外部数据</option></select></label>
          <label className="text-sm"><input type="checkbox" checked={slot.required !== false} onChange={(event) => edit((next) => {
            next.source_slots.find((s) => s.source_slot_id === slot.source_slot_id)!.required = event.target.checked;
          })} /> 必需来源</label>
        </div>)}
        <Button variant="outline" size="sm" onClick={() => edit((next) => { next.source_slots.push({
          source_slot_id: "source_" + crypto.randomUUID().slice(0, 8), kind: "document", class_iri: primarySource?.class_iri || "unresolved:class", required: true,
        }); })}>添加来源</Button>
        <ContractJson key={JSON.stringify(draft.source_slots)} label="高级来源范围、角色与外部映射" value={draft.source_slots}
          onApply={(value) => edit((next) => { next.source_slots = value; })} />
        <div className="space-y-2 rounded border p-3">
          <p className="font-medium">自动适用的规则</p>
          <p className="text-xs text-muted-foreground">系统根据来源类型建立检查计划。参数缺失、未发布变更和未处理异议会阻止完成报告。</p>
          {resolvedSchema.calculation_checks.map((check) => <p className="text-sm" key={check.check_id}>
            {check.source_slot} · {String(contracts.data?.find((c) => c.contract_id === check.contract_ref)?.definition.title || check.contract_ref)} · 必需
          </p>)}
          <Link className="text-sm text-primary underline" href="/settings/graph-rules">查看规则方法与参数</Link>
        </div>
      </div>
    </details>
    <details className="border rounded p-3"><summary>内容与版式、审核流程</summary><div className="grid gap-3 mt-3">
      <label>版式方案<select className={fieldClass} value={draft.style ? "custom" : draft.style_profile_ref} onChange={(event) => edit((next) => {
        if (event.target.value === "custom") next.style = { font: "Arial", font_size_pt: 11, assets: [] };
        else { next.style = null; next.style_profile_ref = event.target.value; }
      })}><option value={draft.style_profile_ref}>当前方案</option><option value="custom">编辑模板版式</option>
        {contracts.data?.filter((c) => c.kind === "style" && c.status === "published").map((c) => <option key={c.contract_id} value={c.contract_id}>{c.family_id}</option>)}
      </select></label>
      {!!draft.style && <>
        <label>字体<input className={fieldClass} value={String((draft.style as { font: string }).font)} onChange={(event) => edit((next) => { next.style = { ...(next.style as object), font: event.target.value }; })} /></label>
        <label>字号（pt）<input type="number" min={6} max={32} className={fieldClass} value={Number((draft.style as { font_size_pt: number }).font_size_pt)} onChange={(event) => edit((next) => { next.style = { ...(next.style as object), font_size_pt: Number(event.target.value) }; })} /></label>
      </>}
      <label>审核流程<select className={fieldClass} value={draft.publication_policy_ref} onChange={(event) => edit((next) => { next.publication_policy_ref = event.target.value; })}>
        <option value={draft.publication_policy_ref}>{draft.publication_policy_ref.startsWith("unresolved:") ? "继承 QA 审核流程" : "当前流程"}</option>
        {contracts.data?.filter((c) => c.kind === "policy" && c.status === "published").map((c) => <option key={c.contract_id} value={c.contract_id}>{c.family_id}</option>)}
      </select></label>
      <p className="text-xs text-muted-foreground">默认流程要求 QA 审核及材料就绪。实际审核、签署和业务记录需在报告流程中完成。</p>
    </div></details>
    <Button size="sm" disabled={saving || busy} onClick={() => onSave(draft)}>保存为新修订</Button>
  </section>;
  const origin = unit?.origin as TemplateOrigin | undefined;
  const sampleAnchor = origin?.label_anchor ? {
    ...origin.label_anchor,
    document_hash: origin.document_hash,
    parser_version: origin.parser_version,
    structure_hash: origin.structure_hash,
  } : null;

  return <TemplateSlotEditor
    schema={{ template_id: templateId ?? "new", doc_no: draft.doc_no, sections: [] }}
    mode={templateId ? "edit" : "create"}
    templateId={templateId} meta={meta} versions={versions} onVersionSwitch={onVersionSwitch}
    onMetaSaved={onMetaSaved} iriPattern={meta?.iriPattern ?? draft.source_slots[0]?.class_iri}
    sampleContentJson={sampleContentJson} sampleText={sampleText}
    saving={saving} onSave={() => onSave(draft)} onCancel={onCancel}
    outputEditor={{
      documentNo: draft.doc_no,
      onDocumentNoChange: (value) => edit((next) => { next.doc_no = value; }),
      documentClassIri: primarySource?.class_iri,
      onDocumentClassChange: (iri) => {
        if (primarySource) setDraft(changeSourceType(draft, primarySource.source_slot_id, iri));
        else edit((next) => { next.source_slots.push({ source_slot_id: "source", kind: "document", class_iri: iri }); });
        setPlan(null);
      },
      sampleAnchor,
      basicInfo: settings,
      sidebar: <>
        <div className="shrink-0 border-b px-4 py-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">{unitsIn(draft).length} 项内容 · 修订 {draft.revision_no}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={onCancel}>取消</Button>
              <Button size="sm" disabled={saving || busy} onClick={() => onSave(draft)}>{saving ? "保存中…" : "保存新修订"}</Button>
            </div>
          </div>
          <Button variant="outline" size="sm" disabled={!templateId || !schemaHash || busy}
            onClick={() => action(async () => { setPlan(await compileTemplate(templateId!, schemaHash!, draft)); })}>
            {busy ? "校验中…" : "校验语义"}
          </Button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto break-words px-3 py-3 space-y-3">
          {error && <p role="alert" className="text-destructive text-sm whitespace-pre-wrap">{error}</p>}
          {plan && <div className="rounded border p-3 max-h-64 overflow-auto" role="status">
      <strong>{plan.valid ? "语义校验通过" : "请修复以下问题"}</strong>
      {plan.diagnostics.map((d, i) => <p key={i} className="text-sm">{d.code} · {d.schema_path} · {d.message}</p>)}
      {plan.valid && JSON.stringify(draft) === JSON.stringify(schema) && <Button size="sm" className="mt-2"
        onClick={() => action(async () => {
          await reportPost("ast-templates/" + templateId + "/publish", {
            expected_hash: schemaHash, compilation_id: plan.compilation_id,
          }); setError("此修订已发布。"); onMetaSaved?.();
        })}>发布此修订</Button>}
    </div>}
          {!templateId && settings}
          {!!draft.migration_issues?.length && <details className="rounded-lg border border-amber-300 bg-amber-50/50 p-3">
            <summary className="cursor-pointer text-sm font-medium">迁移待确认事项（{draft.migration_issues.length}）</summary>
            <div className="mt-2 space-y-2 text-xs text-muted-foreground">
              {draft.migration_issues.map((issue, i) => <p key={i}>{issue.message}</p>)}
            </div>
          </details>}
          <nav aria-label="报告章节">
            {draft.sections.map((section) => <details open key={section.section_id} className="mb-3 rounded-lg border p-3">
          <summary className="cursor-pointer font-semibold">
          <input aria-label="章节标题" placeholder="未命名章节" className="w-[calc(100%-1.5rem)] bg-transparent font-semibold" value={section.title}
            onChange={(e) => edit((next) => { next.sections.find((s) => s.section_id === section.section_id)!.title = e.target.value; })} />
          </summary>
          {section.groups.map(groupTree)}
          <ContractJson key={section.section_id + JSON.stringify(section.completeness_requirements)}
            label="章节独立完整性要求" value={section.completeness_requirements ?? []}
            onApply={(value) => edit((next) => { next.sections.find((s) => s.section_id === section.section_id)!.completeness_requirements = value; })} />
          <Button variant="ghost" size="sm" onClick={() => edit((next) => {
            next.sections.find((s) => s.section_id === section.section_id)!.groups.push({
              group_id: crypto.randomUUID(), title: "新分组", units: [],
            });
          })}>＋分组</Button>
        </details>)}
        <Button variant="outline" onClick={() => edit((next) => {
          next.sections.push({ section_id: crypto.randomUUID(), title: "新章节", groups: [] });
        })}>＋章节</Button>
          </nav>
        </div>
      </>,
      reportPreview: (sourceJobId) => <TemplateReportPreview templateId={templateId} draftSchema={draft}
        templateName={meta?.name} defaultSourceJobId={sourceJobId ?? defaultSourceJobId} />,
    }}
  />;
}
