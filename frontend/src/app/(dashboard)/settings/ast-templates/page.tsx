"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { MoreHorizontal, Plus, FileText } from "lucide-react";
import {
  fetchAstTemplates,
  updateAstTemplateMeta,
  deleteAstTemplate,
  setDefaultTemplate,
  parseSample,
  type AstTemplateDTO,
  type AstTemplateStatus,
  type TiptapContent,
} from "@/lib/api";
import { useCreateTemplateStore } from "@/lib/ast-template-create-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// 015: lifecycle status → 中文标签 + Badge 变体（与 design.pen 一致）。
const STATUS_META: Record<
  AstTemplateStatus,
  { label: string; variant: "default" | "secondary" | "outline" }
> = {
  published: { label: "已发布", variant: "default" },
  draft: { label: "草稿", variant: "outline" },
  archived: { label: "归档", variant: "secondary" },
};

export default function AstTemplatesPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<AstTemplateDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload dialog state
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadVersion, setUploadVersion] = useState("v1");
  const [uploadDocNo, setUploadDocNo] = useState("");
  const [uploadIriPattern, setUploadIriPattern] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [docxText, setDocxText] = useState<string | null>(null);
  const [sampleContent, setSampleContent] = useState<TiptapContent | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTemplates(await fetchAstTemplates());
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

  function handleEnterCreateEditor() {
    if (!sampleContent || !uploadName.trim()) return;
    useCreateTemplateStore.getState().setPayload({
      name: uploadName.trim(),
      version: uploadVersion || "v1",
      docNo: uploadDocNo || "QS-A-020F05",
      iriPattern: uploadIriPattern.trim(),
      sampleText: docxText,
      sampleContent,
    });
    setUploadOpen(false);
    router.push("/settings/ast-templates/create");
  }

  function resetUploadForm() {
    setUploadName("");
    setUploadVersion("v1");
    setUploadDocNo("");
    setUploadIriPattern("");
    setDocxText(null);
    setSampleContent(null);
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
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

  // 015: 就地切换生命周期状态（发布/归档），不产生新版本。
  async function handleSetStatus(id: string, status: AstTemplateStatus) {
    try {
      await updateAstTemplateMeta(id, { status });
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "状态更新失败");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">AST 报告模板</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            管理分析结构模板（AST）：每个模板将文档抽取结果映射为报告插槽，并按
            IRI 模式匹配文档类型。
          </p>
        </div>
        <Button
          onClick={() => { resetUploadForm(); setUploadOpen(true); }}
        >
          <Plus className="size-4" />
          从样例文档创建
        </Button>
      </div>

      <Card>
        <CardHeader className="border-b">
          <CardTitle className="text-base">模板列表</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="p-6 text-sm text-destructive">
              {error}{" "}
              <Button variant="outline" size="sm" onClick={reload}>
                重试
              </Button>
            </div>
          ) : templates.length === 0 ? (
            <EmptyState
              className="m-4"
              icon={<FileText />}
              title="暂无模板"
              description="上传一份样例 DOCX 文档，创建首个 AST 报告模板。"
              action={
                <Button
                  size="sm"
                  onClick={() => { resetUploadForm(); setUploadOpen(true); }}
                >
                  <Plus className="size-4" />
                  从样例文档创建
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead>名称</TableHead>
                  <TableHead>IRI 模式</TableHead>
                  <TableHead className="w-20">版本</TableHead>
                  <TableHead>模板编号</TableHead>
                  <TableHead className="w-20 text-center">插槽数</TableHead>
                  <TableHead className="w-24 text-center">状态</TableHead>
                  <TableHead className="w-16 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((t) => {
                  const status = STATUS_META[t.status] ?? STATUS_META.draft;
                  return (
                    <TableRow key={t.id}>
                      <TableCell className="font-medium">
                        <span className="flex items-center gap-2">
                          {t.name}
                          {t.is_default && (
                            <Badge variant="outline" className="text-xs">
                              默认
                            </Badge>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {t.iri_pattern ?? "—"}
                      </TableCell>
                      <TableCell>{t.version}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {t.doc_no ?? "—"}
                      </TableCell>
                      <TableCell className="text-center">{t.slot_count}</TableCell>
                      <TableCell className="text-center">
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="outline"
                              size="icon"
                              className="size-8"
                              aria-label="操作"
                            >
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => router.push(`/settings/ast-templates/${t.id}`)}>
                              编辑
                            </DropdownMenuItem>
                            {t.status !== "published" && (
                              <DropdownMenuItem
                                onSelect={() => handleSetStatus(t.id, "published")}
                              >
                                发布
                              </DropdownMenuItem>
                            )}
                            {t.status !== "archived" && (
                              <DropdownMenuItem
                                onSelect={() => handleSetStatus(t.id, "archived")}
                              >
                                归档
                              </DropdownMenuItem>
                            )}
                            {!t.is_default && (
                              <DropdownMenuItem
                                onSelect={() => handleSetDefault(t.id)}
                              >
                                设为默认
                              </DropdownMenuItem>
                            )}
                            {!t.is_default && (
                              <>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  className="text-destructive focus:text-destructive"
                                  onSelect={() => handleDelete(t.id, t.name)}
                                >
                                  删除
                                </DropdownMenuItem>
                              </>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

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
              <Label>IRI 模式（可选）</Label>
              <Input
                value={uploadIriPattern}
                onChange={(e) => setUploadIriPattern(e.target.value)}
                placeholder="例如 CMCReport"
                className="font-mono"
              />
              <p className="text-xs text-muted-foreground">
                文档类型 IRI 的匹配片段；生成报告时据此选用本模板（留空则仅作默认/回退）。
              </p>
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
              <p className="text-sm text-destructive">{uploadError}</p>
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
