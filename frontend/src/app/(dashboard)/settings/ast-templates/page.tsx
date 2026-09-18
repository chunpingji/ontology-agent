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
  createAstTemplate,
  saveTemplateSample,
  DOCUMENT_TYPE_GROUPS,
  type AstTemplateDTO,
  type AstTemplateStatus,
  type TiptapContent,
} from "@/lib/api";
import { emptyTemplate } from "@/lib/reporting-v2";
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
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
  // 进入定义页前，先保存模板记录，再附加原始输出格式文件。
  const [sampleFile, setSampleFile] = useState<File | null>(null);
  const [creating, setCreating] = useState(false);
  const [saveStep, setSaveStep] = useState<"template" | "sample" | null>(null);
  const [sampleSaved, setSampleSaved] = useState(false);
  const creatingRef = useRef(false);
  const [createdDraft, setCreatedDraft] = useState<AstTemplateDTO | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const parseControllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => parseControllerRef.current?.abort(), []);

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

  useEffect(() => {
    let active = true;
    fetchAstTemplates().then((result) => { if (active) setTemplates(result); })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : "加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function handleDocxFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (creatingRef.current || createdDraft) return;
    parseControllerRef.current?.abort();
    const controller = new AbortController();
    parseControllerRef.current = controller;
    setUploadError(null);
    setDocxText(null);
    setSampleContent(null);
    setSampleFile(null);
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) {
      setExtracting(false);
      return;
    }
    setSampleFile(file);
    setExtracting(true);
    try {
      // 后台解析为忠于原文结构的 tiptap；此处仅确认可解析，保存时由原始附件生成持久内容。
      const { content_json, plain_text } = await parseSample(file, controller.signal);
      if (controller.signal.aborted || parseControllerRef.current !== controller) return;
      setSampleContent(content_json);
      setDocxText(plain_text);
      if (!uploadName) {
        const baseName = file.name.slice(0, file.name.lastIndexOf(".")) || file.name;
        setUploadName(baseName);
      }
    } catch (err) {
      if (!controller.signal.aborted && parseControllerRef.current === controller) {
        setUploadError(err instanceof Error ? err.message : "文档解析失败");
      }
    } finally {
      if (parseControllerRef.current === controller) setExtracting(false);
    }
  }

  function cancelSampleParse() {
    parseControllerRef.current?.abort();
    parseControllerRef.current = null;
    setExtracting(false);
  }

  function handleUploadOpenChange(open: boolean) {
    if (creatingRef.current) return;
    if (!open) cancelSampleParse();
    setUploadOpen(open);
    // 超时可能发生在服务端提交之后、返回 ID 之前；关闭时也刷新列表以核对结果。
    if (!open) void reload();
  }

  async function handleEnterCreateEditor() {
    if (creatingRef.current || sampleSaved || extracting || !sampleContent || !sampleFile
      || !uploadName.trim() || !uploadIriPattern.trim()) return;
    creatingRef.current = true;
    setCreating(true);
    setUploadError(null);
    let draft = createdDraft;
    try {
      if (!draft) {
        setSaveStep("template");
        const docNo = uploadDocNo || "QS-A-020F05";
        const documentClass = uploadIriPattern.trim();
        draft = await createAstTemplate({
          name: uploadName.trim(), version: uploadVersion || "v1",
          doc_no: docNo, iri_pattern: documentClass,
          schema_json: { ...emptyTemplate(), doc_no: docNo,
            source_slots: [{ source_slot_id: "source", kind: "document", class_iri: documentClass }] },
        });
        setCreatedDraft(draft);
        const saved = draft;
        setTemplates((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      }
      // 附件失败时保留已创建身份，后续只重试附件，不重新 POST 模板。
      setSaveStep("sample");
      await saveTemplateSample(draft.id, sampleFile);
      setSampleSaved(true);
    } catch (err) {
      const reason = err instanceof Error ? err.message : "请重试";
      setUploadError(draft
        ? `模板草稿已保存，输出样例尚未确认保存成功。可核对结果或重试保存样例：${reason}`
        : `未能确认模板保存成功，填写内容和样例已保留：${reason}`);
      return;
    } finally {
      creatingRef.current = false;
      setCreating(false);
      setSaveStep(null);
    }
    // 持久化确认后即结束保存状态；导航慢时仍可关闭弹窗或直接打开已有模板。
    router.push(`/settings/ast-templates/${draft.id}?tab=template&created=1`);
  }

  function resetUploadForm() {
    cancelSampleParse();
    setCreatedDraft(null);
    setSampleSaved(false);
    setSaveStep(null);
    setUploadName("");
    setUploadVersion("v1");
    setUploadDocNo("");
    setUploadIriPattern("");
    setDocxText(null);
    setSampleContent(null);
    setSampleFile(null);
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
      if (status === "published") {
        router.push("/settings/ast-templates/" + id);
        return;
      }
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
          <h1 className="text-2xl font-bold text-foreground">AST(Assessment Semantic template)报告模板</h1>
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
              description="上传一份样例 Word 文档（.doc / .docx），创建首个 AST 报告模板。"
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
                  <TableHead>输出模板文档</TableHead>
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
                          {t.demo_profile && <Badge variant="secondary">演示专用</Badge>}
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
                      <TableCell className="text-xs text-muted-foreground max-w-[180px] truncate" title={t.sample_docx_filename ?? undefined}>
                        {t.sample_docx_filename ?? <span className="italic">未上传</span>}
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
                              {t.demo_profile ? "查看演示" : "编辑"}
                            </DropdownMenuItem>
                            {!t.demo_profile && t.status !== "published" && (
                              <DropdownMenuItem
                                onSelect={() => handleSetStatus(t.id, "published")}
                              >
                                发布
                              </DropdownMenuItem>
                            )}
                            {!t.demo_profile && t.status !== "archived" && (
                              <DropdownMenuItem
                                onSelect={() => handleSetStatus(t.id, "archived")}
                              >
                                归档
                              </DropdownMenuItem>
                            )}
                            {!t.demo_profile && !t.is_default && (
                              <DropdownMenuItem
                                onSelect={() => handleSetDefault(t.id)}
                              >
                                设为默认
                              </DropdownMenuItem>
                            )}
                            {!t.demo_profile && !t.is_default && (
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
      <Dialog open={uploadOpen} onOpenChange={handleUploadOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>从样例文档创建模板</DialogTitle>
          </DialogHeader>
          <fieldset disabled={creating || !!createdDraft} className="min-w-0 space-y-4">
            <p className="text-sm text-muted-foreground">
              上传期望输出的报告样例（.doc / .docx），点击「进入模板定义」即保存模板和样例，并进入编辑状态。关联文档类型用于选择生成报告所需的输入文档。
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
              <Label>
                关联文档类型 <span className="text-destructive">*</span>
              </Label>
              <Select
                disabled={creating || !!createdDraft}
                value={uploadIriPattern || undefined}
                onValueChange={setUploadIriPattern}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择文档类型…" />
                </SelectTrigger>
                <SelectContent>
                  {DOCUMENT_TYPE_GROUPS.map((g) => (
                    <SelectGroup key={g.group}>
                      <SelectLabel>{g.group}</SelectLabel>
                      {g.options.map((o) => (
                        <SelectItem key={o.iri} value={o.iri}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                必选。选中文档类型即绑定其本体图谱——驱动「AI分析」编译覆盖声明，
                并作为生成报告时选用本模板的解析键。
              </p>
            </div>
            <div className="space-y-1">
              <Label>输出样例 Word 文件（.doc / .docx）</Label>
              <Input
                ref={fileInputRef}
                type="file"
                accept=".doc,.docx"
                onChange={handleDocxFileChange}
              />
              {sampleFile && <p className="break-all text-xs text-muted-foreground">已选择：{sampleFile.name}</p>}
              {extracting && (
                <div className="flex items-center justify-between gap-2">
                  <p role="status" className="text-sm text-muted-foreground animate-pulse">正在解析文档…</p>
                  <Button type="button" variant="ghost" size="sm" onClick={cancelSampleParse}>取消等待</Button>
                </div>
              )}
              {docxText && (
                <p className="text-sm text-green-600">
                  文档已解析（{docxText.length} 字符）
                </p>
              )}
            </div>
          </fieldset>
          {creating && <p role="status" className="text-sm text-muted-foreground">
            {saveStep === "sample"
              ? "模板草稿已保存，正在保存输出样例…"
              : "正在保存模板…"}
          </p>}
          {sampleSaved && <p role="status" className="text-sm text-green-600">
            模板和输出样例已保存，正在打开模板定义。也可点击下方入口直接打开，或返回列表稍后编辑。
          </p>}
          {uploadError && <p role="alert" className="break-words text-sm text-destructive">{uploadError}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={creating} onClick={() => handleUploadOpenChange(false)}>
              {createdDraft ? "返回列表" : "取消"}
            </Button>
            {sampleSaved && createdDraft ? <Button asChild>
              <a href={`/settings/ast-templates/${createdDraft.id}?tab=template&created=1`}>打开已保存的模板</a>
            </Button> : <Button
              onClick={handleEnterCreateEditor}
              disabled={
                creating ||
                !sampleContent || !sampleFile ||
                !uploadName.trim() ||
                !uploadIriPattern ||
                extracting
              }
            >
              {creating ? (saveStep === "sample" ? "正在保存输出样例…" : "正在保存模板…")
                : createdDraft ? "重试保存样例并进入模板定义" : "进入模板定义"}
            </Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
