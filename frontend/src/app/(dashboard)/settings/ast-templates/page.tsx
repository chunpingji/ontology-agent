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
  parseSample,
  type AstTemplateDTO,
  type DocTypeMappingDTO,
  type TiptapContent,
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
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [docxText, setDocxText] = useState<string | null>(null);
  // 013: 后台解析出的忠于原文结构的 tiptap 样例（供融合编辑器忠实预览 + AI 分析回传）。
  const [sampleContent, setSampleContent] = useState<TiptapContent | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Slot editor state（创建与编辑统一为同一融合视图）
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<"create" | "edit">("edit");
  const [editorTemplate, setEditorTemplate] = useState<AstTemplateDTO | null>(null);
  const [editorSchema, setEditorSchema] = useState<Record<string, unknown> | null>(null);
  const [editorSampleText, setEditorSampleText] = useState<string | null>(null);
  const [editorSampleContent, setEditorSampleContent] = useState<TiptapContent | null>(null);
  const [editorSaving, setEditorSaving] = useState(false);
  // 创建模式下从上传对话框快照的元数据，保存时用于 createAstTemplate（与编辑模式解耦）。
  const [createMeta, setCreateMeta] = useState<{
    name: string;
    version: string;
    docNo: string;
    sampleText: string | null;
    sampleContent: TiptapContent | null;
  } | null>(null);

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

  async function handleDocxFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    setUploadError(null);
    setDocxText(null);
    setSampleContent(null);
    const file = e.target.files?.[0];
    if (!file) return;
    setExtracting(true);
    try {
      // 后台解析为忠于原文结构的 tiptap（不扁平化）；plain_text 仅用于字符数提示与 sample_text 持久化。
      const { content_json, plain_text } = await parseSample(file);
      setSampleContent(content_json);
      setDocxText(plain_text);
      if (!uploadName) {
        const baseName = file.name.replace(/\.docx$/i, "");
        setUploadName(baseName);
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "文档解析失败");
    } finally {
      setExtracting(false);
    }
  }

  // 创建流程改为进入融合编辑器（mode=create）：以空骨架 schema 起步，左侧忠实预览，
  // 右侧可 AI 分析→审核采纳/手动加插槽，保存时才 createAstTemplate。
  function handleEnterCreateEditor() {
    if (!sampleContent || !uploadName.trim()) return;
    const name = uploadName.trim();
    const version = uploadVersion || "v1";
    const docNo = uploadDocNo || "QS-A-020F05";
    setCreateMeta({ name, version, docNo, sampleText: docxText, sampleContent });
    setEditorMode("create");
    setEditorTemplate(null);
    setEditorSchema({ template_id: name, doc_no: docNo, revision: version, sections: [] });
    setEditorSampleText(docxText);
    setEditorSampleContent(sampleContent);
    setUploadOpen(false);
    setEditorOpen(true);
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
      setEditorMode("edit");
      setEditorTemplate(t);
      setEditorSchema(full.schema_json);
      setEditorSampleText(full.sample_text ?? null);
      setEditorSampleContent(full.sample_content_json ?? null);
      setEditorOpen(true);
    } catch (e) {
      alert(e instanceof Error ? e.message : "加载模板详情失败");
    }
  }

  // 关闭融合编辑器并复位所有相关状态（创建/编辑共用）。
  function closeEditor() {
    setEditorOpen(false);
    setEditorTemplate(null);
    setEditorSchema(null);
    setEditorSampleText(null);
    setEditorSampleContent(null);
    setEditorMode("edit");
    setCreateMeta(null);
  }

  async function handleEditorSave(updated: Record<string, unknown>) {
    if (editorMode === "create" ? !createMeta : !editorTemplate) return;
    setEditorSaving(true);
    try {
      if (editorMode === "create" && createMeta) {
        await createAstTemplate({
          name: createMeta.name,
          version: createMeta.version,
          doc_no: createMeta.docNo || undefined,
          schema_json: updated,
          sample_text: createMeta.sampleText ?? undefined,
          // 013: 持久化忠于原文结构的 tiptap 样例，重新编辑时也能忠实预览。
          sample_content_json: createMeta.sampleContent ?? undefined,
        });
        // 复位上传对话框状态。
        setUploadName("");
        setUploadVersion("v1");
        setUploadDocNo("");
        setDocxText(null);
        setSampleContent(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      } else if (editorTemplate) {
        await updateAstTemplate(editorTemplate.id, { schema_json: updated });
      }
      closeEditor();
      await reload();
    } catch (e) {
      alert(
        e instanceof Error
          ? e.message
          : editorMode === "create" ? "创建失败" : "保存失败",
      );
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
            从样例文档创建
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
        <Dialog open={editorOpen} onOpenChange={(open) => { if (!open) closeEditor(); }}>
          <DialogContent className="max-w-[95vw] w-[95vw] h-[90vh] p-0 gap-0 overflow-hidden flex flex-col">
            <DialogHeader className="px-6 py-3 border-b shrink-0">
              <DialogTitle>
                {editorMode === "create"
                  ? `创建模板：${createMeta?.name ?? ""}`
                  : `编辑模板：${editorTemplate?.name} (${editorTemplate?.version})`}
              </DialogTitle>
            </DialogHeader>
            <div className="flex-1 min-h-0">
              <TemplateSlotEditor
                key={editorTemplate?.id ?? "create"}
                schema={editorSchema as any}
                mode={editorMode}
                onSave={(updated) => handleEditorSave(updated as unknown as Record<string, unknown>)}
                onCancel={closeEditor}
                saving={editorSaving}
                aiEnabled
                sampleText={editorSampleText}
                sampleContentJson={editorSampleContent}
              />
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* ── Upload dialog (DOCX sample) ─────────────────────────── */}
      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>从样例文档创建模板</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              上传一份样例 DOCX 文档，进入编辑器后可用 AI 分析建议插槽，或手动创建插槽结构。
            </p>
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
              <Label>样例 DOCX 文件</Label>
              <Input
                ref={fileInputRef}
                type="file"
                accept=".docx"
                onChange={handleDocxFileChange}
              />
              {extracting && (
                <p className="text-sm text-muted-foreground animate-pulse">
                  正在解析文档…
                </p>
              )}
              {docxText && (
                <p className="text-sm text-green-600">
                  文档已解析（{docxText.length} 字符）
                </p>
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
              onClick={handleEnterCreateEditor}
              disabled={!sampleContent || !uploadName.trim() || extracting}
            >
              进入编辑器
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
