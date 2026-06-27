"use client";

import { useEffect, useState } from "react";
import {
  getAllClasses,
  getSystemConfig,
  updateSystemConfig,
  type OntologyClassFlat,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

export default function SettingsPage() {
  const [classes, setClasses] = useState<OntologyClassFlat[]>([]);
  const [selectedIris, setSelectedIris] = useState<string[]>([]);
  const [keywords, setKeywords] = useState("临床备样,生产信息,备样生产");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    getAllClasses().then(setClasses).catch(() => {});
    getSystemConfig("default_extraction_targets")
      .then((cfg) => {
        if (Array.isArray(cfg.value)) setSelectedIris(cfg.value);
      })
      .catch(() => {});
    getSystemConfig("extraction_keywords")
      .then((cfg) => {
        if (typeof cfg.value === "string") setKeywords(cfg.value);
      })
      .catch(() => {});
  }, []);

  function toggleClass(iri: string) {
    setSelectedIris((prev) =>
      prev.includes(iri) ? prev.filter((i) => i !== iri) : [...prev, iri]
    );
  }

  function selectAll() {
    setSelectedIris(classes.map((c) => c.iri));
  }

  function clearAll() {
    setSelectedIris([]);
  }

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    try {
      await updateSystemConfig("default_extraction_targets", selectedIris);
      await updateSystemConfig("extraction_keywords", keywords);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  }

  const filtered = filter
    ? classes.filter(
        (c) =>
          (c.label ?? "").includes(filter) ||
          c.name.toLowerCase().includes(filter.toLowerCase()) ||
          c.module_key.includes(filter)
      )
    : classes;

  const grouped = filtered.reduce<Record<string, OntologyClassFlat[]>>((acc, c) => {
    (acc[c.module_key] ??= []).push(c);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>默认抽取目标</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            选择文件名匹配关键词时自动抽取的目标类。未配置时默认抽取所有类。
          </p>

          <div className="flex items-center gap-2">
            <Input
              placeholder="搜索类名..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="max-w-xs"
            />
            <Button variant="outline" size="sm" onClick={selectAll}>
              全选
            </Button>
            <Button variant="outline" size="sm" onClick={clearAll}>
              清空
            </Button>
            <span className="text-sm text-muted-foreground">
              已选 {selectedIris.length} / {classes.length}
            </span>
          </div>

          <div className="max-h-[50vh] overflow-auto space-y-4">
            {Object.entries(grouped).map(([module, items]) => (
              <div key={module}>
                <p className="text-sm font-medium mb-1">{module}</p>
                <div className="flex flex-wrap gap-1">
                  {items.map((c) => {
                    const selected = selectedIris.includes(c.iri);
                    return (
                      <Badge
                        key={c.iri}
                        variant={selected ? "default" : "outline"}
                        className="cursor-pointer select-none"
                        onClick={() => toggleClass(c.iri)}
                      >
                        {c.label ?? c.name}
                      </Badge>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>文件类型关键词</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            文件名包含以下关键词时，自动应用上方默认抽取目标。逗号分隔。
          </p>
          <div className="space-y-1">
            <Label>关键词列表</Label>
            <Input
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="临床备样,生产信息"
            />
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "保存中..." : "保存配置"}
        </Button>
        {saved && <span className="text-sm text-green-600">已保存</span>}
      </div>
    </div>
  );
}
