"use client";

import { useState, useCallback } from "react";
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

interface SlotDef {
  slot_id: string;
  label: string;
  source: Record<string, unknown>;
  required: boolean;
  on_missing: string;
  missing_placeholder: string;
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
}

const SOURCE_KINDS = [
  { value: "extraction", label: "抽取" },
  { value: "rule", label: "规则" },
  { value: "manual", label: "手工" },
  { value: "constant", label: "常量" },
];

export function TemplateSlotEditor({
  schema,
  onSave,
  onCancel,
  saving = false,
}: TemplateSlotEditorProps) {
  const [sections, setSections] = useState<SectionDef[]>(
    () => JSON.parse(JSON.stringify(schema.sections)) as SectionDef[],
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
      const next = JSON.parse(JSON.stringify(prev)) as SectionDef[];
      next[sectionIdx].groups[groupIdx].slots.splice(slotIdx, 1);
      return next;
    });
  }

  function addSlot(sectionIdx: number, groupIdx: number) {
    setSections((prev) => {
      const next = JSON.parse(JSON.stringify(prev)) as SectionDef[];
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
    const group = sections[sectionIdx].groups[groupIdx];
    setExpandedGroups((prev) => new Set([...prev, group.group_id]));
  }

  function moveSlot(
    sectionIdx: number,
    groupIdx: number,
    slotIdx: number,
    direction: -1 | 1,
  ) {
    setSections((prev) => {
      const next = JSON.parse(JSON.stringify(prev)) as SectionDef[];
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
      const next = JSON.parse(JSON.stringify(prev)) as SectionDef[];
      next[editingSlot.sectionIdx].groups[editingSlot.groupIdx].slots[
        editingSlot.slotIdx
      ] = editingSlot.slot;
      return next;
    });
    setEditingSlot(null);
  }

  function handleSave() {
    onSave({ ...schema, sections });
  }

  const totalSlots = sections.reduce(
    (acc, s) => acc + s.groups.reduce((a, g) => a + g.slots.length, 0),
    0,
  );
  const requiredSlots = sections.reduce(
    (acc, s) =>
      acc +
      s.groups.reduce(
        (a, g) => a + g.slots.filter((sl) => sl.required).length,
        0,
      ),
    0,
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {totalSlots} 个插槽（{requiredSlots} 必填）
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            取消
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>

      {sections.map((section, sIdx) => (
        <div key={section.section_id} className="border rounded-lg">
          <button
            className="w-full flex items-center gap-2 p-3 hover:bg-muted/50 text-left"
            onClick={() => toggleSection(section.section_id)}
          >
            <span className="text-xs">
              {expandedSections.has(section.section_id) ? "▼" : "▶"}
            </span>
            <span className="font-medium">{section.title}</span>
            <Badge variant="outline" className="ml-auto">
              {section.groups.reduce((a, g) => a + g.slots.length, 0)} 插槽
            </Badge>
          </button>

          {expandedSections.has(section.section_id) && (
            <div className="px-3 pb-3 space-y-2">
              {section.groups.map((group, gIdx) => (
                <div key={group.group_id} className="border rounded ml-4">
                  <button
                    className="w-full flex items-center gap-2 p-2 hover:bg-muted/50 text-left text-sm"
                    onClick={() => toggleGroup(group.group_id)}
                  >
                    <span className="text-xs">
                      {expandedGroups.has(group.group_id) ? "▼" : "▶"}
                    </span>
                    <span>{group.title}</span>
                    <Badge variant="outline" className="text-xs ml-1">
                      {group.kind}
                    </Badge>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {group.slots.length} 插槽
                    </span>
                  </button>

                  {expandedGroups.has(group.group_id) && (
                    <div className="px-2 pb-2 space-y-1">
                      {group.slots.map((slot, slIdx) => (
                        <div
                          key={slot.slot_id}
                          className="flex items-center gap-2 p-1.5 rounded hover:bg-muted/30 text-sm ml-4"
                        >
                          <span className="font-mono text-xs text-muted-foreground truncate max-w-[180px]">
                            {slot.slot_id}
                          </span>
                          <span className="truncate flex-1">{slot.label}</span>
                          {slot.required && (
                            <Badge variant="default" className="text-xs">
                              必填
                            </Badge>
                          )}
                          <Badge variant="outline" className="text-xs">
                            {(slot.source as Record<string, unknown>).kind as string}
                          </Badge>
                          <div className="flex gap-0.5">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => moveSlot(sIdx, gIdx, slIdx, -1)}
                              disabled={slIdx === 0}
                            >
                              ↑
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => moveSlot(sIdx, gIdx, slIdx, 1)}
                              disabled={slIdx === group.slots.length - 1}
                            >
                              ↓
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => openSlotEditor(sIdx, gIdx, slIdx)}
                            >
                              ✎
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0 text-red-500"
                              onClick={() => removeSlot(sIdx, gIdx, slIdx)}
                            >
                              ×
                            </Button>
                          </div>
                        </div>
                      ))}
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-4 text-xs"
                        onClick={() => addSlot(sIdx, gIdx)}
                      >
                        + 添加插槽
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

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
