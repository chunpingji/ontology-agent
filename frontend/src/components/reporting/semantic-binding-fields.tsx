"use client";

import { Button } from "@/components/ui/button";
import { ontologyMenu, type BindingDefinition, type InputDefinition, type OntologyClassContract, type RegisteredContract, type TemplateV2 } from "@/lib/reporting-v2";

const fieldClass = "w-full rounded border bg-background px-3 py-2 text-sm";
type BindingSourceKind = Exclude<BindingDefinition["kind"], "facts"> | TemplateV2["source_slots"][number]["kind"] | "view";

export function SemanticBindingFields({ binding, schema, contracts, classes, onChange }: {
  binding: BindingDefinition; schema: TemplateV2; contracts: RegisteredContract[];
  classes: Record<string, OntologyClassContract>; onChange: (value: BindingDefinition) => void;
}) {
  const contract = typeof binding.contract_ref === "object" ? binding.contract_ref : {};
  const resultClass = String(contract.result_class_iri ?? "");
  const path = (binding.scope?.predicate_path ?? []) as { predicate_iri: string; direction?: string }[];
  const menu = ontologyMenu(classes, resultClass);
  const selectedContract = contracts.find((c) => c.contract_id === binding.contract_ref);
  const operation = (binding.operation ?? {}) as Record<string, unknown>;
  const parameters = (binding.parameters ?? {}) as Record<string, { input_id: string }>;
  const sourceSlot = schema.source_slots.find((slot) => slot.source_slot_id === binding.scope?.source_slot);
  // Both source categories use published facts; their saved source slot determines the UI choice.
  const sourceKind = binding.kind === "facts" ? sourceSlot?.kind ?? "" : binding.kind === "derived" && binding.provider === "view" ? "view" : binding.kind;
  const documentSlots = schema.source_slots.filter((slot) => slot.kind === "document");
  const externalSlots = schema.source_slots.filter((slot) => slot.kind === "external");
  const sourceSlots = sourceKind === "document" ? documentSlots : sourceKind === "external" ? externalSlots : [];
  const sourceLabel = sourceKind === "external" ? "外部数据来源" : "来源文档";
  const unconfiguredSources = [
    !documentSlots.length && "源文档关系图谱",
    !externalSlots.length && "外部数据或 Mock",
  ].filter(Boolean);
  function selectContract(ref: string) {
    const selected = contracts.find((c) => c.contract_id === ref);
    if (binding.kind !== "derived") { onChange({ ...binding, contract_ref: ref }); return; }
    if (selected?.kind === "view") {
      const operations = selected.definition.allowed_operations as string[];
      onChange({ binding_id: binding.binding_id, kind: "derived", contract_ref: ref,
        provider: "view", operation: { kind: operations[0], source: { input_id: "" } } });
    } else if (selected?.kind === "calculation") onChange({ binding_id: binding.binding_id, kind: "derived", contract_ref: ref,
      provider: "calculation", check_ref: schema.calculation_checks?.find((c) => c.contract_ref === ref)?.check_id ?? "" });
    else onChange({ binding_id: binding.binding_id, kind: "derived", contract_ref: ref,
      provider: "rule_result", parameters: {} });
  }
  function kind(value: BindingSourceKind) {
    if (value === "document" || value === "external") {
      const slot = schema.source_slots.find((source) => source.kind === value);
      if (!slot) return;
      onChange({ binding_id: binding.binding_id, label: binding.label, kind: "facts", contract_ref: { kind: "ontology",
        release_ref: "template.ontology_release_ref", root_class_iri: slot.class_iri,
        result_class_iri: slot.class_iri },
        scope: { source_slot: slot.source_slot_id, predicate_path: [] } });
    } else if (value === "view") onChange({ binding_id: binding.binding_id, label: binding.label, kind: "derived", provider: "view", contract_ref: "auto:view",
      operation: { kind: "project", source: { input_id: Object.values(schema.definitions.inputs).find((i) => i.binding_ref !== binding.binding_id)?.input_id ?? "" }, fields: {} } });
    else onChange({ binding_id: binding.binding_id, label: binding.label, kind: value, contract_ref: "unresolved:contract",
      ...(value === "derived" ? { provider: "rule_result", parameters: {} } : { scope: { record_slot: "record" } }) });
  }
  return <div className="space-y-3">
    <label className="block">来源方式<select aria-label="来源方式" className={fieldClass} value={sourceKind} onChange={(e) => kind(e.target.value as BindingSourceKind)}>
      {!sourceKind && <option value="" disabled>请选择来源方式</option>}
      <option value="document" disabled={!documentSlots.length}>源文档关系图谱{!documentSlots.length && "（未配置来源）"}</option>
      <option value="external" disabled={!externalSlots.length}>外部数据或 Mock{!externalSlots.length && "（未配置来源）"}</option>
      <option value="workflow">审核与流程记录</option>
      <option value="context">运行参数</option><option value="derived">规则与计算结果</option><option value="view">数据变换（自动推导类型）</option>
    </select></label>
    {!!unconfiguredSources.length && <p className="text-sm text-muted-foreground">
      尚未配置{unconfiguredSources.join("、")}。请在「数据来源与模型」中添加来源。
    </p>}
    {binding.kind === "facts" ? <>
      <p className="text-sm text-muted-foreground">{sourceKind === "external"
        ? "从外部系统或 Mock 数据中选择对象及属性，数据须先完成审核和发布。"
        : sourceKind === "document"
          ? "沿源文档关系图谱选择对象及属性，数据须先完成审核和发布。"
          : "当前绑定的来源尚未配置，请先配置或重新选择来源。"}</p>
      <label className="block">{sourceLabel}<select aria-label={sourceLabel} className={fieldClass} value={sourceSlot?.source_slot_id ?? ""} disabled={!sourceSlots.length} onChange={(e) => {
        const slot = sourceSlots.find((s) => s.source_slot_id === e.target.value);
        if (slot) onChange({ ...binding, scope: { ...binding.scope, source_slot: slot.source_slot_id, predicate_path: [] },
          contract_ref: { ...contract, root_class_iri: slot.class_iri, result_class_iri: slot.class_iri } });
      }}><option value="" disabled>选择{sourceLabel}</option>{sourceSlots.map((slot) => <option key={slot.source_slot_id} value={slot.source_slot_id}>
        {slot.source_slot_id} · {classes[slot.class_iri]?.label || slot.class_iri}</option>)}</select></label>
      <p className="text-sm">当前对象：{classes[resultClass]?.label || resultClass}</p>
      {!!path.length && <p className="text-xs break-all">关系路径：{path.map((p) => p.predicate_iri).join(" → ")}</p>}
      <select aria-label="追加精确关系" className={fieldClass} value="" onChange={(e) => {
        if (!e.target.value) return;
        const [predicate, range] = JSON.parse(e.target.value) as [string, string];
        onChange({ ...binding, scope: { ...binding.scope, predicate_path: [...path, { predicate_iri: predicate, direction: "forward" }] },
          contract_ref: { ...contract, result_class_iri: range } });
      }}><option value="">沿关系选择对象</option>{menu.relationships?.flatMap((relation) => relation.range.map((range) =>
        <option key={relation.iri + range} value={JSON.stringify([relation.iri, range])}>{relation.label || relation.iri} → {classes[range]?.label || range}</option>))}</select>
      <label className="block">适用时间<input className={fieldClass} value={String(binding.scope?.applicable_at ?? "")} onChange={(e) =>
        onChange({ ...binding, scope: { ...binding.scope, applicable_at: e.target.value || null } })} placeholder="使用报告运行的适用时间" /></label>
    </> : sourceKind === "view" ? <p className="text-sm text-muted-foreground">执行计划由选定的取数操作生成；范围边界等业务含义仍需明确。</p> : <label className="block">规则或业务数据定义<select aria-label="规则或业务数据定义" className={fieldClass} value={String(binding.contract_ref)} onChange={(e) => selectContract(e.target.value)}>
      <option value={String(binding.contract_ref)}>{String(binding.contract_ref)}</option>
      {contracts.filter((c) => c.status === "published" && (binding.kind === "derived" ? ["rule", "view", "calculation"].includes(c.kind) : binding.kind === "context" ? ["context", "parameter"].includes(c.kind) : c.kind === "workflow"))
        .map((c) => <option key={c.contract_id} value={c.contract_id}>{c.family_id} · 修订 {c.revision_no}</option>)}
    </select></label>}
    {binding.kind === "derived" && selectedContract?.kind === "calculation" && <label className="block">计算校验来源
      <select className={fieldClass} value={String(binding.check_ref ?? "")} onChange={(e) => onChange({ ...binding, check_ref: e.target.value })}>
        <option value="">选择自动适用的计算来源</option>{schema.calculation_checks?.filter((c) => c.contract_ref === binding.contract_ref)
          .map((c) => <option key={c.check_id} value={c.check_id}>{c.source_slot} · PDE 计算校验</option>)}
      </select></label>}
    {binding.kind === "derived" && selectedContract?.kind === "rule" && Object.keys(selectedContract.definition.parameters ?? {}).map((name) =>
      <label className="block" key={name}>规则参数：{name}<select className={fieldClass} value={parameters[name]?.input_id ?? ""}
        onChange={(e) => onChange({ ...binding, parameters: { ...parameters, [name]: { kind: "input_ref", input_id: e.target.value } } })}>
        <option value="">选择输入</option>{Object.values(schema.definitions.inputs).filter((i) => i.binding_ref !== binding.binding_id).map((i) =>
          <option key={i.input_id} value={i.input_id}>{i.label || i.name}</option>)}
      </select></label>)}
    {binding.kind === "derived" && binding.provider === "view" && <>
      <label className="block">视图操作<select aria-label="视图操作" className={fieldClass} value={String(operation.kind ?? "")}
        onChange={(e) => onChange({ ...binding, operation: { ...operation, kind: e.target.value } })}>
        {((selectedContract?.definition.allowed_operations ?? ["project", "filter", "sort", "group", "join", "range", "unit_convert"]) as string[]).map((kind) => <option key={kind}>{kind}</option>)}
      </select></label>
      <label className="block">视图来源输入<select aria-label="视图来源输入" className={fieldClass} value={String((operation.source as { input_id?: string })?.input_id ?? "")}
        onChange={(e) => onChange({ ...binding, operation: { ...operation, source: { kind: "input_ref", input_id: e.target.value } } })}>
        <option value="">选择输入</option>{Object.values(schema.definitions.inputs).filter((i) => i.binding_ref !== binding.binding_id).map((i) =>
          <option key={i.input_id} value={i.input_id}>{i.label || i.name}</option>)}
      </select></label>
      {operation.kind === "range" && <>
        {(["lower_field", "upper_field"] as const).map((key, index) => <label className="block" key={key}>{index === 0 ? "下界字段" : "上界字段"}
          <select className={fieldClass} value={String(operation[key] ?? "")} onChange={(event) => onChange({ ...binding, operation: { ...operation, [key]: event.target.value } })}>
            <option value="">请选择字段</option>{Object.keys(schema.definitions.inputs[String((operation.source as { input_id?: string })?.input_id)]?.projection.fields ?? {}).map((field) => <option key={field}>{field}</option>)}
          </select></label>)}
        {(["lower_inclusive", "upper_inclusive"] as const).map((key, index) => <label className="block" key={key}>{index === 0 ? "下界是否包含" : "上界是否包含"}
          <select className={fieldClass} value={operation[key] == null ? "" : String(operation[key])} onChange={(event) => onChange({ ...binding, operation: { ...operation, [key]: event.target.value === "" ? null : event.target.value === "true" } })}>
            <option value="">待业务确认</option><option value="true">包含</option><option value="false">不包含</option>
          </select></label>)}
      </>}
    </>}
  </div>;
}

export function SemanticProjectionFields({ input, binding, classes, contracts, onChange }: {
  input: InputDefinition; binding?: BindingDefinition; classes: Record<string, OntologyClassContract>;
  contracts: RegisteredContract[];
  onChange: (value: InputDefinition["projection"]) => void;
}) {
  if (binding?.kind !== "facts" || typeof binding.contract_ref !== "object") return null;
  const classIri = String(binding.contract_ref.result_class_iri ?? "");
  const menu = ontologyMenu(classes, classIri);
  const projection = input.projection;
  const records = ["record", "records"].includes(projection.kind);
  return <div className="space-y-2">
    <label className="block">输入形态<select aria-label="输入形态" className={fieldClass} value={projection.kind} onChange={(e) => {
      const kind = e.target.value;
      onChange(kind === "property" ? { kind, property_iri: menu.properties?.[0]?.iri ?? "" }
        : kind === "entity" || kind === "entities" ? { kind, class_iri: classIri }
          : { kind, fields: {} });
    }}><option value="property">单个属性</option><option value="record">单个对象的字段</option>
      <option value="records">对象记录列表</option><option value="entity">单个对象引用</option><option value="entities">对象引用列表</option></select></label>
    {projection.kind === "property" && <label className="block">本体属性<select aria-label="本体属性" className={fieldClass} value={projection.property_iri ?? ""} onChange={(e) => onChange({ ...projection, property_iri: e.target.value })}>
      <option value="">选择属性</option>{menu.properties?.map((p) => <option key={p.iri} value={p.iri}>{p.label || p.iri} · {p.canonical_unit || p.datatype}</option>)}
    </select></label>}
    {projection.kind === "property" && <label className="block">额外的业务类型约束<select aria-label="额外的业务类型约束" className={fieldClass} value={String(projection.type_contract_ref ?? "")}
      onChange={(e) => onChange({ ...projection, type_contract_ref: e.target.value || null })}>
      <option value="">继承本体类型与单位</option>{contracts.filter((c) => c.kind === "property_type" && c.status === "published"
        && c.definition.property_iri === projection.property_iri && c.definition.subject_class_iri === classIri
        && c.definition.ontology_release_ref === (binding.contract_ref as Record<string, unknown>).release_ref)
        .map((c) => <option key={c.contract_id} value={c.contract_id}>{c.family_id} · 修订 {c.revision_no}</option>)}
    </select></label>}
    {records && <>
      {Object.entries(projection.fields ?? {}).map(([id, field]) => <div key={id} className="flex items-center gap-2">
        <span className="text-sm break-all flex-1">{id} · {field.value.property_iri}</span>
        <label><input type="checkbox" checked={field.required ?? false} onChange={(e) => onChange({ ...projection,
          fields: { ...projection.fields, [id]: { ...field, required: e.target.checked } } })} />必需</label>
      </div>)}
      <select aria-label="添加本体字段" className={fieldClass} value="" onChange={(e) => {
        if (!e.target.value) return;
        const id = "field_" + crypto.randomUUID();
        onChange({ ...projection, fields: { ...projection.fields, [id]: { value: { kind: "property", property_iri: e.target.value } } } });
      }}><option value="">添加字段</option>{menu.properties?.map((p) => <option key={p.iri} value={p.iri}>{p.label || p.iri}</option>)}</select>
    </>}
    {["entity", "entities"].includes(projection.kind) && <label className="block">名称显示属性<select aria-label="名称显示属性" className={fieldClass} value={String(projection.display_property_iri ?? "")} onChange={(e) => onChange({ ...projection, display_property_iri: e.target.value || null })}>
      <option value="">请选择显示属性</option>{menu.properties?.filter((p) => p.datatype?.endsWith("string")).map((p) => <option key={p.iri} value={p.iri}>{p.label || p.iri}</option>)}
    </select></label>}
    <Button size="sm" variant="ghost" onClick={() => onChange({ kind: "identity" })}>使用绑定的完整类型</Button>
  </div>;
}
