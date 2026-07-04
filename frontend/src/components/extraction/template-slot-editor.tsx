"use client";

import { useState, useCallback, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles, GripVertical, Pencil, Trash2, MoreVertical, FileText, LayoutTemplate, Eye, Info, Loader2, Upload, Save, FileDown, Download, Check, ListTree } from "lucide-react";
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
import { ASTTreeView } from "./ast-tree-view";
import { SlotDetailPanel } from "./slot-detail-panel";
import { SlotActionBar } from "./slot-action-bar";
import { ReportHistoryList } from "./report-history-list";
import {
  suggestSlots,
  generateSectionPrompt,
  getAnnotatedDocument,
  listDocuments,
  updateAstTemplateMeta,
  uploadTemplateSample,
  uploadDefaultSource,
  deleteDefaultSource,
  listTrainingPairs,
  uploadTrainingPair,
  deleteTrainingPair,
  type SuggestSlotsRequest,
  type SuggestedSlot,
  type TiptapContent,
  type EntityShadow,
  type DocClassification,
  type Relationship,
  type TrainingPairDTO,
  type AstTemplateStatus,
  DOCUMENT_TYPE_GROUPS,
  getAstCoverage,
  listReports,
  dismissSlot,
  undismissSlot,
  generateRiskReport,
  downloadReport,
  rerunAnnotation,
  type ASTCoverageDTO,
  type SlotCoverageDTO,
  type GeneratedReportDTO,
} from "@/lib/api";

interface SlotDef {
  slot_id: string;
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
  title: string;
  kind: string;
  repeat?: Record<string, unknown> | null;
  slots: SlotDef[];
}

interface SectionDef {
  section_id: string;
  title: string;
  groups: GroupDef[];
  // 015 行文 Prompt：报告生成时用于把本节插槽值融合成一段叙述文字的提示词。
  // 空/缺失 → 该节不产出叙述（generate_section_narratives 会跳过）。
  prompt?: string | null;
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
}

interface TemplateVersionEntry {
  id: string;
  version: string;
  created_at: string;
}

// 015 基本信息表单模型（由 page 从模板详情构造）。
interface TemplateMeta {
  name: string;
  docNo: string | null;
  version: string;
  status: AstTemplateStatus;
  iriPattern: string | null;
  owner: string | null;
  updatedAt: string | null;
  defaultSourceFilename: string | null;
  sampleConfigured: boolean; // 是否已配置默认示例文档（sample_content_json 非空）
}

// 旧模板仅存扁平 sample_text 时，按行包装成最小 tiptap 文档（段落），
// 使 legacy 模板在左侧预览中也有段落级 evidence 高亮联动。
function wrapTextAsTiptap(text: string): TiptapContent {
  const paragraphs = text.split(/\n+/).filter((line) => line.trim());
  return {
    type: "doc",
    content: paragraphs.map((line) => ({
      type: "paragraph",
      content: [{ type: "text", text: line }],
    })),
  };
}

function cloneSections(sections: SectionDef[]): SectionDef[] {
  return JSON.parse(JSON.stringify(sections)) as SectionDef[];
}

// IRI/类 IRI 的本地名（末段），用于源文档列表的次要标注。
function docLocalName(iri: string | null | undefined): string {
  if (!iri) return "";
  const parts = iri.split(/[#/]/).filter(Boolean);
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

// AI 建议 → 真实 Slot 的映射（与原创建流程一致：本体绑定走 extraction，否则 manual）。
function suggestionToSlot(slot: SuggestedSlot): SlotDef {
  return {
    slot_id: slot.slot_id,
    label: slot.label,
    source: {
      kind: slot.source_kind === "extraction" ? "extraction" : "manual",
      ...(slot.source_hint ? { object_class_iri_contains: slot.source_hint } : {}),
      text: true,
    },
    required: false,
    on_missing: "annotate",
    missing_placeholder: "⚠ 待评估（数据缺失）",
  };
}

const SOURCE_KINDS = [
  { value: "extraction", label: "抽取" },
  { value: "rule", label: "规则" },
  { value: "manual", label: "手工" },
  { value: "constant", label: "常量" },
];

// ── 渲染模型：把真实 sections 与 pending AI 建议合并成一棵树，建议以幽灵行内嵌
// 到对应 section→group 下（缺失时以虚拟容器占位），实现「在 Slot 树上审核采纳」。
interface RenderGroup {
  title: string;
  group: GroupDef | null; // null = 虚拟（仅承载 pending 建议）
  sIdx: number | null;
  gIdx: number | null;
  pending: SuggestedSlot[];
}
interface RenderSection {
  title: string;
  section: SectionDef | null;
  sIdx: number | null;
  groups: RenderGroup[];
}

function buildTree(sections: SectionDef[], pending: SuggestedSlot[]): RenderSection[] {
  const result: RenderSection[] = sections.map((sec, si) => ({
    title: sec.title,
    section: sec,
    sIdx: si,
    groups: sec.groups.map((g, gi) => ({
      title: g.title,
      group: g,
      sIdx: si,
      gIdx: gi,
      pending: [] as SuggestedSlot[],
    })),
  }));
  for (const p of pending) {
    let rs = result.find((r) => r.title === p.section);
    if (!rs) {
      rs = { title: p.section, section: null, sIdx: null, groups: [] };
      result.push(rs);
    }
    let rg = rs.groups.find((g) => g.title === p.group);
    if (!rg) {
      rg = { title: p.group, group: null, sIdx: rs.sIdx, gIdx: null, pending: [] };
      rs.groups.push(rg);
    }
    rg.pending.push(p);
  }
  return result;
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
  const [newSlotIds, setNewSlotIds] = useState<Set<string>>(new Set());

  // ── 015 行文 Prompt（每节一段叙述提示词，可从样本生成）────────────────
  const [promptGenerating, setPromptGenerating] = useState<string | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);

  // ── AI 分析（内联，替代原独立 drawer）──────────────────────────────
  const [pending, setPending] = useState<SuggestedSlot[]>([]);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  const [aiSkipped, setAiSkipped] = useState(0);

  // 左侧忠实预览的高亮锚点：点击建议→其 source_ref/evidence_span；点击真实 Slot→其 label（尽力而为）。
  const [activeRef, setActiveRef] = useState<string | null>(null);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);

  // job_id 分支的忠实预览（模板流程通常不传 jobId；保留以不回退能力）。
  const [jobContent, setJobContent] = useState<TiptapContent | null>(null);
  useEffect(() => {
    if (jobId && !sampleContentJson) {
      getAnnotatedDocument(jobId)
        .then((doc) => setJobContent((doc.content as TiptapContent) ?? null))
        .catch(() => setJobContent(null));
    }
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
    setMetaSaving(true);
    setMetaError(null);
    setMetaJustSaved(false);
    try {
      await updateAstTemplateMeta(templateId, {
        name: metaForm.name.trim(),
        doc_no: metaForm.docNo.trim() || null,
        owner: metaForm.owner.trim() || null,
        status: metaForm.status,
        iri_pattern: metaForm.iriPattern || null,
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
        setDocError("示例文档替换失败（需 .docx）");
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
      selectedDocIri && matchedDocs.some((d) => d.iri === selectedDocIri)
        ? selectedDocIri
        : matchedDocs[0]?.iri ?? null,
    [selectedDocIri, matchedDocs],
  );
  const activeShadow = useMemo(
    () => (activeDocIri ? docs.find((d) => d.iri === activeDocIri) ?? null : null),
    [activeDocIri, docs],
  );

  // 选中真实文档 → 按 job 引用取回正文（尽力而为，降级为「不可预览」；绝不抛错）。
  const docContentQuery = useQuery({
    queryKey: ["ast-source-doc-content", activeDocIri],
    enabled: Boolean(activeDocIri),
    queryFn: async (): Promise<DocPreviewState> => {
      const jobRef = docJobRef(activeShadow ?? undefined);
      if (!jobRef) return { kind: "unavailable" };
      try {
        const doc = await getAnnotatedDocument(jobRef);
        if (doc.content && typeof doc.content === "object") {
          return {
            kind: "ready",
            content: doc.content as TiptapContent,
            docClass: doc.doc_class ?? null,
            relationships: doc.relationships ?? [],
          };
        }
      } catch {
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

  // ── 015 报告预览页签：AST 覆盖率分析（迁移自 /entities/extraction/[jobId]/ast）。
  // 从匹配文档解析 jobId，用当前模板计算覆盖率，支持生成/下载报告。
  const queryClient = useQueryClient();
  const previewJobId = useMemo(() => docJobRef(activeShadow ?? undefined), [activeShadow]);
  const [previewSlot, setPreviewSlot] = useState<SlotCoverageDTO | null>(null);
  const [previewScrollSlot, setPreviewScrollSlot] = useState<string | null>(null);
  const [previewHighlightRef, setPreviewHighlightRef] = useState<string | undefined>(undefined);
  const [confirmGenOpen, setConfirmGenOpen] = useState(false);

  const coverageQuery = useQuery({
    queryKey: ["ast-coverage", previewJobId, templateId ?? "default"],
    queryFn: () => getAstCoverage(previewJobId!, templateId),
    enabled: !!previewJobId,
  });
  const previewReportsQuery = useQuery({
    queryKey: ["reports", previewJobId],
    queryFn: () => listReports(previewJobId!),
    enabled: !!previewJobId,
  });
  const previewCoverage = coverageQuery.data ?? null;
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
    mutationFn: () => generateRiskReport(previewJobId!),
    onSuccess: async (result) => {
      if (result instanceof Blob) {
        const url = URL.createObjectURL(result);
        const a = document.createElement("a");
        a.href = url;
        a.download = `risk-report-${(previewJobId ?? "").slice(0, 8)}.docx`;
        a.click();
        URL.revokeObjectURL(url);
      }
      queryClient.invalidateQueries({ queryKey: ["reports", previewJobId] });
    },
  });
  const rerunMut = useMutation({
    mutationFn: () => rerunAnnotation(previewJobId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ast-coverage", previewJobId] });
    },
  });

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
        source: { kind: "extraction", object_class_iri_contains: "", text: true },
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

  function updateSectionPrompt(sectionIdx: number, value: string) {
    setSections((prev) => {
      const next = cloneSections(prev);
      next[sectionIdx].prompt = value;
      return next;
    });
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

  // 采纳建议入树：section/group 缺失则创建（沿用原创建流程的 create-if-missing 合并），
  // 已存在同 slot_id 则跳过；采纳后从 pending 移除并高亮（绿环）。
  function adoptSuggestions(sugs: SuggestedSlot[]) {
    if (sugs.length === 0) return;
    const next = cloneSections(sections);
    const expandSec = new Set(expandedSections);
    const expandGrp = new Set(expandedGroups);
    const addedIds = new Set<string>();
    const ts = Date.now();
    let counter = 0;
    for (const slot of sugs) {
      let section = next.find((s) => s.title === slot.section);
      if (!section) {
        section = { section_id: `ai_sec_${ts}_${counter++}`, title: slot.section, groups: [] };
        next.push(section);
      }
      let group = section.groups.find((g) => g.title === slot.group);
      if (!group) {
        group = { group_id: `ai_grp_${ts}_${counter++}`, title: slot.group, kind: "fields", slots: [] };
        section.groups.push(group);
      }
      if (!group.slots.some((s) => s.slot_id === slot.slot_id)) {
        group.slots.push(suggestionToSlot(slot));
        addedIds.add(slot.slot_id);
      }
      expandSec.add(section.section_id);
      expandGrp.add(group.group_id);
    }
    setSections(next);
    setExpandedSections(expandSec);
    setExpandedGroups(expandGrp);
    setNewSlotIds((prev) => new Set([...prev, ...addedIds]));
    setPending((prev) => prev.filter((p) => !sugs.includes(p)));
  }

  function rejectSuggestion(sug: SuggestedSlot) {
    setPending((prev) => prev.filter((p) => p !== sug));
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
  }, [jobId, sampleContentJson, sampleText, sections]);

  async function runAiAnalysis() {
    const req = buildAiRequest();
    if (!req) {
      setAiError("无可分析的样例内容");
      return;
    }
    setAiLoading(true);
    setAiError(null);
    try {
      const res = await suggestSlots(req);
      const existingIds = new Set<string>();
      sections.forEach((s) =>
        s.groups.forEach((g) => g.slots.forEach((sl) => existingIds.add(sl.slot_id))),
      );
      const seen = new Set<string>();
      const flat: SuggestedSlot[] = [];
      for (const sec of res.sections) {
        for (const grp of sec.groups) {
          for (const sl of grp.slots) {
            if (existingIds.has(sl.slot_id) || seen.has(sl.slot_id)) continue;
            seen.add(sl.slot_id);
            flat.push(sl);
          }
        }
      }
      setPending(flat);
      setAiSummary(res.document_summary || null);
      setAiSkipped(res.skipped_duplicates || 0);
    } catch (e) {
      setAiError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiLoading(false);
    }
  }

  function handleSlotClick(slot: SlotDef) {
    setActiveSlotId(slot.slot_id);
    // 真实 Slot 无 evidence，用 label 尽力定位（WordViewer 按 textContent 命中）。
    setActiveRef(slot.label || null);
  }

  function handlePendingClick(sug: SuggestedSlot) {
    setActiveSlotId(sug.slot_id);
    setActiveRef(sug.source_ref ?? sug.evidence_span ?? null);
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

  const tree = useMemo(() => buildTree(sections, pending), [sections, pending]);

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
    rs.section ? expandedSections.has(rs.section.section_id) : true;
  const isGrpExpanded = (rg: RenderGroup) =>
    rg.group ? expandedGroups.has(rg.group.group_id) : true;

  return (
    <div className="flex h-full min-h-0">
      {/* ── Left: 多页签预览面板 ──────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col border-r">
        <Tabs value={leftTab} onValueChange={setLeftTab} className="flex flex-col h-full">
          <div className="shrink-0 border-b px-4 pt-2">
            <TabsList className="h-8">
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
                  accept=".docx"
                  className="hidden"
                  onChange={(e) => {
                    void handleSampleFile(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
                <input
                  ref={sourceInputRef}
                  type="file"
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
                        onChange={(e) =>
                          setMetaForm((f) => ({ ...f, name: e.target.value }))
                        }
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">模板编号</Label>
                      <Input
                        value={metaForm.docNo}
                        onChange={(e) =>
                          setMetaForm((f) => ({ ...f, docNo: e.target.value }))
                        }
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
                          <SelectTrigger>
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
                        onChange={(e) =>
                          setMetaForm((f) => ({ ...f, owner: e.target.value }))
                        }
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">状态</Label>
                      <Select
                        value={metaForm.status}
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
                      <Label className="text-xs text-muted-foreground">关联文档类型</Label>
                      <Select
                        value={metaForm.iriPattern || "__none__"}
                        onValueChange={(v) =>
                          setMetaForm((f) => ({ ...f, iriPattern: v === "__none__" ? "" : v }))
                        }
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="选择文档类型…" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">未指定</SelectItem>
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
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">IRI 匹配键</Label>
                      <Input
                        value={metaForm.iriPattern ? docLocalName(metaForm.iriPattern) : "—"}
                        disabled
                        readOnly
                        className="text-muted-foreground"
                      />
                    </div>
                  </div>
                </section>

                {/* ── 文档配置 ─────────────────────────────────────────── */}
                <section className="grid grid-cols-2 gap-4">
                  {docError && (
                    <div className="col-span-2 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                      {docError}
                    </div>
                  )}
                  {/* 默认模板示例文档 */}
                  <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                    <div className="flex flex-col gap-0.5">
                      <div className="text-sm font-semibold text-foreground">
                        默认模板示例文档
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
                  {/* 默认源文件 */}
                  <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                    <div className="flex flex-col gap-0.5">
                      <div className="text-sm font-semibold text-foreground">
                        默认源文件
                      </div>
                      <p className="text-xs text-muted-foreground">
                        固化输出格式的参照原件
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
                            评估报告（可选）
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
                  {activeDocIri && (
                    <span className="max-w-[60%] truncate font-mono text-xs text-muted-foreground">
                      {docLocalName(activeDocIri)}
                    </span>
                  )}
                </div>

                <div className="rounded border bg-card p-6 shadow-sm">
                  {docContent.kind === "ready" ? (
                    <WordViewer
                      key={activeDocIri}
                      content={docContent.content}
                      highlightRef={selectedSourceRef}
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

          <TabsContent
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
              <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
                <Info className="size-5 text-muted-foreground/60" />
                <p className="text-sm text-muted-foreground">
                  {coverageQuery.error ? "该文档类型不支持覆盖率分析" : "暂无覆盖率数据"}
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-4 px-6 py-4">
                {/* 操作栏 */}
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">
                    AST 覆盖率
                  </span>
                  <Button size="sm" onClick={handlePreviewGenerate} disabled={generateMut.isPending}>
                    {generateMut.isPending ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <FileDown className="mr-1 size-3.5" />}
                    {generateMut.isPending ? "生成中..." : "生成报告"}
                  </Button>
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
                    generating={generateMut.isPending}
                  />

                  {/* 右：报告结构树 */}
                  <div className="flex min-h-0 flex-col">
                    <div className="flex items-center justify-between border-b px-5 py-3">
                      <div className="flex items-center gap-2">
                        <ListTree className="size-4 text-foreground" />
                        <span className="text-sm font-semibold text-foreground">报告结构</span>
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
                              onRerun={() => rerunMut.mutate()}
                              rerunning={rerunMut.isPending}
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
          </TabsContent>
        </Tabs>
      </div>

      {/* ── Right: 基本信息/报告预览 → 无（左侧全宽）；源文档 → 关系图谱；其余 → Slot 树 + 内联 AI ── */}
      {leftTab === "basic" || leftTab === "report-preview" ? null : leftTab === "source" ? (
        <div className="flex w-[26rem] shrink-0 flex-col min-h-0 border-l">
          <div className="shrink-0 border-b px-4 py-3">
            <div className="text-sm font-semibold text-foreground">关系图谱</div>
            <p className="text-xs text-muted-foreground">
              文档中识别的关系，点击端点可定位原文
            </p>
          </div>
          {/* 单一滚动区归 RelationPanel 内部（flex-1 overflow-y-auto）；外层仅定界高度，
              避免嵌套滚动条。 */}
          <div className="min-h-0 flex-1">
            <RelationPanel
              docClass={sourceDocClass}
              relationships={sourceRelationships}
              selectedSourceRef={selectedSourceRef}
              onSelectSourceRef={setSelectedSourceRef}
            />
          </div>
        </div>
      ) : (
      <div className="flex w-[26rem] shrink-0 flex-col min-h-0">
        <div className="shrink-0 border-b px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <div className="text-sm text-muted-foreground">
              {totalSlots} 插槽 · {requiredCount} 必填
              {disabledCount > 0 && ` · ${disabledCount} 禁用`}
            </div>
            {pending.length > 0 && (
              <Badge
                variant="outline"
                className="border-amber-400 text-amber-600 dark:text-amber-400"
              >
                AI 建议 {pending.length}
              </Badge>
            )}
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
              disabled={!aiEnabled || aiLoading || !previewContent}
              title={
                !aiEnabled
                  ? "需在设置中开启 LLM 插槽建议"
                  : !previewContent
                    ? "无样例内容可分析"
                    : undefined
              }
            >
              {aiLoading ? "分析中…" : "AI 分析"}
            </Button>
            {pending.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => adoptSuggestions([...pending])}
              >
                全部采纳（{pending.length}）
              </Button>
            )}
            {aiSkipped > 0 && (
              <span className="text-xs text-muted-foreground">
                已跳过 {aiSkipped} 条重复
              </span>
            )}
          </div>
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
                ? "点击「AI 分析」从样例文档生成插槽建议，或「添加分节」手动创建结构。"
                : "该模板暂无插槽。点击「AI 分析」或「添加分节」开始。"}
            </div>
          )}

          {tree.map((rs) => {
            const realCount = rs.section
              ? rs.section.groups.reduce((a, g) => a + g.slots.length, 0)
              : 0;
            const secPending = rs.groups.reduce((a, g) => a + g.pending.length, 0);
            const virtualSection = !rs.section;
            return (
              <div
                key={rs.section?.section_id ?? `virt_${rs.title}`}
                className={`border rounded-lg${virtualSection ? " border-dashed border-amber-400/60" : ""}`}
              >
                <button
                  className="w-full flex items-center gap-2 p-3 hover:bg-muted/50 text-left"
                  onClick={() => rs.section && toggleSection(rs.section.section_id)}
                  disabled={virtualSection}
                >
                  {!virtualSection && (
                    <span className="text-xs">{isSecExpanded(rs) ? "▼" : "▶"}</span>
                  )}
                  <span className="font-medium">{rs.title}</span>
                  {virtualSection && (
                    <Badge
                      variant="outline"
                      className="text-xs border-amber-400 text-amber-600 dark:text-amber-400"
                    >
                      AI
                    </Badge>
                  )}
                  <div className="ml-auto flex items-center gap-1">
                    {rs.section?.prompt?.trim() && (
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
                    {secPending > 0 && (
                      <Badge
                        variant="outline"
                        className="text-xs border-amber-400 text-amber-600 dark:text-amber-400"
                      >
                        +{secPending}
                      </Badge>
                    )}
                  </div>
                </button>

                {isSecExpanded(rs) && (
                  <div className="px-3 pb-3 space-y-2">
                    {rs.groups.map((rg) => {
                      const virtualGroup = !rg.group;
                      return (
                        <div
                          key={rg.group?.group_id ?? `virt_${rs.title}_${rg.title}`}
                          className={`border rounded ml-4${virtualGroup ? " border-dashed border-amber-400/60" : ""}`}
                        >
                          <button
                            className="w-full flex items-center gap-2 p-2 hover:bg-muted/50 text-left text-sm"
                            onClick={() => rg.group && toggleGroup(rg.group.group_id)}
                            disabled={virtualGroup}
                          >
                            {!virtualGroup && (
                              <span className="text-xs">{isGrpExpanded(rg) ? "▼" : "▶"}</span>
                            )}
                            <span>{rg.title}</span>
                            {rg.group && (
                              <Badge variant="outline" className="text-xs ml-1">
                                {rg.group.kind}
                              </Badge>
                            )}
                            <span className="ml-auto text-xs text-muted-foreground">
                              {rg.group ? `${rg.group.slots.length} 插槽` : ""}
                              {rg.pending.length > 0 && (
                                <span className="text-amber-600 dark:text-amber-400">
                                  {rg.group ? " · " : ""}+{rg.pending.length}
                                </span>
                              )}
                            </span>
                          </button>

                          {isGrpExpanded(rg) && (
                            <div className="px-2 pb-2 space-y-1">
                              {/* 真实 Slot 行 + 内联编辑器（选中时在行下方原地展开）*/}
                              {rg.group &&
                                rg.group.slots.map((slot, slIdx) => {
                                  const isEditing =
                                    editingSlot?.sectionIdx === rs.sIdx &&
                                    editingSlot?.groupIdx === rg.gIdx &&
                                    editingSlot?.slotIdx === slIdx;
                                  const kind = (slot.source as Record<string, unknown>)
                                    .kind as string;
                                  const isLast = slIdx === rg.group!.slots.length - 1;
                                  return (
                                    <div key={slot.slot_id}>
                                      <div
                                        className={cn(
                                          "group ml-4 flex items-center gap-1.5 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted/40",
                                          newSlotIds.has(slot.slot_id) &&
                                            "ring-1 ring-green-400 bg-green-50 dark:bg-green-900/20",
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
                                        <Badge variant="outline" className="text-xs">
                                          {slot.disabled ? "已禁用" : kind}
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
                                          onChange={setEditingSlot}
                                          onCancel={() => setEditingSlot(null)}
                                          onSave={saveSlotEdit}
                                        />
                                      )}
                                    </div>
                                  );
                                })}

                              {/* Pending AI 建议幽灵行 */}
                              {rg.pending.map((sug) => (
                                <div
                                  key={`pending_${sug.slot_id}`}
                                  className={`flex items-start gap-2 ml-4 p-1.5 rounded border border-dashed border-amber-400/60 bg-amber-50/50 dark:bg-amber-900/10 text-sm cursor-pointer${
                                    activeSlotId === sug.slot_id
                                      ? " ring-1 ring-amber-400"
                                      : ""
                                  }`}
                                  onClick={() => handlePendingClick(sug)}
                                >
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-1">
                                      <span className="truncate">{sug.label}</span>
                                      <ConfidenceBadge value={sug.confidence} />
                                    </div>
                                    {sug.evidence_span && (
                                      <p className="text-xs text-muted-foreground truncate">
                                        {sug.evidence_span}
                                      </p>
                                    )}
                                    {sug.source_kind === "extraction" && sug.source_hint && (
                                      <p className="text-xs text-green-600 dark:text-green-400 truncate">
                                        IRI: {sug.source_hint}
                                      </p>
                                    )}
                                  </div>
                                  <Badge
                                    variant={sug.source_kind === "extraction" ? "default" : "outline"}
                                    className="shrink-0 text-xs"
                                  >
                                    {sug.source_kind === "extraction" ? "本体" : "LLM"}
                                  </Badge>
                                  <div
                                    className="flex gap-0.5 shrink-0"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-6 w-6 p-0 text-green-600"
                                      title="采纳到插槽树"
                                      onClick={() => adoptSuggestions([sug])}
                                    >
                                      ✓
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-6 w-6 p-0 text-red-500"
                                      title="忽略此建议"
                                      onClick={() => rejectSuggestion(sug)}
                                    >
                                      ✗
                                    </Button>
                                  </div>
                                </div>
                              ))}

                              {rg.group && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="ml-4 text-xs"
                                  onClick={() => addSlot(rs.sIdx!, rg.gIdx!)}
                                >
                                  + 添加插槽
                                </Button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {/* ── 015 行文 Prompt（每节一段叙述提示词）───────────── */}
                    {rs.section && rs.sIdx !== null && (
                      <SectionPromptArea
                        value={rs.section.prompt ?? ""}
                        variables={sectionSlotLabels(rs.section)}
                        generating={promptGenerating === rs.section.section_id}
                        canGenerate={aiEnabled && !!promptSampleText}
                        onChange={(v) => updateSectionPrompt(rs.sIdx!, v)}
                        onGenerate={() => handleGeneratePrompt(rs.sIdx!)}
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
      )}

    </div>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const variant = pct >= 80 ? "default" : pct >= 50 ? "secondary" : "outline";
  return (
    <Badge variant={variant} className="text-xs shrink-0">
      {pct}%
    </Badge>
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
}: {
  value: string;
  variables: string[];
  generating: boolean;
  canGenerate: boolean;
  onChange: (v: string) => void;
  onGenerate: () => void;
}) {
  return (
    <div className="rounded-md border bg-muted/50 p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-primary" />
        <span className="text-sm font-medium">行文 Prompt</span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto h-7 text-xs"
          onClick={onGenerate}
          disabled={!canGenerate || generating}
          title={!canGenerate ? "需开启 LLM 且有样例内容" : undefined}
        >
          {generating ? "生成中…" : "从样本生成"}
        </Button>
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
    </div>
  );
}

type EditingSlot = {
  sectionIdx: number;
  groupIdx: number;
  slotIdx: number;
  slot: SlotDef;
};

// 015 内联插槽编辑器：在选中行下方原地展开（替代原模态 Dialog），字段按 design
// 的两列栅格排布 —— 插槽 ID | 标签 / 来源类型 | 本体类 IRI / 必填 | 缺失处理。
function SlotInlineEditor({
  editing,
  onChange,
  onCancel,
  onSave,
}: {
  editing: EditingSlot;
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

      <div className="flex gap-2.5">
        <Field label="来源类型">
          <Select value={kind} onValueChange={(v) => setSource({ kind: v })}>
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SOURCE_KINDS.map((sk) => (
                <SelectItem key={sk.value} value={sk.value}>
                  {sk.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="本体类 IRI">
          {kind === "extraction" ? (
            <Input
              className="h-8 font-mono text-xs"
              placeholder="例如 DrugProduct"
              value={(slot.source.object_class_iri_contains as string) ?? ""}
              onChange={(e) => setSource({ object_class_iri_contains: e.target.value })}
            />
          ) : (
            <div className="flex h-8 items-center rounded-md border border-dashed px-3 text-xs text-muted-foreground">
              仅抽取来源适用
            </div>
          )}
        </Field>
      </div>

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
}: {
  coverage: ASTCoverageDTO;
  reports: GeneratedReportDTO[];
  generating: boolean;
}) {
  const completed = coverage.filled + coverage.inferred;
  const totalSlots = coverage.total_slots;
  const missing = coverage.missing_required;
  const dismissed = coverage.dismissed;
  const hasReport = reports.length > 0;

  const steps: GenStep[] = [
    { title: "数据抽取解析", desc: "已完成", state: "done" },
    {
      title: "模板匹配",
      desc: coverage.template_name
        ? `已完成 · 命中模板「${coverage.template_name}${coverage.template_version ? ` ${coverage.template_version}` : ""}」`
        : "已完成 · 使用默认模板",
      state: "done",
    },
    {
      title: "覆盖率分析",
      desc: `已完成 · ${completed}/${totalSlots} 插槽已填充${missing > 0 ? `，${missing} 项必填缺失` : ""}`,
      state: "done",
      detail: (
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
