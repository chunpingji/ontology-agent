"use client";

import { useEffect, useState } from "react";
import { searchEntities, getEntity } from "@/lib/api";
import type { EntityShadow, Individual } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default function EntitiesPage() {
  const [entities, setEntities] = useState<EntityShadow[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Individual | null>(null);

  const MODULES = ["drug", "drug-development", "document", "equipment", "facility", "contamination", "risk", "cleaning", "personnel", "integration"];
  // Radix Select 不允许空字符串 value；用哨兵表示"全部模块"，对外仍映射回 ""（保持 module 过滤语义不变）。
  const ALL_MODULES = "__all__";

  useEffect(() => {
    const params: Record<string, string> = { page: String(page), page_size: "20" };
    if (query) params.q = query;
    if (moduleFilter) params.module = moduleFilter;
    searchEntities(params)
      .then((r) => { setEntities(r.items); setTotal(r.total); })
      .catch(console.error);
  }, [query, moduleFilter, page]);

  const handleSelect = (iri: string) => {
    getEntity(iri).then(setSelected).catch(console.error);
  };

  return (
    <div>
      <div className="mb-4 flex gap-3">
        <Input
          type="text"
          placeholder="搜索实体..."
          value={query}
          onChange={(e) => { setQuery(e.target.value); setPage(1); }}
          className="w-auto"
        />
        <Select
          value={moduleFilter || ALL_MODULES}
          onValueChange={(v) => { setModuleFilter(v === ALL_MODULES ? "" : v); setPage(1); }}
        >
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_MODULES}>全部模块</SelectItem>
            {MODULES.map((m) => (
              <SelectItem key={m} value={m}>{m}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="self-center text-sm text-muted-foreground">共 {total} 个实体</span>
      </div>

      <div className="flex gap-4">
        <Card className="flex-1 overflow-auto">
          <Table>
            <TableHeader>
              <TableRow className="border-b bg-muted text-left text-xs text-muted-foreground">
                <TableHead className="px-3 py-2">IRI</TableHead>
                <TableHead className="px-3 py-2">标签</TableHead>
                <TableHead className="px-3 py-2">模块</TableHead>
                <TableHead className="px-3 py-2">类</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entities.map((e) => (
                <TableRow
                  key={e.iri}
                  onClick={() => handleSelect(e.iri)}
                  className="cursor-pointer border-b hover:bg-accent"
                >
                  <TableCell className="px-3 py-2 font-mono text-xs text-muted-foreground">
                    {e.iri.split("/").pop()}
                  </TableCell>
                  <TableCell className="px-3 py-2">{e.label_zh || e.label_en || "-"}</TableCell>
                  <TableCell className="px-3 py-2">
                    <Badge variant="secondary">{e.module}</Badge>
                  </TableCell>
                  <TableCell className="px-3 py-2 text-xs text-muted-foreground">
                    {e.class_iri.split("/").pop()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {entities.length === 0 && (
            <p className="p-4 text-center text-sm text-muted-foreground">暂无数据</p>
          )}
        </Card>

        {selected && (
          <Card className="w-96 shrink-0 p-4">
            <h2 className="mb-2 font-bold">{selected.name}</h2>
            <p className="mb-1 break-all text-xs text-muted-foreground">{selected.iri}</p>
            {selected.label_zh && <p className="text-sm">{selected.label_zh}</p>}
            <div className="mt-3">
              <h3 className="mb-1 text-sm font-semibold text-muted-foreground">类型</h3>
              {selected.class_iris.map((c) => (
                <Badge key={c} variant="secondary" className="mr-1 mb-1">
                  {c.split("/").pop()}
                </Badge>
              ))}
            </div>
            <div className="mt-3">
              <h3 className="mb-1 text-sm font-semibold text-muted-foreground">属性</h3>
              <dl className="space-y-1">
                {Object.entries(selected.properties).map(([key, val]) => (
                  <div key={key} className="flex gap-2 text-xs">
                    <dt className="w-40 shrink-0 truncate font-mono text-muted-foreground">
                      {key.split("/").pop()}
                    </dt>
                    <dd className="min-w-0 break-all text-foreground">{JSON.stringify(val)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
