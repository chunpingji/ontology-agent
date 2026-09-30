"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Database, Loader2 } from "lucide-react";
import { getEntity, getModules, searchEntities,
  type EntitySearchResult, type Individual, type Module } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetTrigger, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const PAGE_SIZE = 20;
const ALL_MODULES = "__all__";
const short = (iri: string) => iri.split(/[\/#]/).pop() || iri;

function SavedEntityDetail({ iri }: { iri: string }) {
  const [entity, setEntity] = useState<Individual | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    getEntity(iri, abort.signal)
      .then(value => { if (!abort.signal.aborted) setEntity(value); })
      .catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [iri]);
  return <Card className="min-w-0 space-y-3 p-4" data-testid="saved-entity-detail">
    {error ? <p role="alert" className="break-all text-sm text-destructive">{error}</p> : !entity ?
      <p role="status" className="text-sm text-muted-foreground">正在读取实体详情…</p> : <>
        <h3 className="break-words font-semibold">{entity.label_zh || entity.label_en || entity.name}</h3>
        <p className="text-sm text-muted-foreground">已保存实体 · 本地实体库</p>
        <p className="break-all text-xs text-muted-foreground">{entity.iri}</p>
        <div><h4 className="mb-1 text-sm font-medium">类型</h4>
          {entity.class_iris.map(c => <Badge key={c} variant="secondary" className="mb-1 mr-1 max-w-full break-all" title={c}>{short(c)}</Badge>)}
        </div>
        <div><h4 className="mb-2 text-sm font-medium">属性</h4>
          <dl className="space-y-2">
            {Object.entries(entity.properties).map(([key, value]) => <div key={key} className="space-y-1 text-xs">
              <dt className="break-all font-medium text-muted-foreground" title={key}>{short(key)}</dt>
              <dd className="break-all rounded bg-muted p-2">{JSON.stringify(value)}</dd>
            </div>)}
          </dl>
          {Object.keys(entity.properties).length === 0 && <p className="text-xs text-muted-foreground">暂无属性</p>}
        </div>
      </>}
  </Card>;
}

function SavedEntityResults({ query, module, page, onPageChange }: {
  query: string; module: string; page: number; onPageChange: (page: number) => void;
}) {
  const [result, setResult] = useState<EntitySearchResult | null>(null);
  const [selectedIri, setSelectedIri] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) };
    if (query) params.q = query;
    if (module) params.module = module;
    searchEntities(params, abort.signal)
      .then(value => { if (!abort.signal.aborted) setResult(value); })
      .catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [query, module, page, reload]);

  if (error) return <div role="alert" className="space-y-2 text-sm text-destructive">
    <p className="break-all">{error}</p><Button variant="outline" onClick={() => { setError(""); setReload(n => n + 1); }}>重新加载</Button>
  </div>;
  if (!result) return <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
    <Loader2 className="h-4 w-4 animate-spin" />正在读取已保存实体…
  </p>;

  return <div className="space-y-3" data-testid="saved-entity-results">
    <div className="flex items-center justify-between gap-2 text-sm" aria-live="polite">
      <p className="font-medium">共 {result.total} 个实体</p>
      <span className="text-xs text-muted-foreground">第 {page} 页 · 每页 {PAGE_SIZE} 条</span>
    </div>
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <Card className="min-w-0 max-h-[52vh] overflow-auto">
        <Table>
          <TableHeader><TableRow><TableHead>实体</TableHead><TableHead>模块 / 类</TableHead></TableRow></TableHeader>
          <TableBody>{result.items.map(entity => <TableRow key={entity.iri}
            data-state={selectedIri === entity.iri ? "selected" : undefined}>
            <TableCell className="max-w-64 align-top">
              <button type="button" className="w-full break-all rounded text-left text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setSelectedIri(entity.iri)}>{entity.label_zh || entity.label_en || short(entity.iri)}</button>
              <p className="mt-1 break-all text-xs text-muted-foreground">{short(entity.iri)}</p>
            </TableCell>
            <TableCell className="max-w-40 align-top"><Badge variant="secondary">{entity.module}</Badge>
              <p className="mt-1 break-all text-xs text-muted-foreground" title={entity.class_iri}>{short(entity.class_iri)}</p>
            </TableCell>
          </TableRow>)}</TableBody>
        </Table>
        {result.items.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">没有符合条件的已保存实体</p>}
      </Card>
      <div className="min-w-0 max-h-[52vh] overflow-y-auto">
        {selectedIri ? <SavedEntityDetail key={selectedIri} iri={selectedIri} /> :
          <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">选择实体查看详情</p>}
      </div>
    </div>
    <div className="flex items-center justify-between gap-2 border-t pt-3">
      <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</Button>
      <Button size="sm" variant="outline" disabled={page * PAGE_SIZE >= result.total} onClick={() => onPageChange(page + 1)}>下一页</Button>
    </div>
  </div>;
}

function SavedEntities() {
  const [query, setQuery] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [page, setPage] = useState(1);
  const [modules, setModules] = useState<Module[]>([]);
  const [moduleError, setModuleError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    getModules(abort.signal)
      .then(value => { if (!abort.signal.aborted) setModules(value); })
      .catch(() => { if (!abort.signal.aborted) setModuleError("模块目录暂不可用，可继续搜索全部已保存实体。"); });
    return () => abort.abort();
  }, []);
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-3">
      <Input aria-label="搜索已保存实体" placeholder="搜索实体名称或 IRI…" value={query}
        onChange={e => { setQuery(e.target.value); setPage(1); }} className="min-w-0 flex-1 basis-48" />
      <Select value={moduleFilter || ALL_MODULES} onValueChange={v => { setModuleFilter(v === ALL_MODULES ? "" : v); setPage(1); }}>
        <SelectTrigger aria-label="实体模块" className="w-44"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value={ALL_MODULES}>全部模块</SelectItem>
          {modules.map(m => <SelectItem key={m.key} value={m.key}>{m.label || m.key}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    {moduleError && <p role="status" className="text-sm text-warning">{moduleError}</p>}
    <SavedEntityResults key={JSON.stringify([query, moduleFilter, page])}
      query={query} module={moduleFilter} page={page} onPageChange={setPage} />
  </div>;
}

export function SavedEntitySourceCard() {
  const [open, setOpen] = useState(false);
  return <Sheet open={open} onOpenChange={setOpen}>
    <Card className="overflow-hidden transition-colors hover:border-primary/50 hover:shadow-md">
      <SheetTrigger asChild>
        <button type="button" aria-label="查看已保存实体" data-testid="saved-entity-source"
          className="flex h-full w-full flex-col gap-4 p-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <span className="flex w-full items-center justify-between gap-3">
            <span className="rounded-lg bg-primary/10 p-2.5 text-primary"><Database className="h-5 w-5" /></span>
            <span className="text-xs text-muted-foreground">本地来源</span>
          </span>
          <span className="space-y-1"><span className="block text-base font-semibold">已保存实体</span>
            <span className="block text-xs text-muted-foreground">浏览系统中已保存的实体</span>
          </span>
          <span className="rounded-md bg-secondary px-2 py-1 text-xs text-secondary-foreground">本地实体库</span>
          <span className="mt-auto flex w-full items-center justify-between border-t pt-3 text-xs text-muted-foreground">
            <span>支持搜索与模块筛选</span><span className="inline-flex items-center gap-1 text-primary">查看实体<ArrowUpRight className="h-3.5 w-3.5" /></span>
          </span>
        </button>
      </SheetTrigger>
    </Card>
    <SheetContent className="gap-0 p-0 sm:max-w-4xl xl:max-w-5xl">
      <SheetHeader className="shrink-0 border-b px-6 py-5 pr-12">
        <SheetTitle>已保存实体</SheetTitle>
        <SheetDescription>浏览本地实体库，按名称、IRI 或模块查找实体并查看详情。</SheetDescription>
      </SheetHeader>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{open && <SavedEntities />}</div>
    </SheetContent>
  </Sheet>;
}
