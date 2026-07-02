"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { WordViewer } from "./word-viewer";
import {
  suggestSlots,
  getAnnotatedDocument,
  type SuggestSlotsRequest,
  type SuggestedSlot,
  type TiptapContent,
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
  // 创建与编辑统一为同一视图：create=空骨架 schema 起步，edit=载入已有 schema。
  mode?: "create" | "edit";
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
  mode = "edit",
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
      {/* ── Left: 忠于原文的文档预览 ─────────────────────────────── */}
      <div className="flex-1 min-w-0 overflow-y-auto border-r px-6 py-4">
        {aiSummary && (
          <div className="mb-4 rounded bg-muted/50 p-3 text-sm">
            <span className="font-medium">文档摘要：</span>
            {aiSummary}
          </div>
        )}
        {previewContent ? (
          <WordViewer
            key={jobId ?? "sample"}
            content={previewContent}
            highlightRef={activeRef}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            无文档预览（该模板未保存样例内容）
          </div>
        )}
      </div>

      {/* ── Right: Slot 树 + 内联 AI 审核 ────────────────────────── */}
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
                              {/* 真实 Slot 行 */}
                              {rg.group &&
                                rg.group.slots.map((slot, slIdx) => (
                                  <div
                                    key={slot.slot_id}
                                    className={`flex items-center gap-2 p-1.5 rounded text-sm ml-4 cursor-pointer hover:bg-muted/30${
                                      newSlotIds.has(slot.slot_id)
                                        ? " ring-1 ring-green-400 bg-green-50 dark:bg-green-900/20"
                                        : ""
                                    }${slot.disabled ? " opacity-50" : ""}${
                                      activeSlotId === slot.slot_id
                                        ? " bg-yellow-50 dark:bg-yellow-900/20"
                                        : ""
                                    }`}
                                    onClick={() => handleSlotClick(slot)}
                                  >
                                    <span className="font-mono text-xs text-muted-foreground truncate max-w-[120px]">
                                      {slot.slot_id}
                                    </span>
                                    <span
                                      className={`truncate flex-1${slot.disabled ? " line-through" : ""}`}
                                    >
                                      {slot.label}
                                    </span>
                                    {slot.required && !slot.disabled && (
                                      <Badge variant="default" className="text-xs">
                                        必填
                                      </Badge>
                                    )}
                                    {slot.disabled ? (
                                      <Badge variant="outline" className="text-xs">
                                        已禁用
                                      </Badge>
                                    ) : (
                                      <Badge variant="outline" className="text-xs">
                                        {(slot.source as Record<string, unknown>).kind as string}
                                      </Badge>
                                    )}
                                    <div className="flex gap-0.5" onClick={(e) => e.stopPropagation()}>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 w-6 p-0 text-xs"
                                        title={slot.disabled ? "启用此插槽" : "禁用此插槽（仅编辑器标记）"}
                                        onClick={() =>
                                          toggleDisable(rs.sIdx!, rg.gIdx!, slIdx)
                                        }
                                      >
                                        {slot.disabled ? "启" : "禁"}
                                      </Button>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 w-6 p-0"
                                        onClick={() => moveSlot(rs.sIdx!, rg.gIdx!, slIdx, -1)}
                                        disabled={slIdx === 0}
                                      >
                                        ↑
                                      </Button>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 w-6 p-0"
                                        onClick={() => moveSlot(rs.sIdx!, rg.gIdx!, slIdx, 1)}
                                        disabled={slIdx === rg.group!.slots.length - 1}
                                      >
                                        ↓
                                      </Button>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 w-6 p-0"
                                        onClick={() => openSlotEditor(rs.sIdx!, rg.gIdx!, slIdx)}
                                      >
                                        ✎
                                      </Button>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 w-6 p-0 text-red-500"
                                        onClick={() => removeSlot(rs.sIdx!, rg.gIdx!, slIdx)}
                                      >
                                        ×
                                      </Button>
                                    </div>
                                  </div>
                                ))}

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
                  </div>
                )}
              </div>
            );
          })}

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

      {/* ── Slot edit dialog ────────────────────────────────────── */}
      <Dialog
        open={editingSlot !== null}
        onOpenChange={(open) => { if (!open) setEditingSlot(null); }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑插槽</DialogTitle>
          </DialogHeader>
          {editingSlot && (
            <div className="space-y-4">
              <div className="space-y-1">
                <Label>插槽 ID</Label>
                <Input
                  value={editingSlot.slot.slot_id}
                  onChange={(e) =>
                    setEditingSlot({
                      ...editingSlot,
                      slot: { ...editingSlot.slot, slot_id: e.target.value },
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>标签</Label>
                <Input
                  value={editingSlot.slot.label}
                  onChange={(e) =>
                    setEditingSlot({
                      ...editingSlot,
                      slot: { ...editingSlot.slot, label: e.target.value },
                    })
                  }
                />
              </div>
              <div className="flex gap-3">
                <div className="space-y-1 flex-1">
                  <Label>来源类型</Label>
                  <Select
                    value={(editingSlot.slot.source.kind as string) || "extraction"}
                    onValueChange={(v) =>
                      setEditingSlot({
                        ...editingSlot,
                        slot: {
                          ...editingSlot.slot,
                          source: { ...editingSlot.slot.source, kind: v },
                        },
                      })
                    }
                  >
                    <SelectTrigger>
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
                </div>
                <div className="space-y-1 flex-1">
                  <Label>必填</Label>
                  <Select
                    value={editingSlot.slot.required ? "true" : "false"}
                    onValueChange={(v) =>
                      setEditingSlot({
                        ...editingSlot,
                        slot: { ...editingSlot.slot, required: v === "true" },
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="true">是</SelectItem>
                      <SelectItem value="false">否</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-1">
                <Label>缺失处理</Label>
                <Select
                  value={editingSlot.slot.on_missing}
                  onValueChange={(v) =>
                    setEditingSlot({
                      ...editingSlot,
                      slot: { ...editingSlot.slot, on_missing: v },
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="annotate">标注</SelectItem>
                    <SelectItem value="leave_blank">留空</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingSlot(null)}>
              取消
            </Button>
            <Button onClick={saveSlotEdit}>确定</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
