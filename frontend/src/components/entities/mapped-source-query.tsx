"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Database, Loader2, Search } from "lucide-react";
import { getEntitySources, queryMappedEntities,
  type EntitySources, type EntitySourceMapping, type MappedEntitySource,
  type MappedEntityCandidate, type MappedEntityQuery, type MappedEntityQueryResult } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetTrigger, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field } from "@/components/ontology/field";
import { IdentityGuidance } from "@/components/ontology/mock-query-config-editor";
import { SavedEntitySourceCard } from "@/components/entities/saved-entity-source";

const XSD = "http://www.w3.org/2001/XMLSchema#";
const short = (iri: string) => iri.split(/[\/#]/).pop() || iri;
type Filter = { property_iri: string; value: string; datatype_iri: string };

function typedValue(filter: Filter): string | number | boolean {
  const type = filter.datatype_iri.slice(XSD.length);
  if (type === "boolean") {
    if (filter.value !== "true" && filter.value !== "false") throw new Error("布尔值须为 true 或 false");
    return filter.value === "true";
  }
  if (type === "integer" || type === "decimal") {
    if (!filter.value.trim()) throw new Error("请输入数值");
    const value = Number(filter.value);
    if (!Number.isFinite(value) || (type === "integer" && !Number.isSafeInteger(value))) {
      throw new Error("请输入该数据类型支持的数值");
    }
    return value;
  }
  return filter.value;
}

function CandidateDetail({ candidate }: { candidate: MappedEntityCandidate }) {
  return <Card className="min-w-0 space-y-3 p-4" data-testid="mapped-entity-detail">
    <h3 className="font-semibold">{candidate.label || "未提供名称"}</h3>
    <p className="text-sm text-muted-foreground">来源候选 · 身份未核对</p>
    <dl className="space-y-2 break-all text-xs">
      <div><dt className="text-muted-foreground">本体类型</dt><dd>{candidate.class_iri}</dd></div>
      <div><dt className="text-muted-foreground">来源记录引用</dt><dd>{candidate.record_ref.source_system} / {candidate.record_ref.dataset} / {candidate.record_ref.record_id}</dd></div>
      <div><dt className="text-muted-foreground">来源声明的实体 IRI</dt><dd>{candidate.source_entity_iri || "未提供"}</dd></div>
      <div><dt className="text-muted-foreground">数据更新时间</dt><dd>{candidate.record_version}</dd></div>
      <div><dt className="text-muted-foreground">编号命名空间</dt><dd>{candidate.identifier_namespace || "未声明"}</dd></div>
      <div><dt className="text-muted-foreground">完整来源键匹配</dt><dd>{candidate.matched_lookup_groups.length ? candidate.matched_lookup_groups.map(i => `第 ${i + 1} 组`).join("、") : "未满足"}</dd></div>
      <div><dt className="text-muted-foreground">业务范围</dt><dd>{candidate.business_scope_status === "provided" ? "查询已提供范围组件（文档归属仍待核对）" : "未明确"}</dd></div>
    </dl>
    {candidate.properties.map(p => <div key={p.property_iri} className="space-y-1 text-sm">
      <p className="break-all font-medium" title={p.property_iri}>{short(p.property_iri)}</p>
      {p.values.length === 0 && <p className="text-xs text-muted-foreground">来源未提供值</p>}
      {p.values.map((v, i) => <div key={i} className="break-all rounded bg-muted p-2">
        <p>{String(v.value)}</p>
        <p className="text-xs text-muted-foreground">原值：{String(v.raw_value)} · {v.source_path}{v.source_index !== null ? ` [${v.source_index}]` : ""} · {short(v.datatype_iri)}</p>
      </div>)}
    </div>)}
    {candidate.issues.map((issue, i) => <p key={i} className="text-xs text-warning">{issue.message}</p>)}
    <details className="break-all text-xs text-muted-foreground"><summary>匹配与配置依据</summary>
      <p>映射：{candidate.mapping_id}</p><p>映射版本：{candidate.mapping_revision}</p>
      {candidate.matches.map((m, i) => <p key={i}>{m.kind} {m.property_iri}</p>)}
    </details>
  </Card>;
}

const PAGE_SIZE = 50;

function initialQuery(mapping: EntitySourceMapping): MappedEntityQuery {
  return {
    query_id: "source-browser", class_iri: mapping.class_iri, mapping_ids: [mapping.id],
    include_subclasses: true, property_filters: [], limit: PAGE_SIZE, offset: 0,
  };
}

function SourceEntities({ mapping }: { mapping: EntitySourceMapping }) {
  const [name, setName] = useState("");
  const [nameMatch, setNameMatch] = useState<"exact" | "contains">("exact");
  const [filters, setFilters] = useState<Filter[]>([]);
  const [request, setRequest] = useState(() => initialQuery(mapping));
  const [result, setResult] = useState<MappedEntityQueryResult | null>(null);
  const [selected, setSelected] = useState<MappedEntityCandidate | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const active = useRef<AbortController | null>(null);
  const propertyOptions = mapping.properties;

  useEffect(() => {
    const abort = new AbortController();
    active.current = abort;
    queryMappedEntities([request], abort.signal)
      .then(response => { if (!abort.signal.aborted) setResult(response.results[0]); })
      .catch(e => { if (!abort.signal.aborted) setError(String(e)); })
      .finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [request]);

  const load = (query: MappedEntityQuery) => {
    active.current?.abort();
    setBusy(true); setError(""); setSelected(null); setResult(null); setRequest(query);
  };
  const run = () => {
    try {
      const property_filters = filters.map(f => {
        if (!f.property_iri || !f.value.trim()) throw new Error("请选择属性并填写属性值");
        return { ...f, value: typedValue(f) };
      });
      load({ ...initialQuery(mapping), name: name ? { value: name, match: nameMatch } : null,
        property_filters });
    } catch (e) { setError(String(e)); }
  };
  const changeFilter = (index: number, update: Partial<Filter>) => setFilters(
    filters.map((f, i) => i === index ? { ...f, ...update } : f),
  );
  const offset = request.offset ?? 0;

  return <div className="space-y-5">
    <form className="space-y-3 rounded-lg border bg-muted/20 p-4" onSubmit={e => { e.preventDefault(); run(); }}>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 basis-48">
          <Field label="实体名称"><Input aria-label="名称" placeholder="输入名称筛选此来源" value={name}
            onChange={e => setName(e.target.value)} /></Field>
        </div>
        <div className="w-36"><Field label="名称匹配">
          <Select value={nameMatch} onValueChange={v => setNameMatch(v as "exact" | "contains")}>
            <SelectTrigger aria-label="名称匹配"><SelectValue /></SelectTrigger><SelectContent>
              <SelectItem value="exact">完全相同</SelectItem><SelectItem value="contains">包含</SelectItem>
            </SelectContent>
          </Select>
        </Field></div>
        <Button type="submit" disabled={busy}><Search className="mr-1.5 h-4 w-4" />筛选</Button>
        <Button type="button" variant="outline" disabled={busy} onClick={() => {
          setName(""); setNameMatch("exact"); setFilters([]); load(initialQuery(mapping));
        }}>重置</Button>
      </div>
      <details className="space-y-3 text-sm">
        <summary className="cursor-pointer text-muted-foreground">属性精确筛选{filters.length > 0 ? `（${filters.length}）` : ""}</summary>
        {filters.map((filter, index) => <div key={index} className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr_auto]">
          <Select value={filter.property_iri} onValueChange={v => changeFilter(index, { property_iri: v,
            datatype_iri: propertyOptions.find(p => p.property_iri === v)?.datatype_iris[0] || XSD + "string" })}>
            <SelectTrigger aria-label={`属性 ${index + 1}`}><SelectValue placeholder="选择本体属性" /></SelectTrigger>
            <SelectContent>{propertyOptions.map(p => <SelectItem key={p.property_iri} value={p.property_iri}>{p.label}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={filter.datatype_iri} onValueChange={v => changeFilter(index, { datatype_iri: v })}>
            <SelectTrigger aria-label={`数据类型 ${index + 1}`}><SelectValue /></SelectTrigger><SelectContent>
              {(propertyOptions.find(p => p.property_iri === filter.property_iri)?.datatype_iris ?? [XSD + "string"])
                .map(dt => <SelectItem key={dt} value={dt}>{short(dt)}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input aria-label={`属性值 ${index + 1}`} value={filter.value} onChange={e => changeFilter(index, { value: e.target.value })} />
          <Button type="button" variant="ghost" onClick={() => setFilters(filters.filter((_, i) => i !== index))}>移除</Button>
        </div>)}
        <Button type="button" size="sm" variant="outline" disabled={!propertyOptions.length || filters.length >= 32}
          onClick={() => setFilters([...filters, { property_iri: "", value: "", datatype_iri: XSD + "string" }])}>添加属性条件</Button>
        <p className="text-xs text-muted-foreground">所有条件同时满足，编号按属性值精确匹配。</p>
      </details>
    </form>
    {error && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive">
      <p className="min-w-0 break-all">{error}</p>
      <Button size="sm" variant="outline" onClick={() => load({ ...request })}>重试上次请求</Button>
    </div>}
    {busy && <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />正在读取来源实体…
    </p>}
    {result && <div className="space-y-3" data-testid="mapped-query-result">
      <div aria-live="polite" className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{result.total !== null ? `共 ${result.total} 条实体` : `本页 ${result.candidates.length} 条实体`}</h3>
        <span className="text-xs text-muted-foreground">第 {Math.floor(offset / PAGE_SIZE) + 1} 页 · 每页 {PAGE_SIZE} 条</span>
      </div>
      {result.total === null && <p className="text-sm text-warning">来源读取不完整，无法确认实体总数。</p>}
      {result.issues.filter(i => i.code !== "result_limit").map((i, n) => <p key={n} className="text-sm text-warning">{i.message}</p>)}
      {result.sources.some(s => s.issues.length > 0) && <details open={result.total === null} className="text-xs">
        <summary className="cursor-pointer">来源读取提示</summary>
        {result.sources.flatMap(s => s.issues).map((i, n) => <p className="mt-1 break-all text-warning" key={n}>
          {i.code}：{i.message} {i.property_iri} {i.record_id}
        </p>)}
      </details>}
      {result.candidates.length === 0 ? <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        {result.outcome === "no_match" ? "当前来源没有符合条件的实体" : result.outcome === "unresolved" ? "查询未完成，不能判断是否存在实体" : "当前页没有实体，请重置后重新读取"}
      </p> : <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="max-h-[52vh] space-y-2 overflow-y-auto pr-1" data-testid="mapped-entity-list">
          {result.candidates.map(c => <button type="button" key={`${c.mapping_id}-${c.record_ref.record_id}`}
            aria-pressed={selected?.record_ref.record_id === c.record_ref.record_id}
            className={cn("w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected?.record_ref.record_id === c.record_ref.record_id && "border-primary bg-primary/5")}
            onClick={() => setSelected(c)}>
            <span className="block break-words text-sm font-medium">{c.label || "未提供名称"}</span>
            <span className="mt-1 block break-all text-xs text-muted-foreground">{short(c.class_iri)} · {c.record_ref.record_id}</span>
            {c.properties.filter(p => mapping.identity_properties.some(i => i.property_iri === p.property_iri))
              .map(p => <span key={p.property_iri} className="mt-1 block break-all text-xs text-muted-foreground">
                {mapping.properties.find(i => i.property_iri === p.property_iri)?.label || short(p.property_iri)}：{p.values.map(v => String(v.value)).join("、") || "未提供"}
              </span>)}
            {c.issues.some(i => i.code === "source_iri_conflict") && <span className="text-xs text-warning">来源 IRI 存在冲突</span>}
          </button>)}
        </div>
        <div className="min-w-0 max-h-[52vh] overflow-y-auto">
          {selected ? <CandidateDetail candidate={selected} /> : <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">选择实体查看属性与来源详情</div>}
        </div>
      </div>}
      <div className="flex items-center justify-between gap-2 border-t pt-3">
        <Button size="sm" variant="outline" disabled={busy || offset === 0}
          onClick={() => load({ ...request, offset: Math.max(0, offset - PAGE_SIZE) })}>上一页</Button>
        <span className="text-xs text-muted-foreground">{result.candidates.length > 0 ? `本页显示 ${offset + 1}–${offset + result.candidates.length} 条` : "本页无记录"}</span>
        <Button size="sm" variant="outline" disabled={busy || result.next_offset === null}
          onClick={() => load({ ...request, offset: result.next_offset ?? 0 })}>下一页</Button>
      </div>
    </div>}
    <details className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer text-muted-foreground">映射与身份指引</summary>
      <div className="mt-3 space-y-2"><p className="break-all text-xs text-muted-foreground">{mapping.class_iri}</p>
        <IdentityGuidance mapping={mapping} />
      </div>
    </details>
  </div>;
}

function SourceDrawer({ source, open }: { source: MappedEntitySource; open: boolean }) {
  const [mappingId, setMappingId] = useState(() => (source.mappings.find(m => m.queryable) || source.mappings[0]).id);
  const mapping = source.mappings.find(m => m.id === mappingId)!;
  return <>
    <SheetHeader className="shrink-0 border-b px-6 py-5 pr-12">
      <SheetTitle>{source.label}</SheetTitle>
      <SheetDescription>浏览此来源映射的实体，选择实体查看详情。来源记录的身份与文档关系需另行核对。</SheetDescription>
    </SheetHeader>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted-foreground">本体类型</span>
        {source.mappings.length > 1 ? <Select value={mappingId} onValueChange={setMappingId}>
          <SelectTrigger aria-label="来源映射类型" className="w-full sm:w-80"><SelectValue /></SelectTrigger>
          <SelectContent>{source.mappings.map(m => <SelectItem key={m.id} value={m.id}>
            {m.class_label}{!m.queryable ? "（待完善）" : ""}
          </SelectItem>)}</SelectContent>
        </Select> : <Badge variant="secondary">{mapping.class_label}</Badge>}
        <span className="text-xs text-muted-foreground">包含子类</span>
      </div>
      {source.issues.map((i, n) => <p key={n} className="text-sm text-warning">{i.message}</p>)}
      {mapping.queryable ? (open && <SourceEntities key={mapping.id} mapping={mapping} />) : <Card className="space-y-2 p-5">
        <p className="font-medium">此映射暂不可查询</p>
        <p className="text-sm text-muted-foreground">请在本体管理中检查该类型的来源映射配置。</p>
        {mapping.issues.map((i, n) => <p key={n} className="break-all text-sm text-warning">{i.message}</p>)}
        <IdentityGuidance mapping={mapping} />
      </Card>}
    </div>
  </>;
}

function MappedSourceCard({ source: s }: { source: MappedEntitySource }) {
  const [open, setOpen] = useState(false);
  const available = s.mappings.filter(m => m.queryable).length;
  return <Sheet open={open} onOpenChange={setOpen}>
    <Card className="overflow-hidden transition-colors hover:border-primary/50 hover:shadow-md">
      <SheetTrigger asChild>
        <button type="button" aria-label={`查看${s.label}的实体`}
          data-testid={`mapped-source-${s.dataset}`}
          className="flex h-full w-full flex-col gap-4 p-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <span className="flex w-full items-center justify-between gap-3">
            <span className="rounded-lg bg-primary/10 p-2.5 text-primary"><Database className="h-5 w-5" /></span>
            <span className={cn("text-xs", available === s.mappings.length ? "text-success" : "text-warning")}>
              {available === s.mappings.length ? "可查询" : available ? "部分可查询" : "待完善"}
            </span>
          </span>
          <span className="space-y-1"><span className="block text-base font-semibold">{s.label}</span>
            <span className="block break-all text-xs text-muted-foreground">{s.source_system} / {s.dataset}</span>
          </span>
          <span className="flex flex-wrap gap-1.5">{s.mappings.map(m => <span key={m.id}
            className="rounded-md bg-secondary px-2 py-1 text-xs text-secondary-foreground">{m.class_label}</span>)}</span>
          <span className="mt-auto flex w-full items-center justify-between border-t pt-3 text-xs text-muted-foreground">
            <span>{s.mappings.length} 个类型映射</span><span className="inline-flex items-center gap-1 text-primary">查看实体<ArrowUpRight className="h-3.5 w-3.5" /></span>
          </span>
        </button>
      </SheetTrigger>
    </Card>
    <SheetContent className="gap-0 p-0 sm:max-w-4xl xl:max-w-5xl">
      <SourceDrawer source={s} open={open} />
    </SheetContent>
  </Sheet>;
}

export function MappedSourceQuery() {
  const [catalog, setCatalog] = useState<EntitySources | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    getEntitySources(abort.signal)
      .then(sources => { if (!abort.signal.aborted) setCatalog(sources); })
      .catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [reload]);
  const sources = catalog?.sources.filter(s => s.mappings.length > 0) ?? [];

  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="space-y-1">
        <h2 className="font-semibold">已映射来源</h2>
        <p className="text-sm text-muted-foreground">选择本地实体库或已映射的外部来源，查看实体及其属性。</p>
      </div>
      {catalog && <Badge variant="outline">{sources.length + 1} 个来源</Badge>}
    </div>
    {error ? <div role="alert" className="space-y-2 text-sm text-destructive">
      <p className="break-all">{error}</p><Button variant="outline" onClick={() => { setError(""); setReload(n => n + 1); }}>重新加载</Button>
    </div> : !catalog ? <p role="status" className="text-sm text-muted-foreground">正在加载外部来源…</p> : sources.length === 0 &&
      <p className="text-sm text-muted-foreground">暂无已映射的外部来源，可在本体管理中配置来源映射。</p>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" data-testid="mapped-source-cards">
      <SavedEntitySourceCard />
      {sources.map(s => <MappedSourceCard key={`${s.source_system}/${s.dataset}`} source={s} />)}
    </div>
  </div>;
}
