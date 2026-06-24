"use client";

import { useEffect, useState } from "react";
import {
  createMapping,
  deleteMapping,
  getMappingHealth,
  getMappings,
  type MappingHealth,
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
import type { useVersionConflict } from "./use-version-conflict";

const MAPPING_TYPES = ["slpra_iri", "bfo", "field", "external"];

type Conflict = ReturnType<typeof useVersionConflict>;

/**
 * 映射面板（T036）：维护类的 SLPRA·IRI / BFO / 字段映射，并展示全局健康度徽标
 * （ok / 未映射 / 漂移 / 孤立）。
 */
export function OntologyMappingPanel({
  classIri,
  conflict,
  onChanged,
}: {
  classIri: string | null;
  conflict: Conflict;
  onChanged: () => void;
}) {
  const [maps, setMaps] = useState<TBoxMapping[]>([]);
  const [health, setHealth] = useState<MappingHealth | null>(null);
  const [form, setForm] = useState({ mapping_type: "bfo", target: "", source_system: "" });
  const [error, setError] = useState<string | null>(null);

  const loadHealth = () => getMappingHealth().then(setHealth).catch(() => {});

  useEffect(() => {
    loadHealth();
  }, []);

  // 按 classIri 作为 key 挂载，空白态由初值 [] 覆盖；effect 仅在异步回调内 setState。
  useEffect(() => {
    if (classIri) getMappings(classIri).then(setMaps).catch((e) => setError(String(e)));
  }, [classIri]);

  const refresh = () => {
    if (classIri) getMappings(classIri).then(setMaps).catch(() => {});
    loadHealth();
    onChanged();
  };

  const add = async () => {
    if (!classIri) return;
    setError(null);
    try {
      await createMapping(classIri, {
        mapping_type: form.mapping_type,
        target: form.target,
        source_system: form.source_system || null,
      });
      setForm({ mapping_type: "bfo", target: "", source_system: "" });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (m: TBoxMapping) => {
    setError(null);
    try {
      const done = await conflict.run(() => deleteMapping(m.id, m.version).then(() => ({ ok: true })));
      if (done) refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const healthBadge = (() => {
    if (!health || !classIri) return null;
    const label = health.ok.includes(classIri)
      ? { t: "映射健全", c: "bg-success/10 text-success" }
      : health.drift.includes(classIri)
        ? { t: "映射漂移", c: "bg-warning/10 text-warning" }
        : health.orphan.includes(classIri)
          ? { t: "孤立映射", c: "bg-primary/10 text-primary" }
          : { t: "未映射", c: "bg-destructive/10 text-destructive" };
    return <span className={`rounded px-2 py-0.5 text-xs ${label.c}`}>{label.t}</span>;
  })();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">映射</h3>
        {healthBadge}
      </div>
      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}

      {!classIri ? (
        <p className="text-xs text-muted-foreground">先选择一个类</p>
      ) : (
        <>
          <ul className="divide-y text-sm">
            {maps.map((m) => (
              <li key={m.id} className="flex items-center justify-between py-1.5">
                <span className="text-xs">
                  <Badge variant="secondary" className="font-normal">{m.mapping_type}</Badge>
                  <span className="ml-2 font-mono text-muted-foreground">{m.target}</span>
                </span>
                <Button
                  variant="link"
                  onClick={() => remove(m)}
                  className="h-auto p-0 text-xs text-destructive hover:underline"
                >
                  删除
                </Button>
              </li>
            ))}
            {maps.length === 0 && <li className="py-2 text-xs text-muted-foreground">暂无映射</li>}
          </ul>

          <div className="space-y-2 rounded border border-border bg-muted p-2">
            <p className="text-xs font-medium text-muted-foreground">新建映射</p>
            <div className="flex gap-2">
              <Field label="映射类型" className="w-1/3">
                <Select
                  value={form.mapping_type}
                  onValueChange={(v) => setForm({ ...form, mapping_type: v })}
                >
                  <SelectTrigger className="h-auto w-full px-2 py-1 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MAPPING_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="目标 target" hint="映射指向的 IRI / 字段" className="w-2/3">
                <Input
                  placeholder="target"
                  value={form.target}
                  onChange={(e) => setForm({ ...form, target: e.target.value })}
                  className="h-auto w-full px-2 py-1 text-sm"
                />
              </Field>
            </div>
            <Field label="来源系统 source" hint="可选">
              <Input
                placeholder="source system（可选）"
                value={form.source_system}
                onChange={(e) => setForm({ ...form, source_system: e.target.value })}
                className="h-auto w-full px-2 py-1 text-sm"
              />
            </Field>
            <Button onClick={add} size="sm" className="text-sm">
              添加映射
            </Button>
          </div>

          {health && (
            <div className="flex flex-wrap gap-2 pt-1 text-xs text-muted-foreground">
              <span>健全 {health.ok.length}</span>
              <span>未映射 {health.unmapped.length}</span>
              <span>漂移 {health.drift.length}</span>
              <span>孤立 {health.orphan.length}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
