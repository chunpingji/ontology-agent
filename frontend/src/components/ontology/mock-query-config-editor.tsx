"use client";

import { useState } from "react";
import { updateMapping, type EntitySourceMapping, type MappedEntitySource,
  type MockQueryConfig, type TBoxMapping } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "./field";

export function IdentityGuidance({ mapping }: { mapping: EntitySourceMapping }) {
  return <div className="space-y-1 text-xs text-muted-foreground">
    <p>本体标识属性：{mapping.identity_properties.map(p => p.label).join("、") || "未声明"}</p>
    <p>本体完整键组（owl:hasKey）：{mapping.identity_key_groups.length ? "" : "未声明"}</p>
    {mapping.identity_key_groups.map((g, i) => <p className="break-all" key={i}>
      {g.property_iris.join(" + ")}{!g.available && `（不可用组件：${g.unavailable_property_iris.join("、")}）`}
    </p>)}
    <p>来源查询键仅用于查找，不代表唯一性或文档身份已核对。</p>
    {mapping.issues.map((i, n) => <p className="text-warning" key={n}>{i.message} {i.property_iri}</p>)}
  </div>;
}

export function MockQueryConfigEditor({ mapping, source, onChanged }: {
  mapping: TBoxMapping; source?: MappedEntitySource; onChanged: () => void;
}) {
  const [config, setConfig] = useState<MockQueryConfig>(mapping.query_config ?? {
    label_path: null, entity_iri_path: null, class_path: null,
    identifier_namespace: null, lookup_key_groups: [],
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const info = source?.mappings.find(m => m.id === mapping.id);
  const save = async () => {
    setSaving(true); setError("");
    try {
      await updateMapping(mapping.id, { mapping_type: mapping.mapping_type, target: mapping.target,
        source_system: mapping.source_system, query_config: config, expected_version: mapping.version });
      onChanged();
    } catch (e) { setError(String(e)); } finally { setSaving(false); }
  };
  const pathFields = [
    ["label_path", "显示名称字段"], ["entity_iri_path", "来源实体 IRI 字段（可选）"],
    ["class_path", "记录本体类型字段（可选）"],
  ] as const;
  return <div className="mt-2 space-y-3 rounded border p-3">
    <p className="text-sm font-medium">Mock 查询配置 {info?.queryable ? "· 可查询" : "· 待完善"}</p>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <datalist id={`query-fields-${mapping.id}`}>
      {source?.fields.map(f => <option key={f.source_path} value={f.source_path}>{f.label}</option>)}
    </datalist>
    {pathFields.map(([key, label]) => <Field key={key} label={label}>
      <Input aria-label={label} list={`query-fields-${mapping.id}`} value={config[key] ?? ""}
        onChange={e => setConfig({ ...config, [key]: e.target.value || null })} />
    </Field>)}
    <Field label="来源编号命名空间" hint="与组织、场地等业务范围分开声明">
      <Input aria-label="来源编号命名空间" value={config.identifier_namespace ?? ""}
        onChange={e => setConfig({ ...config, identifier_namespace: e.target.value || null })} />
    </Field>
    <p className="text-xs font-medium">来源查询键组（组内全部满足；各组独立）</p>
    {config.lookup_key_groups.map((group, index) => <div key={index} className="space-y-2 rounded border p-2">
      <div className="flex justify-between text-xs"><span>键组 {index + 1}</span>
        <Button size="sm" variant="ghost" onClick={() => setConfig({ ...config,
          lookup_key_groups: config.lookup_key_groups.filter((_, i) => i !== index) })}>移除键组</Button>
      </div>
      {(["property_iris", "scope_property_iris"] as const).map(kind => <fieldset key={kind}>
        <legend className="text-xs text-muted-foreground">{kind === "property_iris" ? "编号组件" : "业务范围组件"}</legend>
        <div className="flex flex-wrap gap-2">
          {info?.properties.map(p => <label className="flex gap-1 text-xs" key={p.property_iri} title={p.property_iri}>
            <input type="checkbox" checked={group[kind].includes(p.property_iri)} onChange={e => {
              const groups = config.lookup_key_groups.map((g, i) => i !== index ? g : { ...g,
                [kind]: e.target.checked ? [...g[kind], p.property_iri] : g[kind].filter(v => v !== p.property_iri) });
              setConfig({ ...config, lookup_key_groups: groups });
            }} />{p.label}
          </label>)}
        </div>
      </fieldset>)}
      {[...group.property_iris, ...group.scope_property_iris].filter(p => !info?.properties.some(
        option => option.property_iri === p,
      )).map(p => <p key={p} className="break-all text-xs text-warning">不可用组件：{p}（移除该组后重新配置）</p>)}
    </div>)}
    <div className="flex gap-2">
      <Button size="sm" variant="outline" onClick={() => setConfig({ ...config,
        lookup_key_groups: [...config.lookup_key_groups, { property_iris: [], scope_property_iris: [] }] })}>添加键组</Button>
      <Button size="sm" onClick={save} disabled={saving}>{saving ? "保存中…" : "保存查询配置"}</Button>
    </div>
    {info && <IdentityGuidance mapping={info} />}
  </div>;
}
