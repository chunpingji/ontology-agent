"use client";

import { useState, useCallback, useEffect, useMemo, useRef, type ReactNode, type MouseEvent as ReactMouseEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles, GripVertical, Pencil, Trash2, MoreVertical, FileText, LayoutTemplate, Eye, Info, Loader2, Upload, Save, FileDown, Download, Check, ListTree, GitBranch, ArrowRight, X, Plus, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { Progress } from "@/components/ui/progress";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { WordViewer } from "./word-viewer";
import { RelationPanel } from "./relation-panel";
import { RecognitionTimer } from "./recognition-timer";
import { ModelRequestProgress } from "./model-request-progress";
import { EvidenceReviewPanel } from "./evidence-review-panel";
import { ASTTreeView } from "./ast-tree-view";
import { SlotDetailPanel } from "./slot-detail-panel";
import { SlotActionBar } from "./slot-action-bar";
import { ReportHistoryList } from "./report-history-list";
import {
  suggestSlots,
  generateSectionPrompt,
  previewSectionNarrative,
  getAnnotatedDocument,
  getRelationSchema,
  getCoverageDocClasses,
  listDocuments,
  updateAstTemplateMeta,
  uploadTemplateSample,
  uploadDefaultSource,
  deleteDefaultSource,
  listTrainingPairs,
  uploadTrainingPair,
  deleteTrainingPair,
  type SuggestSlotsRequest,
  type TiptapContent,
  type EntityShadow,
  type DocClassification,
  type Relationship,
  type TrainingPairDTO,
  type AstTemplateStatus,
  type RelationSchemaEdge,
  type CoverageBinding,
  type OntologyRelationBinding,
  type AiStructureSection,
  type TemplateOrigin,
  type EvidenceAnchor,
  coverageKey,
  DOCUMENT_TYPE_GROUPS,
  getAstCoverage,
  listReports,
  dismissSlot,
  undismissSlot,
  generateRiskReport,
  downloadReport,
  pollReportStatus,
  rerunAnnotation,
  pauseAnnotation,
  resumeAnnotation,
  extractJobEvidence,
  type EvidenceExtractOptions,
  subscribeJobProgress,
  type JobProgressEvent,
  type ASTCoverageDTO,
  type SlotCoverageDTO,
  type GeneratedReportDTO,
} from "@/lib/api";

interface SlotDef {
  slot_id: string;
  origin?: TemplateOrigin | null;
  label: string;
  source: Record<string, unknown>;
  required: boolean;
  on_missing: string;
  missing_placeholder: string;
  // 013 融合编辑器：仅编辑器内的视觉标记（灰显、从启用计数中剔除）。持久化进
  // schema_json 并原样回传，但后端 Slot 模型 extra="ignore" 会在报告生成时丢弃它
  // ——即不影响任何已生成/将生成的报告（用户选定语义：仅编辑器内视觉标记）。
  disabled?: boolean;
}

interface GroupDef {
  group_id: string;
  origin?: TemplateOrigin | null;
  title: string;
  kind: string;
  repeat?: Record<string, unknown> | null;
  slots: SlotDef[];
}

interface SectionDef {
  section_id: string;
  origin?: TemplateOrigin | null;
  title: string;
  groups: GroupDef[];
  // 015 行文 Prompt：报告生成时用于把本节插槽值融合成一段叙述文字的提示词。
  // 空/缺失 → 该节不产出叙述（generate_section_narratives 会跳过）。
  prompt?: string | null;
  // 016 本体覆盖声明：section 级 doc-class→predicate→range 的必填(默认)覆盖边。
  // 校验期从只读本体编译无遗漏清单；随 schema_json 原样往返（后端不新增列）。
  coverage?: CoverageBinding[];
}

interface TemplateSchema {
  template_id: string;
  doc_no?: string;
  revision?: string;
  sections: SectionDef[];
}

interface TemplateSlotEditorProps {
  schema: TemplateSchema;
  onSave: (updated: TemplateSchema) => void;
  onCancel: () => void;
  saving?: boolean;
  jobId?: string | null;
  aiEnabled?: boolean;
  sampleText?: string | null;
  // 013: 忠于原文结构的 tiptap 样例（重新编辑时由 page 从 sample_content_json 载入，
  // 创建时由 parse-sample 直接传入），供左侧 WordViewer 忠实预览与结构锚点联动。
  sampleContentJson?: TiptapContent | null;
  // 015 源文档页签：模板的功能性文档类解析键（doc-class resolution key）。用于左侧
  // 「IRI 匹配文档」区标注当前匹配模式，并驱动 listDocuments() 拉取真实匹配文档列表。
  iriPattern?: string | null;
  // 创建与编辑统一为同一视图：create=空骨架 schema 起步，edit=载入已有 schema。
  mode?: "create" | "edit";
  // 015 基本信息页签：仅编辑模式（templateId 存在）渲染。meta 为模板标识/责任信息，
  // onMetaSaved 在保存元数据 / 替换示例 / 增删训练对后触发 page 重新拉取。
  templateId?: string;
  meta?: TemplateMeta;
  versions?: TemplateVersionEntry[];
  onVersionSwitch?: (id: string) => void;
  onMetaSaved?: () => void;
  // V2 keeps the existing workspace, metadata and source review; only definition
  // authoring and report execution are supplied by the output template editor.
  outputEditor?: {
    sidebar: ReactNode;
    basicInfo: ReactNode;
    documentClassIri?: string;
    documentNo?: string;
    onDocumentNoChange?: (value: string) => void;
    onDocumentClassChange?: (iri: string) => void;
    reportPreview: (sourceJobId: string | null) => ReactNode;
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

function cloneSections(sections: SectionDef[]): SectionDef[] {
  return JSON.parse(JSON.stringify(sections)) as SectionDef[];
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

// 从忠于原文结构的 tiptap 文档提取纯文本（供 legacy 无 sample_text 时的行文 Prompt 生成）。
function tiptapToText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const n = node as { type?: string; text?: string; content?: unknown[] };
  if (n.type === "text" && typeof n.text === "string") return n.text;
  const inner = Array.isArray(n.content)
    ? n.content.map(tiptapToText).join("")
    : "";
  // 段落级节点之间补换行，尽量保留原文段落边界。
  return n.type === "paragraph" || n.type === "heading" ? `${inner}\n` : inner;
}

// 插槽来源类型的中文标签（语义化 + 旧式定型）。旧式来源仅在既有插槽上只读显示。
const SOURCE_KIND_LABELS: Record<string, string> = {
  semantic: "语义化",
  extraction: "抽取",
  rule: "规则",
  manual: "手工",
  constant: "常量",
  llm_extraction: "LLM 抽取",
};
const sourceKindLabel = (kind: string): string => SOURCE_KIND_LABELS[kind] ?? kind;
const isLegacySourceKind = (kind: string): boolean => kind !== "semantic";

const DEFAULT_SOURCE_IRI = "__default_source__";

// 右栏（Section / 关系图谱）默认宽度（px），与原固定的 w-[26rem] 一致；可拖动调整。
const DEFAULT_RIGHT_WIDTH = 416;

// ── 渲染模型：真实 sections → 一棵可折叠的 Slot 树。016 收敛后 AI 分析不再产出
// 逐插槽建议，故不再有 pending 幽灵行 / 虚拟容器——树只承载已持久化的真实插槽。
interface RenderGroup {
  title: string;
  group: GroupDef;
  sIdx: number;
  gIdx: number;
}
interface RenderSection {
  title: string;
  section: SectionDef;
  sIdx: number;
  groups: RenderGroup[];
}

function buildTree(sections: SectionDef[]): RenderSection[] {
  return sections.map((sec, si) => ({
    title: sec.title,
    section: sec,
    sIdx: si,
    groups: sec.groups.map((g, gi) => ({
      title: g.title,
      group: g,
      sIdx: si,
      gIdx: gi,
    })),
  }));
}

export function TemplateSlotEditor({
  schema,
  onSave,
  onCancel,
  saving = false,
  jobId,
  aiEnabled = false,
  sampleText,
  sampleContentJson,
  iriPattern,
  mode = "edit",
  templateId,
  meta,
  versions,
  onVersionSwitch,
  onMetaSaved,
  outputEditor,
}: TemplateSlotEditorProps) {
  const [sections, setSections] = useState<SectionDef[]>(
    () => cloneSections(schema.sections),
  );
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    () => new Set(schema.sections.map((s) => s.section_id)),
  );
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [editingSlot, setEditingSlot] = useState<{
    sectionIdx: number;
    groupIdx: number;
    slotIdx: number;
    slot: SlotDef;
  } | null>(null);

  // ── 015 行文 Prompt（每节一段叙述提示词，可从样本生成）────────────────
  const [promptGenerating, setPromptGenerating] = useState<string | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  // 异步报告生成轮询中（POST 已返回 report_id，后台 LLM 仍在运行）
  const [asyncGenerating, setAsyncGenerating] = useState(false);
  // 015+ 行文预览：按 section_id 键；用已关联真实文档的抽取事实测试本节行文效果。
  const [previewingSection, setPreviewingSection] = useState<string | null>(null);
  const [sectionPreviews, setSectionPreviews] = useState<Record<string, string>>({});
  const [sectionPreviewErrors, setSectionPreviewErrors] = useState<
    Record<string, string>
  >({});

  // ── AI 分析（内联，替代原独立 drawer）──────────────────────────────
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  const [aiStage, setAiStage] = useState(0);
  const aiTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // ── 016 本体覆盖声明（AI 分析产出）─────────────────────────────────────
  // pendingCoverage：AI 建议的本体覆盖边（类型级），作者采纳进某分节的 coverage。
  // AI 分析只呈现能绑定到本体的覆盖边，无法绑定的位点静默忽略（取代 FR-008a 的取数候选流）。
  const [pendingCoverage, setPendingCoverage] = useState<OntologyRelationBinding[]>([]);

  // 左侧忠实预览的高亮锚点：点击建议→其 source_ref/evidence_span；点击真实 Slot→其 label（尽力而为）。
  const [activeRef, setActiveRef] = useState<string | null>(null);
  const [activeAnchor, setActiveAnchor] = useState<EvidenceAnchor | null>(null);
  const [sourceAnchor, setSourceAnchor] = useState<EvidenceAnchor | null>(null);
  const [sourceUnavailable, setSourceUnavailable] = useState(false);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);

  // job_id 分支的忠实预览（模板流程通常不传 jobId；保留以不回退能力）。
  const [jobContent, setJobContent] = useState<TiptapContent | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    if (jobId && !sampleContentJson) {
      getAnnotatedDocument(jobId, false, controller.signal)
        .then((doc) => {
          if (!controller.signal.aborted) setJobContent((doc.content as TiptapContent) ?? null);
        })
        .catch(() => { if (!controller.signal.aborted) setJobContent(null); });
    }
    return () => controller.abort();
  }, [jobId, sampleContentJson]);

  // ── 015 源文档页签：IRI 匹配文档列表 + 选中文档正文预览 ─────────────────
  // 拉取全部文档个体（listDocuments），再按模板 iri_pattern 客户端子串过滤 class_iri
  // ——与后端模板解析语义一致（ast_template.resolve：iri_pattern in doc_class_iri）。
  // 选中真实文档后按其 job 引用取回正文（getAnnotatedDocument），取不到则如实降级。
  // 空/失败按气隙常态静默（不作错误呈现）。
  // 左侧多页签受控值（提升到组件级）：右侧面板据此在「关系图谱」（源文档页签）
  // 与「Slot 树」（AST模板定义 / 报告预览页签）间切换，避免删除唯一的编辑入口。
  // 编辑模式（templateId 存在）默认落在「基本信息」；创建模式仍从「源文档」起步。
  const [leftTab, setLeftTab] = useState(templateId ? "basic" : "source");
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
  useEffect(() => {
    if (!meta || metaSaving) return;
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
  }, [meta]); // eslint-disable-line react-hooks/exhaustive-deps

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
        ...(!outputEditor ? {
          doc_no: metaForm.docNo.trim() || null,
          iri_pattern: metaForm.iriPattern || null,
        } : {}),
        owner: metaForm.owner.trim() || null,
        ...(!outputEditor ? { status: metaForm.status } : {}),
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
  }, [templateId, metaForm, onMetaSaved, outputEditor, meta?.status]);

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
    return docs.filter((d) => d.class_iri?.includes(pat));
  }, [docs, iriPattern]);

  // 有效选中项：用户点选若仍在匹配集内则沿用，否则默认首个（render 派生，免 effect 同步）。
  const activeDocIri = useMemo(
    () =>
      selectedDocIri === DEFAULT_SOURCE_IRI
        ? DEFAULT_SOURCE_IRI
        : selectedDocIri && matchedDocs.some((d) => d.iri === selectedDocIri)
          ? selectedDocIri
          : matchedDocs[0]?.iri ?? null,
    [selectedDocIri, matchedDocs],
  );
  const activeShadow = useMemo(
    () => (activeDocIri ? docs.find((d) => d.iri === activeDocIri) ?? null : null),
    [activeDocIri, docs],
  );

  // 选中真实文档 → 按 job 引用取回正文（尽力而为，降级为「不可预览」；绝不抛错）。
  // queryKey 包含 sourceJobId：DEFAULT_SOURCE_IRI 的预览依赖它；变化时自动重取。
  const docContentQuery = useQuery({
    queryKey: ["ast-source-doc-content", activeDocIri, activeDocIri === DEFAULT_SOURCE_IRI ? sourceJobId : null],
    enabled: Boolean(activeDocIri),
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
    : docContentQuery.isLoading
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

  // 016 (F7/D10)：本体覆盖声明与 AI 分析共用的 doc_class_iri —— 优先取模板必选
  // 「关联文档类型」写入的完整 IRI（iri_pattern），即使左侧未选样例文档也能驱动
  // AI 分析；回退到左侧已解析文档类（存量模板 iri_pattern 为部分子串时）。驱动
  // getRelationSchema 拉取单跳(hop-1)关系菜单，作为覆盖声明的作者化单位。
  // 只读本体访问（Principle II）；无 IRI / 离线 → 菜单为空、区块降级为提示（Principle VI）。
  const docClassIri = useMemo(() => {
    // 优先「关联文档类型」下拉框的**实时**选择（未保存即生效）；下拉写入完整 IRI。
    const live = outputEditor?.documentClassIri ?? metaForm.iriPattern;
    if (live && (live.startsWith("http://") || live.startsWith("https://"))) return live;
    if (iriPattern && (iriPattern.startsWith("http://") || iriPattern.startsWith("https://"))) return iriPattern;
    if (sourceDocClass?.doc_class_iri) return sourceDocClass.doc_class_iri;
    return null;
  }, [sourceDocClass, iriPattern, metaForm.iriPattern, outputEditor?.documentClassIri]);
  const relationSchemaQuery = useQuery({
    // key 末位 1 = maxHops，须与下方 queryFn 的入参一致（TanStack 缓存原则：凡影响
    // queryFn 结果的参数都进 key），未来若新增按不同 maxHops 的调用点即不会串用缓存。
    queryKey: ["relation-schema", docClassIri, 1],
    // maxHops=1：菜单只呈现文档类型的**直接出边**（hop-1 作者化单位，见上）。多跳边
    // 经目标类型的后续模板覆盖；且此下拉按 (predicate,range) 建 React key/去重，而 BFS
    // 多跳里同一 (predicate,range) 可经不同 domain 重复出现（如 hasStorageCondition
    // ×3、usesEquipment ×2、自引用 hasDegradationPathway ×2）——在源头只取 hop-1 即
    // 消除重复项与 dup-key 告警。切勿改回默认 4 跳。
    queryFn: () => getRelationSchema(docClassIri!, 1),
    enabled: !!docClassIri && !outputEditor,
    staleTime: 5 * 60 * 1000,
  });
  const relationSchema: RelationSchemaEdge[] = relationSchemaQuery.data ?? [];

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
  // 已选类型「未建模」= capableSet 已知且不含它 → 拦截 AI 覆盖分析、禁用 AI 按钮。
  const docClassUnmodeled =
    !!capableSet && !!docClassIri && !capableSet.has(docClassIri);
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

  // ── 015 报告预览页签：AST 覆盖率分析（迁移自 /entities/extraction/[jobId]/ast）。
  // 从匹配文档解析 jobId，用当前模板计算覆盖率，支持生成/下载报告。
  const queryClient = useQueryClient();
  const previewJobId = useMemo(
    () =>
      activeDocIri === DEFAULT_SOURCE_IRI
        ? sourceJobId
        : docJobRef(activeShadow ?? undefined),
    [activeDocIri, activeShadow, sourceJobId],
  );
  const [previewSlot, setPreviewSlot] = useState<SlotCoverageDTO | null>(null);
  const [previewScrollSlot, setPreviewScrollSlot] = useState<string | null>(null);
  const [previewHighlightRef, setPreviewHighlightRef] = useState<string | undefined>(undefined);
  const [confirmGenOpen, setConfirmGenOpen] = useState(false);

  const coverageQuery = useQuery({
    queryKey: ["ast-coverage", previewJobId, templateId ?? "default"],
    queryFn: ({ signal }) => getAstCoverage(previewJobId!, templateId, signal),
    enabled: !!previewJobId && !outputEditor,
  });
  const previewReportsQuery = useQuery({
    queryKey: ["reports", previewJobId],
    queryFn: () => listReports(previewJobId!),
    enabled: !!previewJobId && !outputEditor,
  });
  const previewCoverage = coverageQuery.data ?? null;
  const refreshEvidenceCoverage = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["ast-coverage", previewJobId] });
  }, [queryClient, previewJobId]);
  const previewReports = previewReportsQuery.data ?? [];

  const dismissMut = useMutation({
    mutationFn: (slotId: string) => dismissSlot(previewJobId!, slotId),
    onSuccess: (updated) => {
      queryClient.setQueryData(["ast-coverage", previewJobId, templateId ?? "default"], updated);
      if (previewSlot) {
        const flat = updated.sections.flatMap((s) => s.groups.flatMap((g) => g.slots));
        setPreviewSlot(flat.find((s) => s.slot_id === previewSlot.slot_id) ?? null);
      }
    },
  });
  const undismissMut = useMutation({
    mutationFn: (slotId: string) => undismissSlot(previewJobId!, slotId),
    onSuccess: (updated) => {
      queryClient.setQueryData(["ast-coverage", previewJobId, templateId ?? "default"], updated);
      if (previewSlot) {
        const flat = updated.sections.flatMap((s) => s.groups.flatMap((g) => g.slots));
        setPreviewSlot(flat.find((s) => s.slot_id === previewSlot.slot_id) ?? null);
      }
    },
  });
  const generateMut = useMutation({
    mutationFn: () => generateRiskReport(previewJobId!, templateId),
    onSuccess: async (result) => {
      if (result instanceof Blob) {
        const url = URL.createObjectURL(result);
        const a = document.createElement("a");
        a.href = url;
        a.download = `risk-report-${(previewJobId ?? "").slice(0, 8)}.docx`;
        a.click();
        URL.revokeObjectURL(url);
        queryClient.invalidateQueries({ queryKey: ["reports", previewJobId] });
        return;
      }
      // 异步路径：轮询直到 completed / failed
      const { report_id } = result as { report_id: string; status: string };
      setAsyncGenerating(true);
      const poll = async () => {
        try {
          for (;;) {
            await new Promise((r) => setTimeout(r, 3000));
            const s = await pollReportStatus(previewJobId!, report_id);
            if (s.report_status === "completed" || s.report_status === "failed") {
              queryClient.invalidateQueries({ queryKey: ["reports", previewJobId] });
              return;
            }
          }
        } catch {
          // 轮询出错时静默结束
        } finally {
          setAsyncGenerating(false);
        }
      };
      poll();
    },
  });
  // POST 仅表示入队；持久化版本推进时刷新图谱，终态刷新覆盖检查。
  const [sourceProgress, setSourceProgress] = useState<JobProgressEvent | null>(null);
  const [sourceRunError, setSourceRunError] = useState<{ jobId: string; message: string } | null>(null);
  const [evidenceRefresh, setEvidenceRefresh] = useState(0);
  const [pauseRequestedJob, setPauseRequestedJob] = useState<string | null>(null);
  const rerunMut = useMutation({
    mutationFn: async ({ jobId, resume = false, continueOptions }: {
      jobId: string; resume?: boolean; continueOptions?: EvidenceExtractOptions;
    }) => {
      if (continueOptions) return extractJobEvidence(jobId, continueOptions);
      if (resume) return resumeAnnotation(jobId);
      return rerunAnnotation(jobId, templateId);
    },
    onMutate: () => { setSourceRunError(null); setPauseRequestedJob(null); },
    onSuccess: (run) => setSourceProgress({
      job_id: run.job_id, run_id: run.run_id, stage: "annotating", annotation_stage: "queued",
      pct: 0, status: "running", degraded: false,
    }),
    onError: (error, { jobId }) => setSourceRunError({
      jobId,
      message: error instanceof Error ? error.message : "重新识别请求失败，请重试",
    }),
  });
  const pauseMut = useMutation({
    mutationFn: (jobId: string) => pauseAnnotation(jobId),
    onSuccess: (_data, jobId) => setPauseRequestedJob(jobId),
    onError: (error, jobId) => setSourceRunError({ jobId,
      message: error instanceof Error ? error.message : "暂停识别请求失败，请重试" }),
  });

  useEffect(() => {
    // 打开/切换文档也订阅历史进度，可接续刷新页面前或其他页面发起的任务。
    // 发起 POST 期间关闭旧流，避免回放上一次终态误将新任务判为完成。
    if (!previewJobId || rerunMut.isPending) return;
    let ignore = false;
    let lastRevision: number | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = subscribeJobProgress(previewJobId, (event) => {
      if (ignore || event.job_id !== previewJobId || !event.annotation_stage) return;
      setSourceProgress(event);
      if (event.status === "running") {
        setSourceRunError(null);
        if (event.data_revision !== undefined && event.data_revision !== lastRevision) {
          lastRevision = event.data_revision;
          if (!refreshTimer) refreshTimer = setTimeout(() => {
            refreshTimer = undefined;
            if (!ignore) setEvidenceRefresh((value) => value + 1);
          }, 1500);
        }
      }
      const terminal = event.annotation_stage;
      if (!["complete", "failed", "paused", "interrupted"].includes(terminal)) return;
      ignore = true;
      clearTimeout(refreshTimer);
      unsubscribe();
      setPauseRequestedJob(null);
      if (terminal !== "complete") {
        setSourceRunError({
          jobId: previewJobId,
          message: event.has_checkpoint && event.can_resume === false
            ? "本轮已达到处理上限，已保存部分结果；请检查未通过的任务和抽取配置"
            : terminal === "interrupted"
            ? event.has_checkpoint ? "识别已中断，已保存断点，可继续识别" : "识别已中断，请重新识别"
            : terminal === "failed"
            ? "关系识别失败，请检查模型服务和任务日志"
            : "关系识别已暂停，已保存部分结果，可继续未处理任务",
        });
      }
      void queryClient.refetchQueries({
        queryKey: ["ast-source-doc-content", activeDocIri,
          activeDocIri === DEFAULT_SOURCE_IRI ? sourceJobId : null],
        exact: true,
      });
      void queryClient.invalidateQueries({ queryKey: ["ast-coverage", previewJobId] });
      setEvidenceRefresh((value) => value + 1);
    });
    return () => { ignore = true; clearTimeout(refreshTimer); unsubscribe(); };
  }, [activeDocIri, previewJobId, queryClient, rerunMut.isPending, sourceJobId]);

  const recognitionRunning = rerunMut.isPending ||
    (sourceProgress?.job_id === previewJobId && sourceProgress.status === "running");
  const rerunSourceError = sourceRunError?.jobId === previewJobId ? sourceRunError.message : null;
  const currentSourceProgress = sourceProgress?.job_id === previewJobId ? sourceProgress : null;
  const canResumeSource = !recognitionRunning && currentSourceProgress?.has_checkpoint &&
    currentSourceProgress.can_resume !== false;
  const sourceProgressText = currentSourceProgress?.tasks_processed !== undefined
    ? `已处理 ${currentSourceProgress.tasks_processed} 项：成功 ${currentSourceProgress.tasks_completed ?? 0} 项，未通过 ${currentSourceProgress.tasks_failed ?? 0} 项；模型调用 ${currentSourceProgress.model_calls ?? 0} 次`
    : "正在解析文档并准备识别任务…";
  const rerunCurrentSource = () => {
    if (previewJobId && !recognitionRunning) rerunMut.mutate({ jobId: previewJobId });
  };

  const handlePreviewGenerate = () => {
    if (!previewCoverage) return;
    if (previewCoverage.missing_required > 0) { setConfirmGenOpen(true); return; }
    generateMut.mutate();
  };
  const doPreviewGenerate = () => { setConfirmGenOpen(false); generateMut.mutate(); };
  const handlePreviewDownload = async (report: GeneratedReportDTO) => {
    if (!previewJobId) return;
    try {
      const blob = await downloadReport(previewJobId, report.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = report.file_path.split("/").pop() ?? `report-${report.id.slice(0, 8)}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* non-critical */ }
  };
  const handlePreviewScrollToMissing = () => {
    if (!previewCoverage) return;
    for (const sec of previewCoverage.sections)
      for (const grp of sec.groups)
        for (const slot of grp.slots)
          if (slot.status === "missing_required") { setPreviewScrollSlot(slot.slot_id); return; }
  };

  const toggleSection = useCallback((id: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleGroup = useCallback((id: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  function removeSlot(sectionIdx: number, groupIdx: number, slotIdx: number) {
    setSections((prev) => {
      const next = cloneSections(prev);
      next[sectionIdx].groups[groupIdx].slots.splice(slotIdx, 1);
      return next;
    });
  }

  function toggleDisable(sectionIdx: number, groupIdx: number, slotIdx: number) {
    setSections((prev) => {
      const next = cloneSections(prev);
      const slot = next[sectionIdx].groups[groupIdx].slots[slotIdx];
      slot.disabled = !slot.disabled;
      return next;
    });
  }

  function addSlot(sectionIdx: number, groupIdx: number) {
    const groupId = sections[sectionIdx].groups[groupIdx].group_id;
    setSections((prev) => {
      const next = cloneSections(prev);
      const group = next[sectionIdx].groups[groupIdx];
      const newId = `${group.group_id}.new_${Date.now()}`;
      group.slots.push({
        slot_id: newId,
        label: "新插槽",
        // 016+：新建插槽默认为语义化插槽（prompt + 关联本体投影）。旧定型来源
        // （extraction/rule/constant/manual）仅对既有插槽只读呈现。
        source: { kind: "semantic", prompt: null, coverage_refs: [] },
        required: false,
        on_missing: "annotate",
        missing_placeholder: "⚠ 待评估（数据缺失）",
      });
      return next;
    });
    setExpandedGroups((prev) => new Set([...prev, groupId]));
  }

  function addSection() {
    const ts = Date.now();
    const secId = `sec_${ts}`;
    const grpId = `grp_${ts}`;
    setSections((prev) => [
      ...cloneSections(prev),
      {
        section_id: secId,
        title: "新分节",
        groups: [{ group_id: grpId, title: "新分组", kind: "fields", slots: [] }],
      },
    ]);
    setExpandedSections((prev) => new Set([...prev, secId]));
    setExpandedGroups((prev) => new Set([...prev, grpId]));
  }

  // 缺陷 B：AI 覆盖建议（pendingCoverage）只在分节的 SectionCoverageArea 内渲染；create
  // 流程初始无分节时 AI 输出无处落地。仅当当前确无分节时播种一个默认分节承载建议
  // （race-free：函数式更新读最新态；重复分析不叠加）。
  function ensureSeedSection() {
    const ts = Date.now();
    const secId = `sec_${ts}`;
    const grpId = `grp_${ts}`;
    setSections((prev) =>
      prev.length
        ? prev
        : [
            {
              section_id: secId,
              title: "新分节",
              groups: [
                { group_id: grpId, title: "新分组", kind: "fields", slots: [] },
              ],
            },
          ],
    );
    setExpandedSections((prev) => new Set([...prev, secId]));
    setExpandedGroups((prev) => new Set([...prev, grpId]));
  }

  // 骨架 ID 与来源由服务端 IR 决定；保留 label/value anchor，不用文本猜测来源。
  function materializeSkeleton(skeleton: AiStructureSection[]) {
    const secIds: string[] = [];
    const grpIds: string[] = [];
    const built: SectionDef[] = skeleton.map((sec) => {
      const secId = sec.id;
      secIds.push(secId);
      const rawGroups =
        sec.groups && sec.groups.length > 0
          ? sec.groups
          : [{ id: `${secId}.fields`, title: "新分组", candidates: [], origin: null }];
      const groups: GroupDef[] = rawGroups.map((grp) => {
        const grpId = grp.id;
        grpIds.push(grpId);
        return {
          group_id: grpId,
          origin: grp.origin,
          title: grp.title || "新分组",
          kind: "fields",
          slots: (grp.candidates ?? []).map((cand) => ({
            slot_id: cand.id,
            origin: cand.origin,
            label: cand.label || "新插槽",
            // 形状同 addSlot 默认：唯一「现代、可作者填写」的插槽。
            source: { kind: "semantic", prompt: null, coverage_refs: [] },
            required: false,
            on_missing: "annotate",
            missing_placeholder: "⚠ 待评估（数据缺失）",
          })),
        };
      });
      return { section_id: secId, title: sec.title || "新分节", origin: sec.origin, groups };
    });
    setSections((prev) => (prev.length ? prev : built));
    setExpandedSections((prev) => new Set([...prev, ...secIds]));
    setExpandedGroups((prev) => new Set([...prev, ...grpIds]));
  }

  function updateSectionPrompt(sectionIdx: number, value: string) {
    setSections((prev) => {
      const next = cloneSections(prev);
      next[sectionIdx].prompt = value;
      return next;
    });
  }

  // ── 016 本体覆盖声明 mutators（写入 section.coverage，随 schema_json 往返）──
  const bindingKey = (b: OntologyRelationBinding) =>
    `${b.predicate_iri}→${b.range_class_iri}`;

  function addCoverage(sectionIdx: number, binding: CoverageBinding) {
    setSections((prev) => {
      const next = cloneSections(prev);
      const cov = next[sectionIdx].coverage ?? [];
      // 去重：同一分节内相同 predicate→range 只保留一条覆盖声明。
      if (
        binding.kind === "ontology_relation" &&
        cov.some(
          (c) =>
            c.kind === "ontology_relation" &&
            bindingKey(c) === bindingKey(binding),
        )
      ) {
        return next;
      }
      next[sectionIdx].coverage = [...cov, binding];
      return next;
    });
  }

  function removeCoverage(sectionIdx: number, covIdx: number) {
    setSections((prev) => {
      const next = cloneSections(prev);
      const cov = next[sectionIdx].coverage ?? [];
      next[sectionIdx].coverage = cov.filter((_, j) => j !== covIdx);
      return next;
    });
  }

  // 必填 ⇄ 选填切换 = 重新校验信号（移除/改选填改变无遗漏清单，SC-005）。
  function toggleCoverageRequired(sectionIdx: number, covIdx: number) {
    setSections((prev) => {
      const next = cloneSections(prev);
      const cov = next[sectionIdx].coverage ?? [];
      const target = cov[covIdx];
      if (target && target.kind === "ontology_relation") {
        target.required = !target.required;
      }
      return next;
    });
  }

  function adoptPendingCoverage(
    sectionIdx: number,
    binding: OntologyRelationBinding,
  ) {
    addCoverage(sectionIdx, binding);
    setPendingCoverage((prev) =>
      prev.filter((b) => bindingKey(b) !== bindingKey(binding)),
    );
  }

  function ignorePendingCoverage(binding: OntologyRelationBinding) {
    setPendingCoverage((prev) =>
      prev.filter((b) => bindingKey(b) !== bindingKey(binding)),
    );
  }

  // 行文 Prompt 的可用变量 = 本节所有插槽标签（生成时以插槽值替换 {{label}}）。
  function sectionSlotLabels(section: SectionDef): string[] {
    return section.groups.flatMap((g) =>
      g.slots.filter((sl) => !sl.disabled).map((sl) => sl.label),
    );
  }

  // 从样本生成：调用 generate-section-prompt（与 suggest-slots 同门控），把返回的
  // 提示词写入本节 prompt。样本文本优先取 sampleText，退化到 previewContent 的纯文本。
  async function handleGeneratePrompt(sectionIdx: number) {
    const section = sections[sectionIdx];
    setPromptGenerating(section.section_id);
    setPromptError(null);
    try {
      const { prompt } = await generateSectionPrompt({
        section_title: section.title,
        slot_labels: sectionSlotLabels(section),
        sample_text: promptSampleText ?? undefined,
      });
      updateSectionPrompt(sectionIdx, prompt);
    } catch (e) {
      setPromptError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setPromptGenerating(null);
    }
  }

  // 预览：用当前（可能未保存）的行文 Prompt + 已关联真实文档的抽取事实，走与报告
  // 同源的 preview-section-narrative 端点，就地展示本节正文。仅编辑态且已关联文档可用。
  async function handlePreviewPrompt(sectionIdx: number) {
    const section = sections[sectionIdx];
    if (!previewJobId || !templateId) return;
    setPreviewingSection(section.section_id);
    setSectionPreviewErrors((prev) => {
      const next = { ...prev };
      delete next[section.section_id];
      return next;
    });
    try {
      const { narrative } = await previewSectionNarrative({
        job_id: previewJobId,
        template_id: templateId,
        section_id: section.section_id,
        prompt: section.prompt ?? "",
      });
      setSectionPreviews((prev) => ({ ...prev, [section.section_id]: narrative }));
    } catch (e) {
      setSectionPreviewErrors((prev) => ({
        ...prev,
        [section.section_id]: e instanceof Error ? e.message : "预览失败",
      }));
    } finally {
      setPreviewingSection(null);
    }
  }

  function closeSectionPreview(sectionId: string) {
    setSectionPreviews((prev) => {
      const next = { ...prev };
      delete next[sectionId];
      return next;
    });
    setSectionPreviewErrors((prev) => {
      const next = { ...prev };
      delete next[sectionId];
      return next;
    });
  }

  function moveSlot(
    sectionIdx: number,
    groupIdx: number,
    slotIdx: number,
    direction: -1 | 1,
  ) {
    setSections((prev) => {
      const next = cloneSections(prev);
      const slots = next[sectionIdx].groups[groupIdx].slots;
      const target = slotIdx + direction;
      if (target < 0 || target >= slots.length) return prev;
      [slots[slotIdx], slots[target]] = [slots[target], slots[slotIdx]];
      return next;
    });
  }

  function openSlotEditor(sectionIdx: number, groupIdx: number, slotIdx: number) {
    const slot = JSON.parse(
      JSON.stringify(sections[sectionIdx].groups[groupIdx].slots[slotIdx]),
    ) as SlotDef;
    setEditingSlot({ sectionIdx, groupIdx, slotIdx, slot });
    // 编辑即选中：高亮该行并尽力在左侧预览定位（与 design 的选中态一致）。
    setActiveSlotId(slot.slot_id);
    setActiveRef(slot.label || null);
  }

  function saveSlotEdit() {
    if (!editingSlot) return;
    setSections((prev) => {
      const next = cloneSections(prev);
      next[editingSlot.sectionIdx].groups[editingSlot.groupIdx].slots[
        editingSlot.slotIdx
      ] = editingSlot.slot;
      return next;
    });
    setEditingSlot(null);
  }

  // 三者互斥（后端 model_post_init 要求恰好其一）：job_id > 忠于结构的 tiptap 样例
  // > 旧的扁平 sample_text（legacy 模板）。existing_template 供后端 round-2 去重。
  const buildAiRequest = useCallback((): SuggestSlotsRequest | null => {
    const source = jobId
      ? { job_id: jobId }
      : sampleContentJson
        ? { sample_content_json: sampleContentJson }
        : sampleText
          ? { document_text: sampleText }
          : null;
    if (!source) return null;
    return {
      ...source,
      // 016 (D10/F7)：把文档实体类型送给服务端，用于把 AI 分析锚定到只读本体的
      // 关系边（而非样本个体），只产出 coverage（本体覆盖边）。
      doc_class_iri: docClassIri,
      existing_template: {
        sections: sections.map((s) => ({
          title: s.title,
          groups: s.groups.map((g) => ({
            title: g.title,
            slots: g.slots.map((sl) => sl.label),
          })),
        })),
      },
    };
  }, [jobId, sampleContentJson, sampleText, sections, docClassIri]);

  const AI_STAGES = [
    { label: "准备本体上下文…", pct: 10 },
    { label: "分析文档结构（Round 1）…", pct: 35 },
    { label: "生成插槽定义（Round 2）…", pct: 65 },
    { label: "后处理与去重…", pct: 90 },
  ];

  function startAiProgress() {
    setAiStage(0);
    let stage = 0;
    const delays = [2000, 12000, 12000];
    function tick() {
      stage += 1;
      if (stage < AI_STAGES.length) {
        setAiStage(stage);
        aiTimerRef.current = setTimeout(tick, delays[stage] ?? 8000);
      }
    }
    aiTimerRef.current = setTimeout(tick, delays[0]);
  }

  function stopAiProgress() {
    if (aiTimerRef.current) {
      clearTimeout(aiTimerRef.current);
      aiTimerRef.current = null;
    }
    setAiStage(0);
  }

  async function runAiAnalysis() {
    const req = buildAiRequest();
    if (!req) {
      setAiError("无可分析的样例内容");
      return;
    }
    setAiLoading(true);
    setAiError(null);
    startAiProgress();
    try {
      const res = await suggestSlots(req);
      setAiSummary(res.document_summary || null);
      if (res.diagnostics?.length) setAiSummary(res.document_summary || res.diagnostics.join("；"));
      // 016：AI 分析只产出本体锚定的覆盖建议（仅关系边）与无法绑定的候选。
      // 已存在于任一分节 coverage 的建议先行过滤，避免重复呈现。
      const declaredKeys = new Set<string>();
      sections.forEach((s) =>
        (s.coverage ?? []).forEach((c) => {
          if (c.kind === "ontology_relation") declaredKeys.add(bindingKey(c));
        }),
      );
      const nextPending = (res.coverage ?? []).filter(
        (c): c is OntologyRelationBinding =>
          c.kind === "ontology_relation" && !declaredKeys.has(bindingKey(c)),
      );
      setPendingCoverage(nextPending);
      // 主修复：空模板 + AI 回传结构骨架 → 物化真实样例骨架（分节 + 语义化槽）。
      // 否则退化：有覆盖建议但无分节时至少播种一个空分节承载建议。骨架仅在 0 分节时
      // 物化，绝不覆盖作者已有编辑（materializeSkeleton / ensureSeedSection 内均门控）。
      const skeleton = res.sections ?? [];
      if (sections.length === 0 && skeleton.length > 0) {
        materializeSkeleton(skeleton);
      } else if (nextPending.length > 0) {
        ensureSeedSection();
      }
    } catch (e) {
      setAiError(e instanceof Error ? e.message : String(e));
    } finally {
      stopAiProgress();
      setAiLoading(false);
    }
  }

  function handleSlotClick(slot: SlotDef) {
    setActiveSlotId(slot.slot_id);
    setActiveRef(null);
    setSourceUnavailable(!slot.origin);
    setActiveAnchor(slot.origin ? {
      ...slot.origin.label_anchor,
      document_hash: slot.origin.document_hash,
      parser_version: slot.origin.parser_version,
      structure_hash: slot.origin.structure_hash,
    } : null);
  }

  // 忠实预览内容：优先持久化/直传的 tiptap 样例；job_id 走拉取缓存；legacy 仅有
  // 扁平 sample_text 时按行包装成段落；都缺省则显示占位。
  const previewContent: TiptapContent | null = useMemo(() => {
    if (sampleContentJson) return sampleContentJson;
    if (jobContent) return jobContent;
    if (sampleText) return wrapTextAsTiptap(sampleText);
    return null;
  }, [sampleContentJson, jobContent, sampleText]);

  // 行文 Prompt 生成用的样本文本：优先扁平 sample_text，退化到 tiptap 预览的纯文本。
  const promptSampleText: string | null = sampleText
    ? sampleText
    : previewContent
      ? tiptapToText(previewContent).trim() || null
      : null;

  function handleSave() {
    onSave({ ...schema, sections });
  }

  const tree = useMemo(() => buildTree(sections), [sections]);

  const enabledCount = sections.reduce(
    (acc, s) =>
      acc + s.groups.reduce((a, g) => a + g.slots.filter((sl) => !sl.disabled).length, 0),
    0,
  );
  const disabledCount = sections.reduce(
    (acc, s) =>
      acc + s.groups.reduce((a, g) => a + g.slots.filter((sl) => sl.disabled).length, 0),
    0,
  );
  const requiredCount = sections.reduce(
    (acc, s) =>
      acc + s.groups.reduce((a, g) => a + g.slots.filter((sl) => sl.required).length, 0),
    0,
  );
  const totalSlots = enabledCount + disabledCount;

  const isSecExpanded = (rs: RenderSection) =>
    expandedSections.has(rs.section.section_id);
  const isGrpExpanded = (rg: RenderGroup) =>
    expandedGroups.has(rg.group.group_id);

  // ── 左右两栏可调宽：在「预览」与右侧「Section / 关系图谱」区之间拖动把手调宽度。
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
    <div ref={containerRef} className="flex h-full min-h-0">
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
                        value={outputEditor?.documentNo ?? metaForm.docNo}
                        aria-label="模板编号"
                        onChange={(e) => {
                          if (outputEditor?.onDocumentNoChange) outputEditor.onDocumentNoChange(e.target.value);
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
                        disabled={!!outputEditor}
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
                      {outputEditor && <p className="text-xs text-muted-foreground">
                        在「AST模板定义」校验语义后发布新修订。
                      </p>}
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
                        value={(outputEditor?.documentClassIri ?? metaForm.iriPattern) || undefined}
                        onValueChange={(v) => {
                          if (outputEditor?.onDocumentClassChange) outputEditor.onDocumentClassChange(v);
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
                        仅「已建模本体关系」的类型可用于{outputEditor ? "语义绑定与来源匹配" : "覆盖声明与 AI 分析"}
                        {capableLabels && capableLabels.length > 0
                          ? `（当前：${capableLabels.join("、")}）`
                          : ""}
                        ；其余类型待本体补充关系后自动启用。
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">IRI 匹配键</Label>
                      <Input
                        value={(outputEditor?.documentClassIri ?? metaForm.iriPattern) ? docLocalName(outputEditor?.documentClassIri ?? metaForm.iriPattern) : "—"}
                        disabled
                        readOnly
                        className="text-muted-foreground"
                      />
                    </div>
                  </div>
                </section>

                {outputEditor?.basicInfo}

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
                    正文已加载。语义抽取独立执行；请在逐值证据面板继续抽取或刷新状态。
                  </p>
                )}
                <div className="rounded border bg-card p-6 shadow-sm">
                  {docContent.kind === "ready" ? (
                    <WordViewer
                      key={activeDocIri}
                      content={docContent.content}
                      highlightRef={selectedSourceRef}
                      activeAnchor={sourceAnchor}
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
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="template" className="flex-1 min-h-0 overflow-y-auto px-6 py-4 mt-0">
            <div className="flex flex-col gap-4">
              {/* 用户上传的模板样例正文（忠于原文结构）——AI 分析与插槽选中在此高亮联动 */}
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-foreground">
                  模板样例内容
                </span>
                <span className="text-xs text-muted-foreground">
                  {schema.doc_no ? schema.doc_no : "用户上传样例"}
                </span>
              </div>

              {aiSummary && (
                <div className="rounded bg-muted/50 p-3 text-sm">
                  <span className="font-medium">文档摘要：</span>
                  {aiSummary}
                </div>
              )}

              <div className="rounded border bg-card p-6 shadow-sm">
                {previewContent ? (
                  <WordViewer
                    key={jobId ?? "sample"}
                    content={previewContent}
                    highlightRef={activeRef}
                    activeAnchor={outputEditor ? outputEditor.sampleAnchor : activeAnchor}
                    sourceUnavailable={outputEditor ? false : sourceUnavailable}
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

          {outputEditor ? (
            <TabsContent value="report-preview" forceMount={reportOpened ? true : undefined}
              className="mt-0 flex-1 min-h-0 overflow-y-auto data-[state=inactive]:hidden">
              {reportOpened && outputEditor.reportPreview(previewJobId ?? sourceJobId)}
            </TabsContent>
          ) : <TabsContent
            value="report-preview"
            className="mt-0 flex-1 min-h-0 overflow-y-auto"
          >
            {!previewJobId ? (
              <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
                <Eye className="size-6 text-muted-foreground/60" />
                <p className="text-sm text-muted-foreground">
                  需先在「源文档」页签关联文档才能预览报告覆盖率
                </p>
              </div>
            ) : coverageQuery.isLoading ? (
              <div className="flex items-center justify-center px-6 py-16">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : !previewCoverage ? (
              <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
                <Info className="size-5 text-muted-foreground/60" />
                <p className="text-sm text-muted-foreground">
                  {coverageQuery.error
                    ? String(coverageQuery.error).includes("422")
                      ? "文档尚未完成抽取分析，需先执行数据抽取"
                      : "覆盖率分析失败"
                    : "暂无覆盖率数据"}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={rerunCurrentSource}
                  disabled={recognitionRunning}
                >
                  {recognitionRunning
                    ? <Loader2 className="mr-1 size-3.5 animate-spin" />
                    : <RotateCw className="mr-1 size-3.5" />}
                  {recognitionRunning ? "分析中…" : "执行数据抽取与覆盖分析"}
                </Button>
                {rerunSourceError && (
                  <p className="text-xs text-destructive">
                    {rerunSourceError}
                  </p>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-4 px-6 py-4">
                {/* 操作栏 */}
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">
                    AST 覆盖率
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={rerunCurrentSource}
                      disabled={recognitionRunning}
                    >
                      {recognitionRunning
                        ? <Loader2 className="mr-1 size-3.5 animate-spin" />
                        : <RotateCw className="mr-1 size-3.5" />}
                      刷新覆盖率
                    </Button>
                    <Button size="sm" onClick={handlePreviewGenerate} disabled={generateMut.isPending || asyncGenerating}>
                      {generateMut.isPending || asyncGenerating ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <FileDown className="mr-1 size-3.5" />}
                      {generateMut.isPending || asyncGenerating ? "生成中..." : "生成报告"}
                    </Button>
                  </div>
                </div>

                {(dismissMut.error || undismissMut.error || generateMut.error) && (
                  <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {String(dismissMut.error || undismissMut.error || generateMut.error)}
                  </div>
                )}

                {/* 进度 + 结构双栏 */}
                <div className="grid grid-cols-1 overflow-hidden rounded-lg border bg-card lg:grid-cols-[1fr_420px]">
                  {/* 左：生成进度 */}
                  <ReportProgressPanel
                    coverage={previewCoverage}
                    reports={previewReports}
                    generating={generateMut.isPending || asyncGenerating}
                    refreshing={recognitionRunning}
                  />

                  {/* 右：报告结构树 */}
                  <div className="flex min-h-0 flex-col">
                    <div className="flex items-center justify-between border-b px-5 py-3">
                      <div className="flex items-center gap-2">
                        <ListTree className="size-4 text-foreground" />
                        <span className="text-sm font-semibold text-foreground">报告结构</span>
                        {(() => {
                          const completed = previewCoverage.filled + previewCoverage.inferred;
                          const pct = previewCoverage.total_slots > 0
                            ? Math.round((completed / previewCoverage.total_slots) * 100)
                            : 0;
                          const color = pct >= 80 ? "text-success" : pct >= 50 ? "text-amber-500" : "text-destructive";
                          return (
                            <span className={cn("text-sm font-semibold tabular-nums", color)}>
                              {pct}%
                            </span>
                          );
                        })()}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <CoverageBadge tone="success" count={previewCoverage.filled + previewCoverage.inferred} />
                        <CoverageBadge
                          tone="destructive"
                          count={previewCoverage.missing_required}
                          onClick={previewCoverage.missing_required > 0 ? handlePreviewScrollToMissing : undefined}
                        />
                        <CoverageBadge tone="muted" count={previewCoverage.dismissed} />
                      </div>
                    </div>
                    <div className="max-h-[44vh] min-h-0 flex-1 overflow-y-auto px-4 py-3">
                      <ASTTreeView
                        coverage={previewCoverage}
                        selectedSlotId={previewSlot?.slot_id}
                        onSelectSlot={setPreviewSlot}
                        scrollToSlotId={previewScrollSlot}
                      />
                    </div>
                    {previewSlot && (
                      <div className="max-h-[36vh] overflow-y-auto border-t px-4 py-3">
                        <SlotDetailPanel
                          slot={previewSlot}
                          onClickSourceRef={setPreviewHighlightRef}
                          actionBar={
                            <SlotActionBar
                              slot={previewSlot}
                              onDismiss={(id) => dismissMut.mutate(id)}
                              onUndismiss={(id) => undismissMut.mutate(id)}
                              dismissing={dismissMut.isPending || undismissMut.isPending}
                              onRerun={rerunCurrentSource}
                              rerunning={recognitionRunning}
                            />
                          }
                        />
                      </div>
                    )}
                    <div className="flex items-center justify-between border-t px-5 py-3">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Info className="size-3.5" />
                        <span>生成完成后可下载 DOCX 报告</span>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={previewReports.length === 0}
                        onClick={() => previewReports[0] && handlePreviewDownload(previewReports[0])}
                      >
                        <Download className="mr-1 size-3.5" />
                        下载报告
                      </Button>
                    </div>
                  </div>
                </div>

                {/* 历史报告 */}
                {previewReports.length > 0 && (
                  <div className="rounded-lg border bg-card p-4">
                    <h4 className="mb-2 text-sm font-semibold text-foreground">
                      历史报告 ({previewReports.length})
                    </h4>
                    <ReportHistoryList reports={previewReports} onDownload={handlePreviewDownload} />
                  </div>
                )}

                {/* 源文档（evidence 高亮联动） */}
                {docContent.kind === "ready" && (
                  <div className="rounded-lg border bg-card p-4">
                    <h4 className="mb-2 text-sm font-semibold text-foreground">源文档</h4>
                    <div className="max-h-[50vh] overflow-y-auto">
                      <WordViewer content={docContent.content} highlightRef={previewHighlightRef} />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 覆盖率不完整确认对话框 */}
            <Dialog open={confirmGenOpen} onOpenChange={setConfirmGenOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>覆盖率不完整</DialogTitle>
                </DialogHeader>
                <p className="text-sm">
                  当前仍有 <strong>{previewCoverage?.missing_required ?? 0}</strong> 个必填槽位缺失。
                  生成的报告中对应部分将标注为「信息缺失」。
                </p>
                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" onClick={() => setConfirmGenOpen(false)}>取消</Button>
                  <Button onClick={doPreviewGenerate} disabled={generateMut.isPending}>
                    {generateMut.isPending ? "生成中..." : "仍然生成"}
                  </Button>
                </div>
              </DialogContent>
            </Dialog>
          </TabsContent>}
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

      {outputEditor && (
        <aside aria-label="输出模板定义" style={{ width: rightWidth }}
          className={cn("shrink-0 flex-col min-h-0", leftTab === "template" ? "flex" : "hidden")}>
          {outputEditor.sidebar}
        </aside>
      )}

      {/* ── Right: 基本信息/报告预览 → 无（左侧全宽）；源文档 → 关系图谱；其余 → Slot 树 + 内联 AI ── */}
      {leftTab === "source" ? (
        <div style={{ width: rightWidth }} className="flex shrink-0 flex-col min-h-0">
          <div className="shrink-0 border-b px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-semibold text-foreground">关系图谱</div>
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                disabled={
                  !previewJobId ||
                  docContent.kind !== "ready" ||
                  recognitionRunning
                }
                title={
                  !previewJobId
                    ? "需已关联真实文档"
                    : docContent.kind !== "ready"
                      ? "暂无可重识别的标注"
                      : "对当前文档重新完整标注（实体+关系），较慢"
                }
                onClick={rerunCurrentSource}
              >
                {recognitionRunning ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RotateCw className="size-3.5" />
                )}
                {recognitionRunning ? "识别中…" : "重新识别"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              按源文档类型的属性和关系展开，查看关联实体与原文依据
            </p>
            {currentSourceProgress?.tasks_processed !== undefined && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">{sourceProgressText}</p>
            )}
            {recognitionRunning && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded border bg-muted/30 p-2">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
                <span className="text-xs text-muted-foreground">
                  {pauseRequestedJob === previewJobId ? "正在取消请求并保存断点…" : "正在识别关系图谱…"}
                </span>
                <span className="px-4 text-center text-xs text-muted-foreground">{sourceProgressText}</span>
                <RecognitionTimer startedAt={currentSourceProgress?.started_at} />
                <ModelRequestProgress request={currentSourceProgress?.model_request}
                  attempts={currentSourceProgress?.http_attempts} />
                <Button variant="outline" size="sm"
                  disabled={!previewJobId || rerunMut.isPending || pauseMut.isPending || pauseRequestedJob === previewJobId}
                  onClick={() => previewJobId && pauseMut.mutate(previewJobId)}>
                  {pauseRequestedJob === previewJobId ? "正在暂停…" : "暂停并保存结果"}
                </Button>
              </div>
            )}
            {canResumeSource && previewJobId && (
              <Button variant="outline" size="sm" className="mt-2"
                onClick={() => rerunMut.mutate({ jobId: previewJobId, resume: true })}>
                从断点继续识别
              </Button>
            )}
            {rerunSourceError && (
              <p className="mt-1 text-xs text-destructive">{rerunSourceError}</p>
            )}
          </div>
          {/* 识别结果与异议操作共用一棵关系树，保持单一滚动区。 */}
          <div className="relative min-h-0 flex-1 overflow-y-auto">
            {previewJobId ? <EvidenceReviewPanel key={`${previewJobId}:${templateId}`} jobId={previewJobId}
              templateId={templateId} refreshKey={evidenceRefresh} running={recognitionRunning}
              onContinue={(continueOptions) => rerunMut.mutate({ jobId: previewJobId, continueOptions })}
              onSource={setSourceAnchor} onSnapshot={refreshEvidenceCoverage} /> : <RelationPanel
              docClass={sourceDocClass}
              relationships={sourceRelationships}
              selectedSourceRef={selectedSourceRef}
              onSelectSourceRef={setSelectedSourceRef}
              emptyMessage={recognitionRunning ? "正在识别实体、属性和关系…"
                : docContent.kind === "ready" && docContent.previewOnly
                  ? "正文已加载，尚无完成的关系识别结果" : undefined}
            />}

          </div>
        </div>
      ) : hasRightPanel && !outputEditor ? (
      <div style={{ width: rightWidth }} className="flex shrink-0 flex-col min-h-0">
        <div className="shrink-0 border-b px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <div className="text-sm text-muted-foreground">
              {totalSlots} 插槽 · {requiredCount} 必填
              {disabledCount > 0 && ` · ${disabledCount} 禁用`}
            </div>
            <div className="ml-auto flex gap-2">
              <Button variant="outline" size="sm" onClick={onCancel}>
                取消
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? "保存中…" : "保存"}
              </Button>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={runAiAnalysis}
              disabled={
                aiLoading ||
                !previewContent
              }
              title={
                !aiEnabled
                  ? "模型已关闭：仍可生成带原文来源的结构骨架"
                  : !previewContent
                    ? "无样例内容可分析"
                    : !docClassIri
                      ? "请先在「基本信息」选择关联文档类型"
                      : docClassUnmodeled
                        ? "该文档类型尚未在本体中建模关系边，暂不支持 AI 覆盖分析"
                        : undefined
              }
            >
              {aiLoading ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  {AI_STAGES[aiStage]?.label ?? "分析中…"}
                </>
              ) : "AI 分析"}
            </Button>
          </div>
          {aiLoading && (
            <div className="space-y-1.5">
              <Progress value={AI_STAGES[aiStage]?.pct ?? 0} className="h-1.5" />
              <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>阶段 {aiStage + 1}/{AI_STAGES.length}</span>
                <span>{AI_STAGES[aiStage]?.pct ?? 0}%</span>
              </div>
            </div>
          )}
          {aiError && (
            <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {aiError}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          {tree.length === 0 && (
            <div className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
              {mode === "create"
                ? "点击「添加分节」手动创建插槽结构；「AI 分析」按关联文档类型编译本体覆盖建议。"
                : "该模板暂无插槽。点击「添加分节」开始，或用「AI 分析」查看本体覆盖建议。"}
            </div>
          )}

          {tree.map((rs) => {
            const realCount = rs.section.groups.reduce(
              (a, g) => a + g.slots.length,
              0,
            );
            return (
              <div key={rs.section.section_id} className="border rounded-lg">
                <button
                  className="w-full flex items-center gap-2 p-3 hover:bg-muted/50 text-left"
                  onClick={() => toggleSection(rs.section.section_id)}
                >
                  <span className="text-xs">{isSecExpanded(rs) ? "▼" : "▶"}</span>
                  <span className="font-medium">{rs.title}</span>
                  <div className="ml-auto flex items-center gap-1">
                    {rs.section.prompt?.trim() && (
                      <Sparkles
                        className="size-3.5 text-primary"
                        aria-label="已配置行文 Prompt"
                      />
                    )}
                    {realCount > 0 && (
                      <Badge variant="outline" className="text-xs">
                        {realCount} 插槽
                      </Badge>
                    )}
                  </div>
                </button>

                {isSecExpanded(rs) && (
                  <div className="px-3 pb-3 space-y-2">
                    {rs.groups.map((rg) => {
                      return (
                        <div
                          key={rg.group.group_id}
                          className="border rounded ml-4"
                        >
                          <button
                            className="w-full flex items-center gap-2 p-2 hover:bg-muted/50 text-left text-sm"
                            onClick={() => toggleGroup(rg.group.group_id)}
                          >
                            <span className="text-xs">{isGrpExpanded(rg) ? "▼" : "▶"}</span>
                            <span>{rg.title}</span>
                            <Badge variant="outline" className="text-xs ml-1">
                              {rg.group.kind}
                            </Badge>
                            <span className="ml-auto text-xs text-muted-foreground">
                              {rg.group.slots.length} 插槽
                            </span>
                          </button>

                          {isGrpExpanded(rg) && (
                            <div className="px-2 pb-2 space-y-1">
                              {/* 真实 Slot 行 + 内联编辑器（选中时在行下方原地展开）*/}
                              {rg.group.slots.map((slot, slIdx) => {
                                  const isEditing =
                                    editingSlot?.sectionIdx === rs.sIdx &&
                                    editingSlot?.groupIdx === rg.gIdx &&
                                    editingSlot?.slotIdx === slIdx;
                                  const kind = (slot.source as Record<string, unknown>)
                                    .kind as string;
                                  const isLast = slIdx === rg.group.slots.length - 1;
                                  return (
                                    <div key={slot.slot_id}>
                                      <div
                                        className={cn(
                                          "group ml-4 flex items-center gap-1.5 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted/40",
                                          slot.disabled && "opacity-50",
                                          (activeSlotId === slot.slot_id || isEditing) &&
                                            "bg-yellow-50 dark:bg-yellow-900/20",
                                        )}
                                        onClick={() => handleSlotClick(slot)}
                                      >
                                        <span
                                          className={cn(
                                            "truncate",
                                            slot.disabled && "line-through",
                                          )}
                                        >
                                          {slot.label}
                                        </span>
                                        <span className="flex-1" />
                                        {slot.required && !slot.disabled && (
                                          <Badge variant="default" className="text-xs">
                                            必填
                                          </Badge>
                                        )}
                                        <Badge
                                          variant={
                                            !slot.disabled && isLegacySourceKind(kind)
                                              ? "secondary"
                                              : "outline"
                                          }
                                          className="text-xs"
                                          title={
                                            isLegacySourceKind(kind)
                                              ? "旧式定型插槽（已弃用）"
                                              : undefined
                                          }
                                        >
                                          {slot.disabled ? "已禁用" : sourceKindLabel(kind)}
                                        </Badge>
                                        <GripVertical
                                          className={cn(
                                            "size-3.5 shrink-0 text-muted-foreground",
                                            isEditing
                                              ? "opacity-100"
                                              : "opacity-40 group-hover:opacity-70",
                                          )}
                                          aria-hidden
                                        />
                                        <div
                                          className="flex shrink-0 items-center gap-0.5"
                                          onClick={(e) => e.stopPropagation()}
                                        >
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            className={cn(
                                              "size-6 text-muted-foreground",
                                              !isEditing &&
                                                "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100",
                                            )}
                                            title="编辑插槽"
                                            onClick={() =>
                                              openSlotEditor(rs.sIdx!, rg.gIdx!, slIdx)
                                            }
                                          >
                                            <Pencil className="size-3.5" />
                                          </Button>
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            className={cn(
                                              "size-6 text-destructive",
                                              !isEditing &&
                                                "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100",
                                            )}
                                            title="删除插槽"
                                            onClick={() =>
                                              removeSlot(rs.sIdx!, rg.gIdx!, slIdx)
                                            }
                                          >
                                            <Trash2 className="size-3.5" />
                                          </Button>
                                          <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                              <Button
                                                variant="ghost"
                                                size="icon"
                                                className="size-6 text-muted-foreground opacity-50 group-hover:opacity-100 data-[state=open]:opacity-100"
                                                title="更多操作"
                                              >
                                                <MoreVertical className="size-3.5" />
                                              </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                              <DropdownMenuItem
                                                onSelect={() =>
                                                  toggleDisable(rs.sIdx!, rg.gIdx!, slIdx)
                                                }
                                              >
                                                {slot.disabled
                                                  ? "启用此插槽"
                                                  : "禁用此插槽（仅编辑器标记）"}
                                              </DropdownMenuItem>
                                              <DropdownMenuSeparator />
                                              <DropdownMenuItem
                                                disabled={slIdx === 0}
                                                onSelect={() =>
                                                  moveSlot(rs.sIdx!, rg.gIdx!, slIdx, -1)
                                                }
                                              >
                                                上移
                                              </DropdownMenuItem>
                                              <DropdownMenuItem
                                                disabled={isLast}
                                                onSelect={() =>
                                                  moveSlot(rs.sIdx!, rg.gIdx!, slIdx, 1)
                                                }
                                              >
                                                下移
                                              </DropdownMenuItem>
                                            </DropdownMenuContent>
                                          </DropdownMenu>
                                        </div>
                                      </div>
                                      {isEditing && editingSlot && (
                                        <SlotInlineEditor
                                          editing={editingSlot}
                                          sectionCoverage={rs.section?.coverage ?? []}
                                          onChange={setEditingSlot}
                                          onCancel={() => setEditingSlot(null)}
                                          onSave={saveSlotEdit}
                                        />
                                      )}
                                    </div>
                                  );
                                })}

                              <Button
                                variant="outline"
                                size="sm"
                                className="ml-4 text-xs"
                                onClick={() => addSlot(rs.sIdx, rg.gIdx)}
                              >
                                + 添加插槽
                              </Button>
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {/* ── 016 本体覆盖声明（每节 doc-class→predicate→range 覆盖边）── */}
                    {rs.section && (
                      <SectionCoverageArea
                        docClassLabel={sourceDocClass?.label ?? null}
                        docClassIri={docClassIri}
                        coverage={rs.section.coverage ?? []}
                        relationSchema={relationSchema}
                        schemaLoading={relationSchemaQuery.isLoading}
                        pending={pendingCoverage}
                        onAdd={(b) => addCoverage(rs.sIdx!, b)}
                        onRemove={(idx) => removeCoverage(rs.sIdx!, idx)}
                        onToggleRequired={(idx) => toggleCoverageRequired(rs.sIdx!, idx)}
                        onAdoptPending={(b) => adoptPendingCoverage(rs.sIdx!, b)}
                        onIgnorePending={ignorePendingCoverage}
                      />
                    )}

                    {/* ── 015 行文 Prompt（每节一段叙述提示词）───────────── */}
                    {rs.section && (
                      <SectionPromptArea
                        value={rs.section.prompt ?? ""}
                        variables={sectionSlotLabels(rs.section)}
                        generating={promptGenerating === rs.section.section_id}
                        canGenerate={aiEnabled && !!promptSampleText}
                        onChange={(v) => updateSectionPrompt(rs.sIdx!, v)}
                        onGenerate={() => handleGeneratePrompt(rs.sIdx!)}
                        canPreview={
                          aiEnabled &&
                          !!previewJobId &&
                          !!templateId &&
                          !!(rs.section.prompt ?? "").trim()
                        }
                        previewing={previewingSection === rs.section.section_id}
                        previewText={sectionPreviews[rs.section.section_id] ?? null}
                        previewError={
                          sectionPreviewErrors[rs.section.section_id] ?? null
                        }
                        onPreview={() => handlePreviewPrompt(rs.sIdx!)}
                        onClosePreview={() =>
                          closeSectionPreview(rs.section!.section_id)
                        }
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {promptError && (
            <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              行文 Prompt 生成失败：{promptError}
            </div>
          )}

          <Button
            variant="outline"
            size="sm"
            className="w-full text-xs"
            onClick={addSection}
          >
            + 添加分节
          </Button>
        </div>
      </div>
      ) : null}

    </div>
  );
}

// 015 行文 Prompt 区：sparkles 标题 + 「从样本生成」按钮 + monospace 文本域 + 可用变量提示。
// 报告生成时后端按本节 prompt 把插槽值融合成一段叙述（generate_section_narratives）。
function SectionPromptArea({
  value,
  variables,
  generating,
  canGenerate,
  onChange,
  onGenerate,
  canPreview,
  previewing,
  previewText,
  previewError,
  onPreview,
  onClosePreview,
}: {
  value: string;
  variables: string[];
  generating: boolean;
  canGenerate: boolean;
  onChange: (v: string) => void;
  onGenerate: () => void;
  canPreview: boolean;
  previewing: boolean;
  previewText: string | null;
  previewError: string | null;
  onPreview: () => void;
  onClosePreview: () => void;
}) {
  const showPreview = previewing || previewText != null || previewError != null;
  return (
    <div className="rounded-md border bg-muted/50 p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-primary" />
        <span className="text-sm font-medium">行文 Prompt</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={onGenerate}
            disabled={!canGenerate || generating}
            title={!canGenerate ? "需开启 LLM 且有样例内容" : undefined}
          >
            {generating ? "生成中…" : "从样本生成"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={onPreview}
            disabled={!canPreview || previewing}
            title={
              !canPreview
                ? "需已在「源文档」页签关联真实文档，且行文 Prompt 非空"
                : "用已关联文档的真实抽取事实测试本节行文效果"
            }
          >
            <Eye className="size-3.5" />
            {previewing ? "预览中…" : "预览"}
          </Button>
        </div>
      </div>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="留空则本节不产出叙述文字。可用 {{插槽标签}} 引用插槽值。"
        className="min-h-[120px] resize-y bg-background font-mono text-xs leading-relaxed"
      />
      {variables.length > 0 && (
        <p className="text-xs text-muted-foreground">
          可用变量：
          {variables.map((v) => (
            <code
              key={v}
              className="mx-0.5 rounded bg-background px-1 py-0.5 font-mono"
            >{`{{${v}}}`}</code>
          ))}
          — 插槽值将在生成时替换
        </p>
      )}
      {showPreview && (
        <div className="rounded border bg-background p-2.5 space-y-1.5">
          <div className="flex items-center gap-2">
            <Eye className="size-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">
              行文预览 · 基于已关联文档的真实抽取事实
            </span>
            <button
              type="button"
              className="ml-auto text-muted-foreground hover:text-foreground"
              onClick={onClosePreview}
              title="关闭预览"
            >
              <X className="size-3.5" />
            </button>
          </div>
          {previewing ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              正在按当前行文 Prompt 生成本节正文…
            </p>
          ) : previewError ? (
            <p className="text-xs text-destructive">预览失败：{previewError}</p>
          ) : (
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/90">
              {previewText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── 016 IRI 短名助手（覆盖声明行以 local-name / slpra:* 呈现类型，非样本个体）──
function iriLocalName(iri: string): string {
  if (!iri) return "";
  const cut = Math.max(iri.lastIndexOf("#"), iri.lastIndexOf("/"));
  return iri.slice(cut + 1) || iri;
}
function shortIri(iri: string): string {
  const local = iriLocalName(iri);
  return iri.includes("/slpra/") ? `slpra:${local}` : local;
}

// 016 本体覆盖声明区（modeled on SectionPromptArea）：把 section 级
// doc-class→predicate→range 覆盖边渲染为可编辑声明行（谓词 local-name +
// arrow + 目标类型 + 必填切换 + 删除），并从只读本体的单跳关系菜单
// （getRelationSchema）新增。AI 建议的覆盖边以琥珀卡片呈现，作者采纳/忽略。
// 所有 IRI 都是类/谓词「类型」，绝非样本个体（FR-003 / SC-002）。
function SectionCoverageArea({
  docClassLabel,
  docClassIri,
  coverage,
  relationSchema,
  schemaLoading,
  pending,
  onAdd,
  onRemove,
  onToggleRequired,
  onAdoptPending,
  onIgnorePending,
}: {
  docClassLabel: string | null;
  docClassIri: string | null;
  coverage: CoverageBinding[];
  relationSchema: RelationSchemaEdge[];
  schemaLoading: boolean;
  pending: OntologyRelationBinding[];
  onAdd: (b: CoverageBinding) => void;
  onRemove: (idx: number) => void;
  onToggleRequired: (idx: number) => void;
  onAdoptPending: (b: OntologyRelationBinding) => void;
  onIgnorePending: (b: OntologyRelationBinding) => void;
}) {
  const [adding, setAdding] = useState(false);
  const relCount = coverage.filter((c) => c.kind === "ontology_relation").length;

  const handlePick = (key: string) => {
    const edge = relationSchema.find(
      (e) => `${e.predicate_iri}→${e.range_class_iri}` === key,
    );
    if (!edge || !docClassIri) return;
    onAdd({
      kind: "ontology_relation",
      doc_class_iri: docClassIri,
      predicate_iri: edge.predicate_iri,
      range_class_iri: edge.range_class_iri,
      required: true,
      label: `${edge.predicate_label} → ${edge.range_class_label}`,
    });
    setAdding(false);
  };

  return (
    <div className="rounded-md border bg-muted/50 p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <GitBranch className="size-3.5 text-primary" />
        <span className="text-sm font-medium">本体覆盖声明</span>
        {docClassLabel && (
          <Badge
            variant="outline"
            className="text-xs text-primary border-primary/40"
          >
            {docClassLabel}
          </Badge>
        )}
        {relCount > 0 && (
          <Badge variant="outline" className="text-xs">
            {relCount} 覆盖
          </Badge>
        )}
        <Button
          variant="outline"
          size="sm"
          className="ml-auto h-7 text-xs"
          onClick={() => setAdding((v) => !v)}
          disabled={!docClassIri}
          title={!docClassIri ? "需先解析文档实体类型" : undefined}
        >
          <Plus className="size-3.5 mr-1" />
          添加关系
        </Button>
      </div>

      {!docClassIri && (
        <p className="text-xs text-muted-foreground">
          未解析到文档实体类型 —— 在「源文档」页签选择匹配文档后可声明本体覆盖。
        </p>
      )}

      {adding && docClassIri && (
        <div className="rounded border bg-background p-2">
          {schemaLoading ? (
            <p className="text-xs text-muted-foreground">加载关系菜单…</p>
          ) : relationSchema.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              本体未返回可用关系边（离线，或该类型无出边）。
            </p>
          ) : (
            <Select onValueChange={handlePick}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="选择关系边（谓词 → 目标类型）" />
              </SelectTrigger>
              <SelectContent>
                {relationSchema.map((e) => (
                  <SelectItem
                    key={`${e.predicate_iri}→${e.range_class_iri}`}
                    value={`${e.predicate_iri}→${e.range_class_iri}`}
                    className="text-xs"
                  >
                    {e.predicate_label} → {e.range_class_label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )}

      {coverage.map((c, idx) =>
        c.kind === "ontology_relation" ? (
          <div
            key={`cov_${idx}`}
            className="rounded border bg-background p-2.5 space-y-1.5"
          >
            <div className="flex items-center gap-1.5 text-sm">
              <span className="font-mono text-[13px] font-medium">
                {iriLocalName(c.predicate_iri)}
              </span>
              <ArrowRight className="size-3.5 text-muted-foreground" />
              <span className="font-medium">
                {iriLocalName(c.range_class_iri)}
              </span>
              <span className="flex-1" />
              <Button
                variant={c.required ? "default" : "outline"}
                size="sm"
                className="h-6 text-xs"
                onClick={() => onToggleRequired(idx)}
                title="切换必填 / 选填（改变无遗漏清单，触发重新校验）"
              >
                {c.required ? "必填" : "选填"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                title="移除覆盖声明"
                onClick={() => onRemove(idx)}
              >
                <X className="size-3.5" />
              </Button>
            </div>
            <p className="font-mono text-[11px] text-green-600 dark:text-green-400">
              {shortIri(c.range_class_iri)}
            </p>
          </div>
        ) : (
          <div key={`cov_${idx}`} className="rounded border bg-background p-2.5">
            <div className="flex items-center gap-1.5 text-sm">
              <span className="font-mono text-[13px]">{c.source}</span>
              {c.selector && (
                <span className="text-xs text-muted-foreground">
                  · {c.selector}
                </span>
              )}
              <span className="flex-1" />
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                title="移除覆盖声明"
                onClick={() => onRemove(idx)}
              >
                <X className="size-3.5" />
              </Button>
            </div>
          </div>
        ),
      )}

      {pending.map((b) => (
        <div
          key={`pcov_${b.predicate_iri}→${b.range_class_iri}`}
          className="rounded border border-amber-400/60 bg-amber-50/50 dark:bg-amber-900/10 p-2.5 space-y-1.5"
        >
          <div className="flex items-center gap-1.5 text-sm">
            <Sparkles className="size-3.5 text-amber-600 dark:text-amber-400" />
            <span className="font-mono text-[13px] font-medium">
              {iriLocalName(b.predicate_iri)}
            </span>
            <ArrowRight className="size-3.5 text-muted-foreground" />
            <span className="font-medium">{iriLocalName(b.range_class_iri)}</span>
            <Badge
              variant="outline"
              className="ml-1 text-xs border-amber-400 text-amber-600 dark:text-amber-400"
            >
              AI 建议
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              采纳后按本体展开属性 · 默认必填
            </span>
            <span className="flex-1" />
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-xs"
              onClick={() => onIgnorePending(b)}
            >
              忽略
            </Button>
            <Button size="sm" className="h-6 text-xs" onClick={() => onAdoptPending(b)}>
              采纳
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

type EditingSlot = {
  sectionIdx: number;
  groupIdx: number;
  slotIdx: number;
  slot: SlotDef;
};

// 015/016+ 内联插槽编辑器：在选中行下方原地展开（替代原模态 Dialog）。
// 新建插槽为「语义化插槽」——prompt（留空=继承本节行文 Prompt）+ 关联本体（本节
// coverage 声明的投影，未选=全部）。旧式定型来源（抽取/规则/常量/人工/LLM 抽取）
// 只读保留以兼容既有 schema_json，标「旧式定型插槽（已弃用）」。
function SlotInlineEditor({
  editing,
  sectionCoverage,
  onChange,
  onCancel,
  onSave,
}: {
  editing: EditingSlot;
  sectionCoverage: CoverageBinding[];
  onChange: (next: EditingSlot) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const slot = editing.slot;
  const set = (patch: Partial<SlotDef>) =>
    onChange({ ...editing, slot: { ...slot, ...patch } });
  const setSource = (patch: Record<string, unknown>) =>
    onChange({ ...editing, slot: { ...slot, source: { ...slot.source, ...patch } } });
  const kind = (slot.source.kind as string) || "extraction";
  const isSemantic = kind === "semantic";

  // 语义化插槽的关联本体过滤：coverage_refs 是本节 coverage 键集，空=投影全部。
  const coverageRefs = Array.isArray(slot.source.coverage_refs)
    ? (slot.source.coverage_refs as string[])
    : [];
  const toggleRef = (key: string) =>
    setSource({
      coverage_refs: coverageRefs.includes(key)
        ? coverageRefs.filter((k) => k !== key)
        : [...coverageRefs, key],
    });
  const bindingLabel = (b: CoverageBinding): string =>
    b.kind === "fact_source"
      ? b.label || b.source
      : b.label ||
        `${iriLocalName(b.predicate_iri)} → ${iriLocalName(b.range_class_iri)}`;

  return (
    <div
      className="ml-4 mt-1 space-y-3 rounded-md border bg-muted/50 p-3"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex gap-2.5">
        <Field label="插槽 ID">
          <Input
            className="h-8 font-mono text-xs"
            value={slot.slot_id}
            onChange={(e) => set({ slot_id: e.target.value })}
          />
        </Field>
        <Field label="标签">
          <Input
            className="h-8"
            value={slot.label}
            onChange={(e) => set({ label: e.target.value })}
          />
        </Field>
      </div>

      {isSemantic ? (
        <>
          <Field label="插槽 Prompt（留空 = 继承本节「行文 Prompt」）">
            <Textarea
              value={(slot.source.prompt as string) ?? ""}
              onChange={(e) => setSource({ prompt: e.target.value || null })}
              placeholder="规定本语义化插槽的输出内容；留空则生成时继承本节「行文 Prompt」。"
              className="min-h-[96px] resize-y bg-background text-xs leading-relaxed"
            />
          </Field>

          <div className="space-y-1.5">
            <Label className="text-xs font-medium">
              关联本体（本节覆盖声明的投影 · 未选 = 投影全部）
            </Label>
            {sectionCoverage.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                本节尚无「本体覆盖声明」。在下方覆盖区添加关系边后，可在此选择要纳入本插槽的关联本体。
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {sectionCoverage.map((b, i) => {
                  const key = coverageKey(b);
                  const explicit = coverageRefs.includes(key);
                  const projectedAll = coverageRefs.length === 0;
                  return (
                    <button
                      key={`${key}_${i}`}
                      type="button"
                      onClick={() => toggleRef(key)}
                      title={key}
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-xs transition-colors",
                        explicit
                          ? "border-primary bg-primary/10 text-primary"
                          : projectedAll
                            ? "border-dashed border-primary/40 text-muted-foreground hover:text-primary"
                            : "border-border text-muted-foreground hover:border-primary/40",
                      )}
                    >
                      {bindingLabel(b)}
                    </button>
                  );
                })}
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {coverageRefs.length === 0
                ? "未显式选择 → 生成时投影本节全部关联本体与事实源。"
                : `已选 ${coverageRefs.length} 项 → 仅投影所选关联本体。`}
            </p>
          </div>
        </>
      ) : (
        <div className="space-y-2 rounded-md border border-dashed bg-background/60 p-2.5">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-xs">
              旧式定型插槽（已弃用）
            </Badge>
            <span className="text-xs text-muted-foreground">
              来源类型：{sourceKindLabel(kind)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            旧式定型来源（抽取 / 规则 / 常量 / 人工 / LLM 抽取）只读保留以兼容既有模板；新插槽请改用语义化插槽（prompt + 关联本体）。
          </p>
          {kind === "extraction" && (slot.source.object_class_iri_contains as string) && (
            <p className="font-mono text-[11px] text-muted-foreground">
              本体类 IRI：{slot.source.object_class_iri_contains as string}
            </p>
          )}
        </div>
      )}

      <div className="flex gap-2.5">
        <Field label="必填">
          <Select
            value={slot.required ? "true" : "false"}
            onValueChange={(v) => set({ required: v === "true" })}
          >
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">是</SelectItem>
              <SelectItem value="false">否</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field label="缺失处理">
          <Select value={slot.on_missing} onValueChange={(v) => set({ on_missing: v })}>
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="annotate">标注</SelectItem>
              <SelectItem value="leave_blank">留空</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      </div>

      {slot.on_missing === "annotate" && (
        <Field label="缺失占位文字">
          <Input
            className="h-8"
            value={slot.missing_placeholder}
            onChange={(e) => set({ missing_placeholder: e.target.value })}
          />
        </Field>
      )}

      <div className="flex justify-end gap-2 pt-0.5">
        <Button variant="outline" size="sm" className="h-8" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" className="h-8" onClick={onSave}>
          确定
        </Button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex-1 space-y-1">
      <Label className="text-xs font-medium">{label}</Label>
      {children}
    </div>
  );
}

// ── 015 报告预览：覆盖率徽章（与 [jobId]/ast 同源） ────────────────────────
function CoverageBadge({
  tone,
  count,
  onClick,
}: {
  tone: "success" | "destructive" | "muted";
  count: number;
  onClick?: () => void;
}) {
  const dot = tone === "success" ? "bg-success" : tone === "destructive" ? "bg-destructive" : "bg-muted-foreground";
  const bg = tone === "success" ? "bg-success/10" : tone === "destructive" ? "bg-destructive/10" : "bg-muted";
  const text = tone === "success" ? "text-success" : tone === "destructive" ? "text-destructive" : "text-muted-foreground";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "flex items-center gap-1 rounded-full px-2 py-0.5",
        bg,
        onClick ? "cursor-pointer hover:opacity-80" : "cursor-default",
      )}
    >
      <span className={cn("size-1.5 rounded-full", dot)} />
      <span className={cn("text-[11px] font-semibold", text)}>{count}</span>
    </button>
  );
}

// ── 015 报告预览：生成进度面板（5 步状态与 [jobId]/ast 同源） ────────────────
type StepState = "done" | "active" | "pending";
interface GenStep { title: string; desc: string; state: StepState; detail?: ReactNode }

function ReportProgressPanel({
  coverage,
  reports,
  generating,
  refreshing = false,
}: {
  coverage: ASTCoverageDTO;
  reports: GeneratedReportDTO[];
  generating: boolean;
  // 刷新覆盖率进行中：重跑数据抽取解析 / 模板匹配 / 覆盖率分析三步，进度面板同步呈现「重新分析中」。
  refreshing?: boolean;
}) {
  const completed = coverage.filled + coverage.inferred;
  const totalSlots = coverage.total_slots;
  const missing = coverage.missing_required;
  const dismissed = coverage.dismissed;
  const hasReport = reports.length > 0;

  // 刷新时前三步回到 active 并显示「重新分析中…」；完成后重新落定为已完成的新结果。
  const analysisState: StepState = refreshing ? "active" : "done";
  const steps: GenStep[] = [
    {
      title: "数据抽取解析",
      desc: refreshing ? "重新分析中…" : "已完成",
      state: analysisState,
    },
    {
      title: "模板匹配",
      desc: refreshing
        ? "重新分析中…"
        : coverage.template_name
          ? `已完成 · 命中模板「${coverage.template_name}${coverage.template_version ? ` ${coverage.template_version}` : ""}」`
          : "已完成 · 使用默认模板",
      state: analysisState,
    },
    {
      title: "覆盖率分析",
      desc: refreshing
        ? "重新分析中…"
        : `已完成 · ${completed}/${totalSlots} 插槽已填充${missing > 0 ? `，${missing} 项必填缺失` : ""}`,
      state: analysisState,
      detail: refreshing ? undefined : (
        <div className="mt-1 space-y-1.5 rounded-lg border bg-muted p-3">
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">已填充插槽</span>
            <span className="font-semibold text-foreground">{completed} / {totalSlots}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">缺失必填项</span>
            <span className={cn("font-semibold", missing > 0 ? "text-destructive" : "text-foreground")}>{missing}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">已忽略</span>
            <span className="font-semibold text-foreground">{dismissed}</span>
          </div>
        </div>
      ),
    },
    {
      title: "AI 行文生成",
      desc: hasReport ? "已完成" : generating ? "生成中…" : "等待中",
      state: hasReport ? "done" : generating ? "active" : "pending",
    },
    {
      title: "DOCX 报告渲染",
      desc: hasReport ? "已完成 · 已生成 Word 文档" : generating ? "生成中…" : "等待中",
      state: hasReport ? "done" : generating ? "active" : "pending",
    },
  ];

  const doneCount = steps.filter((s) => s.state === "done").length;
  const pct = Math.round((doneCount / steps.length) * 100);

  return (
    <div className="flex flex-col gap-5 p-6 lg:border-r">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[15px] font-semibold text-foreground">生成进度</span>
          <span className="text-sm font-semibold text-primary">{pct}%</span>
        </div>
        <Progress value={pct} className="h-2" />
      </div>
      <div className="flex flex-col">
        {steps.map((s, i) => (
          <div key={s.title} className="flex gap-4">
            <div className="flex flex-col items-center gap-1">
              <div className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full",
                s.state === "done" && "bg-success text-success-foreground",
                s.state === "active" && "bg-primary text-primary-foreground",
                s.state === "pending" && "border-2 border-border",
              )}>
                {s.state === "done" && <Check className="size-4" />}
                {s.state === "active" && <Loader2 className="size-4 animate-spin" />}
              </div>
              {i < steps.length - 1 && (
                <div className={cn("min-h-8 w-0.5 flex-1", s.state === "done" ? "bg-success" : "bg-border")} />
              )}
            </div>
            <div className={cn("flex-1 space-y-1", i === steps.length - 1 ? "pb-1" : "pb-3")}>
              <p className={cn(
                "text-sm",
                s.state === "pending" ? "font-medium text-muted-foreground"
                  : s.state === "active" ? "font-semibold text-primary"
                  : "font-semibold text-foreground",
              )}>{s.title}</p>
              <p className="text-xs text-muted-foreground">{s.desc}</p>
              {s.detail}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
