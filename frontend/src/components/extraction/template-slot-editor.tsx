"use client";

import { FinderDocumentGraphPanel, FinderOriginal } from "@/components/analysis/finder-document-graph-panel";
import { useTemplateFinder } from "@/components/analysis/use-template-finder";

import {
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  GripVertical,
  Trash2,
  FileText,
  LayoutTemplate,
  Eye,
  Info,
  Loader2,
  Upload,
  Save,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectGroup,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { cn } from "@/lib/utils";
import { extractionCapabilities } from "@/lib/extraction-capabilities";

import { WordViewer } from "./word-viewer";
import { RelationPanel } from "./relation-panel";
import { TemplateDocumentGraphPanel } from "@/components/analysis/template-document-graph-panel";
import { useTemplateDocumentRun } from "@/components/analysis/use-template-document-run";

import {
  getAnnotatedDocument,
  getExtractionJob,
  getCoverageDocClasses,
  listDocuments,
  updateAstTemplateMeta,
  uploadTemplateSample,
  uploadDefaultSource,
  deleteDefaultSource,
  listTrainingPairs,
  uploadTrainingPair,
  deleteTrainingPair,
  type TiptapContent,
  type EntityShadow,
  type DocClassification,
  type Relationship,
  type TrainingPairDTO,
  type AstTemplateStatus,
  type EvidenceAnchor,
  DOCUMENT_TYPE_GROUPS,
} from "@/lib/api";

interface TemplateSlotEditorProps {
  recognitionMode?: "ontology_guided" | "finder_legacy";
  sampleText?: string | null;
  // 013: 忠于原文结构的 tiptap 样例（重新编辑时由 page 从 sample_content_json 载入，
  // 创建时由 parse-sample 直接传入），供左侧 WordViewer 忠实预览与结构锚点联动。
  sampleContentJson?: TiptapContent | null;
  // 015 源文档页签：模板的功能性文档类解析键（doc-class resolution key）。用于左侧
  // 「IRI 匹配文档」区标注当前匹配模式，并驱动 listDocuments() 拉取真实匹配文档列表。
  iriPattern?: string | null;
  initialTab?: "basic" | "template";
  // 015 基本信息页签：仅编辑模式（templateId 存在）渲染。meta 为模板标识/责任信息，
  // onMetaSaved 在保存元数据 / 替换示例 / 增删训练对后触发 page 重新拉取。
  templateId?: string;
  meta?: TemplateMeta;
  versions?: TemplateVersionEntry[];
  onVersionSwitch?: (id: string) => void;
  onMetaSaved?: () => void;
  // Definition authoring and report execution are supplied by the V2 editor.
  outputEditor: {
    actions: ReactNode;
    sidebar: ReactNode;
    basicInfo: ReactNode;
    documentClassIri?: string;
    documentNo?: string;
    onDocumentNoChange?: (value: string) => void;
    onDocumentClassChange?: (iri: string) => void;
    reportPreview: (sourceJobId: string | null) => ReactNode;
    onSourceSelection?: (sourceJobId: string | null) => void;
    sampleAnchor?: EvidenceAnchor | null;
  };
}

export interface TemplateVersionEntry {
  id: string;
  version: string;
  created_at: string;
}

// 015 基本信息表单模型（由 page 从模板详情构造）。
export interface TemplateMeta {
  name: string;
  docNo: string | null;
  version: string;
  status: AstTemplateStatus;
  iriPattern: string | null;
  owner: string | null;
  updatedAt: string | null;
  defaultSourceFilename: string | null;
  defaultSourceJobId: string | null;
  sampleConfigured: boolean;
}

// 旧模板仅存扁平 sample_text 时，按行包装成最小 tiptap 文档（段落），
// 使 legacy 模板在左侧预览中也有段落级 evidence 高亮联动。
// 保留连续空行为空段落、保留行内缩进/空白（WordViewer 以 pre-wrap 渲染）。
function wrapTextAsTiptap(text: string): TiptapContent {
  const lines = text.split("\n");
  return {
    type: "doc",
    content: lines.map((line) =>
      line.trim()
        ? { type: "paragraph", content: [{ type: "text", text: line }] }
        : { type: "paragraph" },
    ),
  };
}

// IRI/类 IRI 的本地名（末段），用于源文档列表的次要标注。
function docLocalName(iri: string | null | undefined): string {
  if (!iri) return "";
  const parts = iri.split("#").flatMap((part) => part.split("/")).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : iri;
}

// 源文档正文解析：文档个体本身不一定携带抽取 jobId，正文只能按 jobId 经
// getAnnotatedDocument 取回。故从个体 properties_json 探测 job 引用，取不到则
// 优雅降级为「不可预览」（绝不抛错）——与 reading-pane.resolveDocumentContent 同源。
const DOC_JOB_KEYS = ["job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob"];
function docJobRef(shadow: EntityShadow | undefined): string | null {
  const props = shadow?.properties_json ?? {};
  for (const key of DOC_JOB_KEYS) {
    const value = props[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

// 选中真实文档的正文预览状态机（源文档页签）。ready 态同时携带文档分类与关系，
// 供右侧「关系图谱」面板（RelationPanel）复用同一次 getAnnotatedDocument 结果。
type DocPreviewState =
  | { kind: "empty" }
  | { kind: "loading" }
  | {
      kind: "ready";
      content: TiptapContent;
      docClass: DocClassification | null;
      relationships: Relationship[];
      previewOnly?: boolean;
    }
  | { kind: "unavailable" };

const DEFAULT_SOURCE_IRI = "__default_source__";

// 右栏（输出定义 / 关系图谱）默认宽度（px），与原固定的 w-[26rem] 一致；可拖动调整。
const DEFAULT_RIGHT_WIDTH = 416;

export function TemplateSlotEditor({
  sampleText,
  sampleContentJson,
  iriPattern,
  initialTab,
  templateId,
  meta,
  versions,
  onVersionSwitch,
  onMetaSaved,
  outputEditor,
  recognitionMode = "ontology_guided",
}: TemplateSlotEditorProps) {
  const finderMode = recognitionMode === "finder_legacy";

  // ── 015 源文档页签：IRI 匹配文档列表 + 选中文档正文预览 ─────────────────
  // 拉取全部文档个体（listDocuments），再按模板 iri_pattern 客户端子串过滤 class_iri
  // ——与后端模板解析语义一致（ast_template.resolve：iri_pattern in doc_class_iri）。
  // 选中真实文档后按其 job 引用取回正文（getAnnotatedDocument），取不到则如实降级。
  // 空/失败按气隙常态静默（不作错误呈现）。
  // 左侧多页签受控值（提升到组件级）：右侧面板据此在「关系图谱」（源文档页签）
  // 与输出模板定义间切换。
  // 新模板直接展示输出样例和定义，保存入口在所有页签上方保留。
  const [leftTab, setLeftTab] = useState<string>(initialTab ?? (templateId ? "basic" : "template"));
  const [reportOpened, setReportOpened] = useState(false);

  // ── 015 基本信息页签：元数据编辑 + 文档配置 + 训练数据 ─────────────────
  const [metaForm, setMetaForm] = useState(() => ({
    name: meta?.name ?? "",
    docNo: meta?.docNo ?? "",
    owner: meta?.owner ?? "",
    status: (meta?.status ?? "draft") as AstTemplateStatus,
    iriPattern: meta?.iriPattern ?? "",
  }));
  const [metaSaving, setMetaSaving] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [metaJustSaved, setMetaJustSaved] = useState(false);
  const [sampleConfigured, setSampleConfigured] = useState(!!meta?.sampleConfigured);
  const [sourceFilename, setSourceFilename] = useState<string | null>(
    meta?.defaultSourceFilename ?? null,
  );
  const [sourceJobId, setSourceJobId] = useState<string | null>(
    meta?.defaultSourceJobId ?? null,
  );
  const [docBusy, setDocBusy] = useState<"sample" | "source" | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const [pairs, setPairs] = useState<TrainingPairDTO[]>([]);
  const [pairFormOpen, setPairFormOpen] = useState(false);
  const [pairSrc, setPairSrc] = useState<File | null>(null);
  const [pairRep, setPairRep] = useState<File | null>(null);
  const [pairBusy, setPairBusy] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);
  const sampleInputRef = useRef<HTMLInputElement>(null);
  const sourceInputRef = useRef<HTMLInputElement>(null);
  const pairSrcInputRef = useRef<HTMLInputElement>(null);
  const pairRepInputRef = useRef<HTMLInputElement>(null);

  const metaDirty =
    !!meta &&
    (metaForm.name.trim() !== (meta.name ?? "") ||
      metaForm.docNo.trim() !== (meta.docNo ?? "") ||
      metaForm.owner.trim() !== (meta.owner ?? "") ||
      metaForm.status !== meta.status ||
      metaForm.iriPattern !== (meta.iriPattern ?? ""));

  // 父级 refetch 后 meta prop 更新 → 同步本地表单与文档配置状态（保存中不覆盖）。
  const [previousMeta, setPreviousMeta] = useState(meta);
  if (meta !== previousMeta) {
    setPreviousMeta(meta);
    if (meta && !metaSaving) {
      setMetaForm({
        name: meta.name ?? "",
        docNo: meta.docNo ?? "",
        owner: meta.owner ?? "",
        status: (meta.status ?? "draft") as AstTemplateStatus,
        iriPattern: meta.iriPattern ?? "",
      });
      setSampleConfigured(!!meta.sampleConfigured);
      setSourceFilename(meta.defaultSourceFilename ?? null);
      setSourceJobId(meta.defaultSourceJobId ?? null);
    }
  }

  // 载入训练对（仅编辑模式）。气隙常态：失败静默为空列表。
  useEffect(() => {
    if (!templateId) return;
    let alive = true;
    listTrainingPairs(templateId)
      .then((res) => alive && setPairs(res))
      .catch(() => alive && setPairs([]));
    return () => {
      alive = false;
    };
  }, [templateId]);

  const handleSaveMeta = useCallback(async () => {
    if (!templateId) return;
    if (!metaForm.name.trim()) {
      setMetaError("模板名称不能为空");
      return;
    }
    if (!metaForm.iriPattern) {
      setMetaError("请选择关联文档类型（必选）——它绑定本体图谱并驱动 AI 分析");
      return;
    }
    setMetaSaving(true);
    setMetaError(null);
    setMetaJustSaved(false);
    try {
      await updateAstTemplateMeta(templateId, {
        name: metaForm.name.trim(),
        owner: metaForm.owner.trim() || null,
      });
      setMetaJustSaved(true);
      onMetaSaved?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setMetaError(
        msg.includes("409") ? "该名称+版本已被占用，请改用其他名称" : "保存失败，请重试",
      );
    } finally {
      setMetaSaving(false);
    }
  }, [templateId, metaForm, onMetaSaved]);

  const handleSampleFile = useCallback(
    async (file: File | null | undefined) => {
      if (!file || !templateId) return;
      setDocBusy("sample");
      setDocError(null);
      try {
        await uploadTemplateSample(templateId, file);
        setSampleConfigured(true);
        onMetaSaved?.();
      } catch {
        setDocError("示例文档替换失败（需 .doc / .docx）");
      } finally {
        setDocBusy(null);
      }
    },
    [templateId, onMetaSaved],
  );

  const handleSourceFile = useCallback(
    async (file: File | null | undefined) => {
      if (!file || !templateId) return;
      setDocBusy("source");
      setDocError(null);
      try {
        const res = await uploadDefaultSource(templateId, file);
        setSourceFilename(res.default_source_filename);
        setSourceJobId(res.default_source_job_id);
        onMetaSaved?.();
      } catch {
        setDocError("源文件上传失败");
      } finally {
        setDocBusy(null);
      }
    },
    [templateId, onMetaSaved],
  );

  const handleSourceDelete = useCallback(async () => {
    if (!templateId) return;
    setDocBusy("source");
    setDocError(null);
    try {
      await deleteDefaultSource(templateId);
      setSourceFilename(null);
      setSourceJobId(null);
      onMetaSaved?.();
    } catch {
      setDocError("源文件移除失败");
    } finally {
      setDocBusy(null);
    }
  }, [templateId, onMetaSaved]);

  const handleUploadPair = useCallback(async () => {
    if (!templateId || !pairSrc) return;
    setPairBusy(true);
    setPairError(null);
    try {
      await uploadTrainingPair(templateId, pairSrc, pairRep);
      const fresh = await listTrainingPairs(templateId);
      setPairs(fresh);
      setPairFormOpen(false);
      setPairSrc(null);
      setPairRep(null);
      onMetaSaved?.();
    } catch {
      setPairError("训练对上传失败");
    } finally {
      setPairBusy(false);
    }
  }, [templateId, pairSrc, pairRep, onMetaSaved]);

  const handleDeletePair = useCallback(
    async (pairId: string) => {
      if (!templateId) return;
      try {
        await deleteTrainingPair(templateId, pairId);
        setPairs((prev) => prev.filter((p) => p.id !== pairId));
        onMetaSaved?.();
      } catch {
        /* 气隙常态：删除失败静默，下次拉取自愈 */
      }
    },
    [templateId, onMetaSaved],
  );

  const [docs, setDocs] = useState<EntityShadow[]>([]);
  // 用户显式点选的文档（可能失效于匹配集变化）；有效选中项按 render 派生（见 activeDocIri）。
  const [selectedDocIri, setSelectedDocIri] = useState<string | null>(null);
  // 关系图谱 ↔ 正文预览联动锚点：点击关系端点高亮/滚动到原文（与查看标注 drawer 同源）。
  const [selectedSourceRef, setSelectedSourceRef] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    listDocuments()
      .then((res) => alive && setDocs(res.items ?? []))
      .catch(() => alive && setDocs([]));
    return () => {
      alive = false;
    };
  }, []);

  // iri_pattern 子串匹配 class_iri（与后端解析一致）；无 pattern 时不过滤。
  const matchedDocs = useMemo(() => {
    const pat = iriPattern?.trim();
    if (!pat) return docs;
    return docs.filter((d) => finderMode ? d.class_iri === pat : d.class_iri?.includes(pat));
  }, [docs, iriPattern, finderMode]);

  // 保留有效的手动选择；模板已有默认源时优先展示它，避免跳到另一份 IRI 匹配文档。
  const activeDocIri = useMemo(
    () =>
      selectedDocIri === DEFAULT_SOURCE_IRI && sourceJobId
        ? DEFAULT_SOURCE_IRI
        : selectedDocIri && matchedDocs.some((d) => d.iri === selectedDocIri)
          ? selectedDocIri
          : !templateId || initialTab === "template" ? null
            : sourceJobId ? DEFAULT_SOURCE_IRI : matchedDocs[0]?.iri ?? null,
    [selectedDocIri, matchedDocs, templateId, initialTab, sourceJobId],
  );
  const activeShadow = useMemo(
    () => (activeDocIri ? docs.find((d) => d.iri === activeDocIri) ?? null : null),
    [activeDocIri, docs],
  );

  const previewJobId = activeDocIri === DEFAULT_SOURCE_IRI
    ? sourceJobId : docJobRef(activeShadow ?? undefined);
  const onSourceSelection = outputEditor.onSourceSelection;
  useEffect(() => { onSourceSelection?.(previewJobId ?? sourceJobId); }, [onSourceSelection, previewJobId, sourceJobId]);
  const sourceJobQuery = useQuery({
    queryKey: ["extraction-job", previewJobId],
    queryFn: ({ signal }) => getExtractionJob(previewJobId!, signal),
    enabled: !!previewJobId && !finderMode,
  });
  const sourceCapabilities = extractionCapabilities(sourceJobQuery.data);
  const canRecognizeSource = !!templateId && !!previewJobId && !finderMode;
  const documentRun = useTemplateDocumentRun(templateId, previewJobId, leftTab === "source" && !finderMode);
  const finder = useTemplateFinder(templateId, previewJobId, leftTab === "source" && finderMode);
  const runSource = documentRun.source?.recognition_run_id === documentRun.run?.recognition_run_id
    && documentRun.source?.analysis_id === documentRun.run?.identities.analysis_id
    ? documentRun.source : null;

  // 选中真实文档 → 按 job 引用取回正文（尽力而为，降级为「不可预览」；绝不抛错）。
  // queryKey 包含 sourceJobId：DEFAULT_SOURCE_IRI 的预览依赖它；变化时自动重取。
  const docContentQuery = useQuery({
    queryKey: ["ast-source-doc-content", activeDocIri, activeDocIri === DEFAULT_SOURCE_IRI ? sourceJobId : null],
    enabled: Boolean(activeDocIri) && sourceCapabilities.preview && !finderMode,
    queryFn: async ({ signal }): Promise<DocPreviewState> => {
      const jobRef =
        activeDocIri === DEFAULT_SOURCE_IRI
          ? sourceJobId
          : docJobRef(activeShadow ?? undefined);
      if (!jobRef) return { kind: "unavailable" };
      try {
        const doc = await getAnnotatedDocument(jobRef, false, signal);
        if (doc.content && typeof doc.content === "object") {
          return {
            kind: "ready",
            content: doc.content as TiptapContent,
            docClass: doc.doc_class ?? null,
            relationships: doc.relationships ?? [],
            previewOnly: doc.preview_only,
          };
        }
      } catch (error) {
        if (signal.aborted) throw error;
        return { kind: "unavailable" };
      }
      return { kind: "unavailable" };
    },
  });
  const docContent: DocPreviewState = !activeDocIri
    ? { kind: "empty" }
    : sourceJobQuery.isLoading || docContentQuery.isLoading
      ? { kind: "loading" }
      : docContentQuery.data ?? { kind: "unavailable" };

  // 切换选中文档时清除关系高亮锚点（新文档的 source_ref 不适用于旧选择）。
  // 渲染期修正（React 认可的「随 prop 变化调整 state」模式，免 effect 级联渲染）。
  const [refDocIri, setRefDocIri] = useState<string | null>(activeDocIri);
  if (refDocIri !== activeDocIri) {
    setRefDocIri(activeDocIri);
    setSelectedSourceRef(null);
  }

  // 右侧关系图谱数据：复用同一次正文取回结果（ready 态）；否则空。
  const sourceDocClass =
    docContent.kind === "ready" ? docContent.docClass : null;
  const sourceRelationships =
    docContent.kind === "ready" ? docContent.relationships : [];

  // 016（仅启用已建模类型）：一次性问询全部候选文档类型中「已建模可覆盖关系」（≥1 条
  // hop-1 边）的子集，门控「关联文档类型」下拉与 AI 分析。查询失败/加载中 → capableSet=null
  // → 视为全部可用（离线优雅降级，Principle VI：绝不因探测失败而阻断作者化）。
  const allDocTypeIris = useMemo(
    () => DOCUMENT_TYPE_GROUPS.flatMap((g) => g.options.map((o) => o.iri)),
    [],
  );
  const coverageCapableQuery = useQuery({
    queryKey: ["coverage-doc-classes"],
    queryFn: () => getCoverageDocClasses(allDocTypeIris),
    staleTime: 5 * 60 * 1000,
  });
  const capableSet = useMemo(
    () =>
      coverageCapableQuery.data
        ? new Set(coverageCapableQuery.data.capable)
        : null,
    [coverageCapableQuery.data],
  );
  // 已建模类型的显示标签（供下拉提示行；capableSet 未知时为 null → 不显示具体清单）。
  const capableLabels = useMemo(
    () =>
      capableSet
        ? DOCUMENT_TYPE_GROUPS.flatMap((g) => g.options)
            .filter((o) => capableSet.has(o.iri))
            .map((o) => o.label)
        : null,
    [capableSet],
  );

  // 优先展示样例文档；仅提供 sample_text 时按行展示，缺省时显示占位。
  const previewContent: TiptapContent | null = useMemo(() => {
    if (sampleContentJson) return sampleContentJson;
    if (sampleText) return wrapTextAsTiptap(sampleText);
    return null;
  }, [sampleContentJson, sampleText]);

  // ── 左右两栏可调宽：在「预览」与右侧「输出定义 / 关系图谱」区之间拖动把手调宽度。
  // 右栏宽度受控（px），由「容器右边界 − 鼠标 X」求得，左栏至少保留 ~360px；右栏
  // 下限 320px。基本信息 / 报告预览两页签无右栏，不渲染把手。
  const containerRef = useRef<HTMLDivElement>(null);
  const [rightWidth, setRightWidth] = useState(DEFAULT_RIGHT_WIDTH);
  const [resizing, setResizing] = useState(false);
  const hasRightPanel = leftTab !== "basic" && leftTab !== "report-preview";

  const startResize = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    setResizing(true);
    const onMove = (ev: MouseEvent) => {
      const raw = rect.right - ev.clientX;
      const max = Math.max(320, rect.width - 360);
      setRightWidth(Math.min(max, Math.max(320, raw)));
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);
  const resetRightWidth = useCallback(
    () => setRightWidth(DEFAULT_RIGHT_WIDTH),
    [],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {outputEditor.actions}
    <div ref={containerRef} className="flex flex-1 min-h-0">
      {/* ── Left: 多页签预览面板 ──────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col">
        <Tabs value={leftTab} onValueChange={(value) => {
          setLeftTab(value);
          if (value === "report-preview") setReportOpened(true);
        }} className="flex flex-col h-full">
          <div className="shrink-0 border-b px-4 pt-2">
            <TabsList aria-label="模板工作区" className="h-8">
              {templateId && (
                <TabsTrigger value="basic" className="gap-1.5 text-xs">
                  <Info className="size-3.5" />
                  基本信息
                </TabsTrigger>
              )}
              <TabsTrigger value="source" className="gap-1.5 text-xs">
                <FileText className="size-3.5" />
                源文档
              </TabsTrigger>
              <TabsTrigger value="template" className="gap-1.5 text-xs">
                <LayoutTemplate className="size-3.5" />
                AST模板定义
              </TabsTrigger>
              <TabsTrigger value="report-preview" className="gap-1.5 text-xs">
                <Eye className="size-3.5" />
                报告预览
              </TabsTrigger>
            </TabsList>
          </div>

          {templateId && meta && (
            <TabsContent
              value="basic"
              className="mt-0 flex-1 min-h-0 overflow-y-auto"
            >
              <div className="mx-auto w-full max-w-[940px] space-y-6 px-8 py-6">
                {/* 隐藏文件选择器 */}
                <input
                  ref={sampleInputRef}
                  type="file"
                  accept=".doc,.docx"
                  className="hidden"
                  onChange={(e) => {
                    void handleSampleFile(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
                <input
                  ref={sourceInputRef}
                  type="file"
                  accept=".doc,.docx,.xlsx,.xls"
                  className="hidden"
                  onChange={(e) => {
                    void handleSourceFile(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />

                {/* ── 基本信息 ─────────────────────────────────────────── */}
                <section className="space-y-3.5">
                  <div className="flex items-start justify-between">
                    <div className="flex flex-col gap-0.5">
                      <h3 className="text-[15px] font-semibold text-foreground">
                        基本信息
                      </h3>
                      <p className="text-xs text-muted-foreground">
                        AST 模板的标识与责任信息
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {metaJustSaved && !metaDirty && (
                        <span className="text-xs text-emerald-600 dark:text-emerald-400">
                          已保存
                        </span>
                      )}
                      <Button
                        size="sm"
                        onClick={handleSaveMeta}
                        disabled={metaSaving || !metaDirty}
                      >
                        {metaSaving ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Save className="size-3.5" />
                        )}
                        保存基本信息
                      </Button>
                    </div>
                  </div>

                  {metaError && (
                    <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                      {metaError}
                    </div>
                  )}

                  <div className="grid grid-cols-3 gap-4">
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">模板名称</Label>
                      <Input
                        value={metaForm.name}
                        aria-label="模板名称"
                        onChange={(e) =>
                          setMetaForm((f) => ({ ...f, name: e.target.value }))
                        }
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">模板编号</Label>
                      <Input
                        value={outputEditor.documentNo ?? metaForm.docNo}
                        aria-label="模板编号"
                        onChange={(e) => {
                          if (outputEditor.onDocumentNoChange) outputEditor.onDocumentNoChange(e.target.value);
                          else setMetaForm((f) => ({ ...f, docNo: e.target.value }));
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">版本</Label>
                      {versions && versions.length > 1 && onVersionSwitch ? (
                        <Select
                          value={templateId}
                          onValueChange={(id) => {
                            if (id !== templateId) onVersionSwitch(id);
                          }}
                        >
                          <SelectTrigger aria-label="版本">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {versions.map((v) => (
                              <SelectItem key={v.id} value={v.id}>
                                {v.version}
                                {v.id === templateId ? "（当前）" : ""}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input value={meta.version} disabled readOnly />
                      )}
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">责任人</Label>
                      <Input
                        value={metaForm.owner}
                        aria-label="责任人"
                        onChange={(e) =>
                          setMetaForm((f) => ({ ...f, owner: e.target.value }))
                        }
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">状态</Label>
                      <Select
                        value={metaForm.status}
                        disabled
                        onValueChange={(v) =>
                          setMetaForm((f) => ({ ...f, status: v as AstTemplateStatus }))
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="draft">草稿</SelectItem>
                          <SelectItem value="published">已发布</SelectItem>
                          <SelectItem value="archived">已归档</SelectItem>
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        在「AST模板定义」校验语义后发布新修订。
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">最近更新</Label>
                      <Input
                        value={meta.updatedAt ? meta.updatedAt.slice(0, 10) : "—"}
                        disabled
                        readOnly
                      />
                    </div>
                    <div className="col-span-2 space-y-1.5">
                      <Label className="text-xs text-muted-foreground">
                        关联文档类型 <span className="text-destructive">*</span>
                      </Label>
                      <Select
                        value={(outputEditor.documentClassIri ?? metaForm.iriPattern) || undefined}
                        onValueChange={(v) => {
                          if (outputEditor.onDocumentClassChange) outputEditor.onDocumentClassChange(v);
                          else setMetaForm((f) => ({ ...f, iriPattern: v }));
                        }}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="选择文档类型…" />
                        </SelectTrigger>
                        <SelectContent>
                          {DOCUMENT_TYPE_GROUPS.map((g) => (
                            <SelectGroup key={g.group}>
                              <SelectLabel>{g.group}</SelectLabel>
                              {g.options.map((o) => {
                                // 016：仅启用「已建模本体关系」的类型；capableSet 未知
                                // （加载/失败）时不禁用（优雅降级）。
                                const modeled = !capableSet || capableSet.has(o.iri);
                                return (
                                  <SelectItem
                                    key={o.iri}
                                    value={o.iri}
                                    disabled={!modeled}
                                  >
                                    {o.label}
                                    {!modeled && (
                                      <span className="ml-1 text-xs text-muted-foreground">
                                        （暂未建模）
                                      </span>
                                    )}
                                  </SelectItem>
                                );
                              })}
                            </SelectGroup>
                          ))}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        仅「已建模本体关系」的类型可用于{"语义绑定与来源匹配"}
                        {capableLabels && capableLabels.length > 0
                          ? `（当前：${capableLabels.join("、")}）`
                          : ""}
                        ；其余类型待本体补充关系后自动启用。
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">IRI 匹配键</Label>
                      <Input
                        value={(outputEditor.documentClassIri ?? metaForm.iriPattern) ? docLocalName(outputEditor.documentClassIri ?? metaForm.iriPattern) : "—"}
                        disabled
                        readOnly
                        className="text-muted-foreground"
                      />
                    </div>
                  </div>
                </section>

                {outputEditor.basicInfo}

                {/* ── 文档配置 ─────────────────────────────────────────── */}
                <section className="grid grid-cols-2 gap-4">
                  {docError && (
                    <div className="col-span-2 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                      {docError}
                    </div>
                  )}
                  {/* 默认源文件 */}
                  <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                    <div className="flex flex-col gap-0.5">
                      <div className="text-sm font-semibold text-foreground">
                        关联文档类型的源文件
                      </div>
                      <p className="text-xs text-muted-foreground">
                        关联文档类型的参照原件
                      </p>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2 rounded-md bg-muted px-2.5 py-2">
                        <FileText className="size-4 shrink-0 text-primary" />
                        <span className="truncate text-xs font-medium text-foreground">
                          {sourceFilename ?? "未上传"}
                        </span>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        {sourceFilename && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-8 text-muted-foreground"
                            onClick={handleSourceDelete}
                            disabled={docBusy === "source"}
                            title="移除"
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => sourceInputRef.current?.click()}
                          disabled={docBusy === "source"}
                        >
                          {docBusy === "source" ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Upload className="size-3.5" />
                          )}
                          {sourceFilename ? "替换" : "上传"}
                        </Button>
                      </div>
                    </div>
                  </div>
                  {/* 默认模板示例文档 */}
                  <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                    <div className="flex flex-col gap-0.5">
                      <div className="text-sm font-semibold text-foreground">
                        默认输出模板示例文档
                      </div>
                      <p className="text-xs text-muted-foreground">
                        用于固化输出 section 结构与排版格式
                      </p>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 rounded-md bg-muted px-2.5 py-2">
                        <LayoutTemplate className="size-4 text-primary" />
                        <span className="text-xs font-medium text-foreground">
                          {sampleConfigured ? "已配置示例文档" : "未配置"}
                        </span>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => sampleInputRef.current?.click()}
                        disabled={docBusy === "sample"}
                      >
                        {docBusy === "sample" ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Upload className="size-3.5" />
                        )}
                        {sampleConfigured ? "替换" : "上传"}
                      </Button>
                    </div>
                  </div>
                </section>

                {/* ── 训练数据 ─────────────────────────────────────────── */}
                <section className="space-y-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex flex-col gap-0.5">
                      <h3 className="text-[15px] font-semibold text-foreground">
                        训练数据
                      </h3>
                      <p className="text-xs text-muted-foreground">
                        源文档 → 评估报告 配对，用于学习深层评估语义
                      </p>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => {
                        setPairError(null);
                        setPairFormOpen((v) => !v);
                      }}
                    >
                      <Upload className="size-3.5" />
                      上传训练对
                    </Button>
                  </div>

                  {/* 内联上传器：源文档必填，评估报告可选 */}
                  {pairFormOpen && (
                    <div className="space-y-2 rounded-lg border bg-muted/40 p-3">
                      <input
                        ref={pairSrcInputRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                          setPairSrc(e.target.files?.[0] ?? null);
                          e.target.value = "";
                        }}
                      />
                      <input
                        ref={pairRepInputRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                          setPairRep(e.target.files?.[0] ?? null);
                          e.target.value = "";
                        }}
                      />
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">
                            源文档 <span className="text-destructive">*</span>
                          </Label>
                          <Button
                            variant="outline"
                            size="sm"
                            className="w-full justify-start font-normal"
                            onClick={() => pairSrcInputRef.current?.click()}
                          >
                            <FileText className="size-3.5 shrink-0" />
                            <span className="truncate">
                              {pairSrc ? pairSrc.name : "选择源文档…"}
                            </span>
                          </Button>
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">
                            评估报告（必选）
                          </Label>
                          <Button
                            variant="outline"
                            size="sm"
                            className="w-full justify-start font-normal"
                            onClick={() => pairRepInputRef.current?.click()}
                          >
                            <FileText className="size-3.5 shrink-0" />
                            <span className="truncate">
                              {pairRep ? pairRep.name : "选择评估报告…"}
                            </span>
                          </Button>
                        </div>
                      </div>
                      {pairError && (
                        <div className="text-xs text-destructive">{pairError}</div>
                      )}
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setPairFormOpen(false);
                            setPairSrc(null);
                            setPairRep(null);
                          }}
                        >
                          取消
                        </Button>
                        <Button
                          size="sm"
                          onClick={handleUploadPair}
                          disabled={!pairSrc || pairBusy}
                        >
                          {pairBusy ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Upload className="size-3.5" />
                          )}
                          上传
                        </Button>
                      </div>
                    </div>
                  )}

                  {/* 训练对表 */}
                  <div className="overflow-hidden rounded-lg border">
                    <div className="flex gap-3 bg-muted px-3.5 py-2.5 text-xs font-semibold text-muted-foreground">
                      <div className="flex-1">源文档</div>
                      <div className="flex-1">评估报告</div>
                      <div className="w-24">状态</div>
                      <div className="w-16 text-right">操作</div>
                    </div>
                    {pairs.length === 0 ? (
                      <div className="px-3.5 py-8 text-center text-sm text-muted-foreground">
                        暂无训练数据。点击「上传训练对」添加源文档→评估报告样例。
                      </div>
                    ) : (
                      pairs.map((p) => (
                        <div
                          key={p.id}
                          className="flex items-center gap-3 px-3.5 py-2.5 text-xs [&:not(:last-child)]:border-b"
                        >
                          <div className="flex flex-1 items-center gap-1.5">
                            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="truncate text-foreground">
                              {p.source_filename}
                            </span>
                          </div>
                          <div className="flex-1 truncate text-foreground">
                            {p.report_filename ?? (
                              <span className="text-muted-foreground">— 待生成</span>
                            )}
                          </div>
                          <div className="w-24">
                            {p.report_filename ? (
                              <Badge
                                variant="outline"
                                className="border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400"
                              >
                                已配对
                              </Badge>
                            ) : (
                              <Badge
                                variant="outline"
                                className="border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-400"
                              >
                                待处理
                              </Badge>
                            )}
                          </div>
                          <div className="flex w-16 justify-end">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 text-muted-foreground hover:text-destructive"
                              onClick={() => handleDeletePair(p.id)}
                              title="删除"
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </section>
              </div>
            </TabsContent>
          )}

          <TabsContent
            value="source"
            className="mt-0 flex-1 min-h-0 overflow-hidden"
          >
            {/* 左右结构：IRI 匹配文档列表（定宽） | 文档内容预览（充满） */}
            <div className="flex h-full min-h-0">
              {/* ── 左列：IRI 匹配文档列表 ─────────────────────────── */}
              <div className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-r px-4 py-4">
                {/* 默认源文件（固化输出格式的参照原件） */}
                {templateId && sourceFilename && (
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-semibold text-foreground">
                      默认源文件
                    </span>
                    <div className="overflow-hidden rounded-lg border">
                      <button
                        type="button"
                        onClick={() => setSelectedDocIri(DEFAULT_SOURCE_IRI)}
                        className={cn(
                          "flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors",
                          activeDocIri === DEFAULT_SOURCE_IRI
                            ? "border-l-[3px] border-primary bg-accent"
                            : "border-l-[3px] border-transparent hover:bg-muted/50",
                        )}
                      >
                        <FileText className="size-4 shrink-0 text-primary" />
                        <div className="min-w-0 flex-1">
                          <div
                            className={cn(
                              "truncate text-[13px] text-foreground",
                              activeDocIri === DEFAULT_SOURCE_IRI ? "font-semibold" : "font-normal",
                            )}
                          >
                            {sourceFilename}
                          </div>
                        </div>
                      </button>
                    </div>
                  </div>
                )}

                {/* IRI 匹配文档 —— 标题 + 当前解析键 */}
                <div className="flex flex-col gap-1">
                  <span className="text-sm font-semibold text-foreground">
                    IRI 匹配文档
                  </span>
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    iri_pattern: {iriPattern?.trim() ? iriPattern : "—"}
                  </span>
                </div>

              {/* 文档列表：按 iri_pattern 匹配 class_iri 的真实文档个体 */}
              <div className="overflow-hidden rounded-lg border">
                {matchedDocs.map((d) => {
                  const active = activeDocIri === d.iri;
                  const name = d.label_zh || d.label_en || docLocalName(d.iri);
                  const kind = docLocalName(d.class_iri) || d.module;
                  return (
                    <button
                      key={d.iri}
                      type="button"
                      onClick={() => setSelectedDocIri(d.iri)}
                      className={cn(
                        "flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors [&:not(:first-child)]:border-t",
                        active
                          ? "border-l-[3px] border-primary bg-accent"
                          : "border-l-[3px] border-transparent hover:bg-muted/50",
                      )}
                    >
                      <FileText className="size-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <div
                          className={cn(
                            "truncate text-[13px] text-foreground",
                            active ? "font-semibold" : "font-normal",
                          )}
                        >
                          {name}
                        </div>
                        <div className="flex gap-3 text-xs text-muted-foreground">
                          {kind && <span>{kind}</span>}
                          {d.label_en && d.label_en !== name && (
                            <span className="truncate">{d.label_en}</span>
                          )}
                        </div>
                      </div>
                    </button>
                  );
                })}

                {matchedDocs.length === 0 && (
                  <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                    {iriPattern?.trim()
                      ? "暂无与该模板 IRI 模式匹配的文档。"
                      : "该模板未设置 IRI 模式，暂无可匹配的文档。"}
                  </div>
                )}
              </div>
              </div>

              {/* ── 右列：文档内容预览（选中真实文档的正文，尽力而为）───── */}
              <div className="flex flex-1 min-w-0 flex-col gap-3 overflow-y-auto px-6 py-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">
                    文档内容预览
                  </span>
                  {activeDocIri && activeDocIri !== DEFAULT_SOURCE_IRI && (
                    <span className="max-w-[60%] truncate font-mono text-xs text-muted-foreground">
                      {docLocalName(activeDocIri)}
                    </span>
                  )}
                </div>

                {docContent.kind === "ready" && docContent.previewOnly && (
                  <p className="text-xs text-muted-foreground" role="status">
                    {canRecognizeSource
                      ? "正文已加载。可在关系图谱面板发起识别或查看已有结果。"
                      : "正文已加载。关系图谱及历史任务请在「文档分析」中查看。"}
                  </p>
                )}
                {finderMode ? <FinderOriginal model={finder} documentIri={activeShadow?.iri} /> : <div className="rounded border bg-card p-6 shadow-sm">
                  {runSource || docContent.kind === "ready" ? (
                    <WordViewer
                      key={runSource ? documentRun.sourceIdentity : activeDocIri}
                      content={runSource ? runSource.content as TiptapContent : docContent.kind === "ready" ? docContent.content : { type: "doc", content: [] }}
                      highlightRef={runSource ? null : selectedSourceRef}
                      activeAnchor={runSource ? documentRun.sourceSelection?.anchors[0] ?? null : null}
                    />
                  ) : docContent.kind === "loading" ? (
                    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" />
                      正在加载文档正文…
                    </div>
                  ) : docContent.kind === "unavailable" ? (
                    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                      <Info className="size-6 text-muted-foreground/60" />
                      <p className="text-sm text-muted-foreground">
                        该文档暂无法在线预览正文，请通过原始文档库获取原件。
                      </p>
                    </div>
                  ) : (
                    <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                      从左侧选择一篇文档以预览其正文
                    </div>
                  )}
                </div>}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="template" className="flex-1 min-h-0 overflow-y-auto px-6 py-4 mt-0">
            <div className="flex flex-col gap-4">
              {/* 用户上传的模板样例正文，与输出定义的来源锚点联动 */}
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-foreground">
                  模板样例内容
                </span>
                <span className="text-xs text-muted-foreground">
                  {outputEditor.documentNo || "用户上传样例"}
                </span>
              </div>

              <div className="rounded border bg-card p-6 shadow-sm">
                {previewContent ? (
                  <WordViewer
                    content={previewContent}
                    activeAnchor={outputEditor.sampleAnchor}
                    sourceUnavailable={false}
                    fitTables
                  />
                ) : (
                  <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                    该模板未保存样例内容
                  </div>
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="report-preview" forceMount={reportOpened ? true : undefined}
              className="mt-0 flex-1 min-h-0 overflow-y-auto data-[state=inactive]:hidden">
              {reportOpened && outputEditor.reportPreview(previewJobId ?? sourceJobId)}
          </TabsContent>
        </Tabs>
      </div>

      {/* ── Splitter: 拖动把手调整左右两栏宽度（无右栏的页签不渲染）───────────── */}
      {hasRightPanel && (
        <div
          role="separator"
          aria-orientation="vertical"
          onMouseDown={startResize}
          onDoubleClick={resetRightWidth}
          title="拖动调整宽度 · 双击复位"
          className={cn(
            "group relative w-px shrink-0 cursor-col-resize bg-border transition-colors",
            resizing ? "bg-primary" : "hover:bg-primary/50",
          )}
        >
          {/* 加宽命中区（1px 线太难抓）*/}
          <div className="absolute inset-y-0 -left-1.5 -right-1.5 z-10" />
          {/* 抓手指示（hover / 拖动时可见）*/}
          <div
            className={cn(
              "absolute left-1/2 top-1/2 z-20 flex h-8 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background shadow-sm transition-opacity",
              resizing ? "opacity-100" : "opacity-0 group-hover:opacity-100",
            )}
          >
            <GripVertical className="size-3 text-muted-foreground" />
          </div>
        </div>
      )}

      <aside aria-label="输出模板定义" style={{ width: rightWidth }}
          className={cn("shrink-0 flex-col min-h-0", leftTab === "template" ? "flex" : "hidden")}>
          {outputEditor.sidebar}
      </aside>

      {/* ── Right: 源文档页签显示当前识别引擎的关系图谱 ── */}
      {leftTab === "source" ? (
        <div style={{ width: rightWidth }} className="flex shrink-0 flex-col min-h-0">
          {finderMode ? <FinderDocumentGraphPanel model={finder} /> : previewJobId && templateId ? <TemplateDocumentGraphPanel model={documentRun} /> : (
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <h3 className="mb-3 text-sm font-semibold">关系图谱</h3>
              <RelationPanel docClass={sourceDocClass} relationships={sourceRelationships}
                selectedSourceRef={selectedSourceRef} onSelectSourceRef={setSelectedSourceRef}
                emptyMessage="选择已保存模板的源文档后可开始识别。" />
            </div>
          )}
        </div>
      ) : null}

    </div>
    </div>
  );
}
