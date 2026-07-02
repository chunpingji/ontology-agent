"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  fetchAstTemplates,
  getAstTemplate,
  createAstTemplate,
  updateAstTemplate,
  deleteAstTemplate,
  setDefaultTemplate,
  fetchDocTypeMappings,
  createDocTypeMapping,
  deleteDocTypeMapping,
  type AstTemplateDTO,
  type DocTypeMappingDTO,
} from "@/lib/api";
import { TemplateSlotEditor } from "@/components/extraction/template-slot-editor";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function AstTemplatesPage() {
  const [templates, setTemplates] = useState<AstTemplateDTO[]>([]);
  const [mappings, setMappings] = useState<DocTypeMappingDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload dialog state
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadVersion, setUploadVersion] = useState("v1");
  const [uploadDocNo, setUploadDocNo] = useState("");
  const [uploadJson, setUploadJson] = useState<Record<string, unknown> | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Slot editor state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorTemplate, setEditorTemplate] = useState<AstTemplateDTO | null>(null);
  const [editorSchema, setEditorSchema] = useState<Record<string, unknown> | null>(null);
  const [editorSaving, setEditorSaving] = useState(false);

  // Mapping form state
  const [mappingPattern, setMappingPattern] = useState("");
  const [mappingTemplateId, setMappingTemplateId] = useState("");
  const [mappingPriority, setMappingPriority] = useState("0");
  const [creatingMapping, setCreatingMapping] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tpls, maps] = await Promise.all([
        fetchAstTemplates(),
        fetchDocTypeMappings(),
      ]);
      setTemplates(tpls);
      setMappings(maps);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    setUploadError(null);
    setUploadJson(null);
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const json = JSON.parse(text);
      setUploadJson(json);
      if (!uploadName && json.template_id) {
        setUploadName(json.template_id);
      }
    } catch {
      setUploadError("无法解析 JSON 文件");
    }
  }

  async function handleUpload() {
    if (!uploadJson || !uploadName.trim()) return;
    setUploading(true);
    setUploadError(null);
    try {
      await createAstTemplate({
        name: uploadName.trim(),
        version: uploadVersion || "v1",
        doc_no: uploadDocNo || undefined,
        schema_json: uploadJson,
      });
      setUploadOpen(false);
      setUploadName("");
      setUploadVersion("v1");
      setUploadDocNo("");
      setUploadJson(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await reload();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确认删除模板「${name}」？`)) return;
    try {
      await deleteAstTemplate(id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function handleSetDefault(id: string) {
    try {
      await setDefaultTemplate(id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "设置失败");
    }
  }

  async function handleOpenEditor(t: AstTemplateDTO) {
    try {
      const full = await getAstTemplate(t.id);
      setEditorTemplate(t);
      setEditorSchema(full.schema_json);
      setEditorOpen(true);
    } catch (e) {
      alert(e instanceof Error ? e.message : "加载模板详情失败");
    }
  }

  async function handleEditorSave(updated: Record<string, unknown>) {
    if (!editorTemplate) return;
    setEditorSaving(true);
    try {
      await updateAstTemplate(editorTemplate.id, { schema_json: updated });
      setEditorOpen(false);
      setEditorTemplate(null);
      setEditorSchema(null);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setEditorSaving(false);
    }
  }

  async function handleCreateMapping() {
    if (!mappingPattern.trim() || !mappingTemplateId) return;
    setCreatingMapping(true);
    try {
      await createDocTypeMapping({
        doc_class_iri_pattern: mappingPattern.trim(),
        template_id: mappingTemplateId,
        priority: parseInt(mappingPriority, 10) || 0,
      });
      setMappingPattern("");
      setMappingTemplateId("");
      setMappingPriority("0");
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreatingMapping(false);
    }
  }

  async function handleDeleteMapping(id: string) {
    if (!confirm("确认删除此映射？")) return;
    try {
      await deleteDocTypeMapping(id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "删除失败");
    }
  }

  if (loading) {
    return <div className="text-muted-foreground p-4">加载中…</div>;
  }

  if (error) {
    return (
      <div className="p-4 text-red-600">
        {error}{" "}
        <Button variant="outline" size="sm" onClick={reload}>重试</Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Template table ──────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>AST 报告模板</CardTitle>
          <Button size="sm" onClick={() => setUploadOpen(true)}>
            上传模板
          </Button>
        </CardHeader>
        <CardContent>
          {templates.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无模板</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>文档编号</TableHead>
                  <TableHead className="text-center">插槽数</TableHead>
                  <TableHead className="text-center">状态</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell>{t.version}</TableCell>
                    <TableCell>{t.doc_no ?? "—"}</TableCell>
                    <TableCell className="text-center">{t.slot_count}</TableCell>
                    <TableCell className="text-center">
                      {t.is_default && <Badge variant="default">默认</Badge>}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleOpenEditor(t)}
                        >
                          编辑
                        </Button>
                        {!t.is_default && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleSetDefault(t.id)}
                          >
                            设为默认
                          </Button>
                        )}
                        {!t.is_default && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDelete(t.id, t.name)}
                          >
                            删除
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ── Document type mappings ──────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>文档类型映射</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            配置文档类型 IRI 模式与模板的映射关系。优先级高的映射优先匹配。
          </p>

          {mappings.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>IRI 模式</TableHead>
                  <TableHead>模板</TableHead>
                  <TableHead className="text-center">优先级</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {mappings.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-sm">
                      {m.doc_class_iri_pattern}
                    </TableCell>
                    <TableCell>
                      {m.template_name} ({m.template_version})
                    </TableCell>
                    <TableCell className="text-center">{m.priority}</TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDeleteMapping(m.id)}
                      >
                        删除
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          <div className="flex items-end gap-3 pt-2 border-t">
            <div className="space-y-1 flex-1">
              <Label>IRI 模式</Label>
              <Input
                placeholder="例如 CMCReport"
                value={mappingPattern}
                onChange={(e) => setMappingPattern(e.target.value)}
              />
            </div>
            <div className="space-y-1 w-48">
              <Label>模板</Label>
              <Select value={mappingTemplateId} onValueChange={setMappingTemplateId}>
                <SelectTrigger>
                  <SelectValue placeholder="选择模板" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name} ({t.version})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 w-20">
              <Label>优先级</Label>
              <Input
                type="number"
                value={mappingPriority}
                onChange={(e) => setMappingPriority(e.target.value)}
              />
            </div>
            <Button
              size="sm"
              disabled={!mappingPattern.trim() || !mappingTemplateId || creatingMapping}
              onClick={handleCreateMapping}
            >
              添加映射
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Slot editor dialog ──────────────────────────────────── */}
      {editorOpen && editorSchema && (
        <Dialog open={editorOpen} onOpenChange={(open) => { if (!open) { setEditorOpen(false); setEditorTemplate(null); setEditorSchema(null); }}}>
          <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto">
            <DialogHeader>
              <DialogTitle>
                编辑模板：{editorTemplate?.name} ({editorTemplate?.version})
              </DialogTitle>
            </DialogHeader>
            <TemplateSlotEditor
              schema={editorSchema as any}
              onSave={(updated) => handleEditorSave(updated as Record<string, unknown>)}
              onCancel={() => { setEditorOpen(false); setEditorTemplate(null); setEditorSchema(null); }}
              saving={editorSaving}
            />
          </DialogContent>
        </Dialog>
      )}

      {/* ── Upload dialog ───────────────────────────────────────── */}
      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>上传模板</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1">
              <Label>模板名称</Label>
              <Input
                value={uploadName}
                onChange={(e) => setUploadName(e.target.value)}
                placeholder="例如 稳定性评估"
              />
            </div>
            <div className="flex gap-3">
              <div className="space-y-1 flex-1">
                <Label>版本</Label>
                <Input
                  value={uploadVersion}
                  onChange={(e) => setUploadVersion(e.target.value)}
                  placeholder="v1"
                />
              </div>
              <div className="space-y-1 flex-1">
                <Label>文档编号（可选）</Label>
                <Input
                  value={uploadDocNo}
                  onChange={(e) => setUploadDocNo(e.target.value)}
                  placeholder="QS-A-020F05"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label>模板 JSON 文件</Label>
              <Input
                ref={fileInputRef}
                type="file"
                accept=".json"
                onChange={handleFileChange}
              />
              {uploadJson && (
                <p className="text-sm text-green-600">JSON 已加载</p>
              )}
            </div>
            {uploadError && (
              <p className="text-sm text-red-600">{uploadError}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUploadOpen(false)}>
              取消
            </Button>
            <Button
              onClick={handleUpload}
              disabled={!uploadJson || !uploadName.trim() || uploading}
            >
              {uploading ? "上传中…" : "上传"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
