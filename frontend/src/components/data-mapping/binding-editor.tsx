"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createPropertyBinding,
  updatePropertyBinding,
  VersionConflictError,
  type PropertyBinding,
  type PropertyBindingInput,
} from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { propertyBindingsKey } from "@/components/data-mapping/binding-table";
import { MAPPING_HEALTH_KEY } from "@/components/data-mapping/coverage";

const PROPERTY_KINDS = ["data", "object"];
const TRANSFORM_TYPES = ["none", "controlled_vocab", "pattern", "cast"];
const OBJECT_RESOLUTIONS = ["id_reference", "nested_object"];

const CONFLICT_MESSAGE = "版本冲突，请刷新后重试";

interface FormState {
  property_iri: string;
  property_kind: string;
  source_path: string;
  transform_type: string;
  transform_config_text: string;
  is_identifier: boolean;
  is_label: boolean;
  object_resolution: string;
  target_class_iri: string;
  target_id_path: string;
}

function initialForm(binding?: PropertyBinding): FormState {
  return {
    property_iri: binding?.property_iri ?? "",
    property_kind: binding?.property_kind ?? "data",
    source_path: binding?.source_path ?? "",
    transform_type: binding?.transform_type ?? "none",
    transform_config_text: binding?.transform_config
      ? JSON.stringify(binding.transform_config)
      : "",
    is_identifier: binding?.is_identifier ?? false,
    is_label: binding?.is_label ?? false,
    object_resolution: binding?.object_resolution ?? "id_reference",
    target_class_iri: binding?.target_class_iri ?? "",
    target_id_path: binding?.target_id_path ?? "",
  };
}

/**
 * 属性绑定编辑器（US3 · FR-016）：senior_analyst 新建 / 编辑一条属性绑定的 Dialog 表单。
 * 保存走既有 014 端点，更新时携 expected_version 乐观并发；409 → VersionConflictError
 * → 展示冲突提示。成功后失效属性绑定与映射健康度查询。
 */
export function BindingEditor({
  mappingId,
  binding,
  open,
  onClose,
}: {
  mappingId: string;
  binding?: PropertyBinding;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(() => initialForm(binding));
  const [error, setError] = useState<string | null>(null);

  const isEdit = Boolean(binding);
  const isObject = form.property_kind === "object";

  const mutation = useMutation({
    mutationFn: (payload: PropertyBindingInput) =>
      binding
        ? updatePropertyBinding(binding.id, payload)
        : createPropertyBinding(mappingId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: propertyBindingsKey(mappingId) });
      queryClient.invalidateQueries({ queryKey: MAPPING_HEALTH_KEY });
      onClose();
    },
    onError: (err) => {
      setError(err instanceof VersionConflictError ? CONFLICT_MESSAGE : String(err));
    },
  });

  const submit = () => {
    setError(null);

    const propertyIri = form.property_iri.trim();
    if (!propertyIri) {
      setError("属性 IRI 不能为空");
      return;
    }
    if (!form.source_path.trim()) {
      setError("源字段 source_path 不能为空");
      return;
    }

    let transformConfig: Record<string, unknown> | null = null;
    if (form.transform_type !== "none" && form.transform_config_text.trim()) {
      try {
        transformConfig = JSON.parse(form.transform_config_text) as Record<string, unknown>;
      } catch {
        setError("transform_config 不是合法 JSON");
        return;
      }
    }

    const payload: PropertyBindingInput = {
      property_iri: propertyIri,
      property_kind: form.property_kind,
      source_path: form.source_path.trim(),
      transform_type: form.transform_type,
      transform_config: transformConfig,
      is_identifier: form.is_identifier,
      is_label: form.is_label,
    };

    if (isObject) {
      payload.object_resolution = form.object_resolution;
      payload.target_class_iri = form.target_class_iri.trim() || null;
      payload.target_id_path = form.target_id_path.trim() || null;
    }

    if (binding) {
      payload.expected_version = binding.version;
    }

    mutation.mutate(payload);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑属性绑定" : "新建属性绑定"}</DialogTitle>
          <DialogDescription>
            声明「本体属性 → 源字段」的绑定：源字段路径、逐值转换、标识符 / 标签标记。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {error && (
            <Alert variant="destructive">
              <AlertTitle>保存失败</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="pb-property-iri">属性 IRI</Label>
            <Input
              id="pb-property-iri"
              value={form.property_iri}
              onChange={(e) => setForm({ ...form, property_iri: e.target.value })}
              placeholder="https://…/approvalNumber"
              className="font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>属性种类</Label>
              <Select
                value={form.property_kind}
                onValueChange={(v) => setForm({ ...form, property_kind: v })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROPERTY_KINDS.map((k) => (
                    <SelectItem key={k} value={k}>
                      {k}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pb-source-path">源字段 source_path</Label>
              <Input
                id="pb-source-path"
                value={form.source_path}
                onChange={(e) => setForm({ ...form, source_path: e.target.value })}
                placeholder="approval_no"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>转换 transform_type</Label>
            <Select
              value={form.transform_type}
              onValueChange={(v) => setForm({ ...form, transform_type: v })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRANSFORM_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {form.transform_type !== "none" && (
            <div className="space-y-1.5">
              <Label htmlFor="pb-transform-config">transform_config（JSON）</Label>
              <Input
                id="pb-transform-config"
                value={form.transform_config_text}
                onChange={(e) => setForm({ ...form, transform_config_text: e.target.value })}
                placeholder='{"map": {"高": "HighRisk"}}'
                className="font-mono text-xs"
              />
            </div>
          )}

          {isObject && (
            <div className="grid grid-cols-1 gap-3 rounded-md border border-border bg-muted/40 p-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>对象解析</Label>
                <Select
                  value={form.object_resolution}
                  onValueChange={(v) => setForm({ ...form, object_resolution: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OBJECT_RESOLUTIONS.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pb-target-class">目标类 IRI</Label>
                <Input
                  id="pb-target-class"
                  value={form.target_class_iri}
                  onChange={(e) => setForm({ ...form, target_class_iri: e.target.value })}
                  placeholder="…/Manufacturer"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pb-target-id">目标 id 路径</Label>
                <Input
                  id="pb-target-id"
                  value={form.target_id_path}
                  onChange={(e) => setForm({ ...form, target_id_path: e.target.value })}
                  placeholder="mfr_code"
                />
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-6">
            <div className="flex items-center gap-2">
              <Switch
                id="pb-is-identifier"
                checked={form.is_identifier}
                onCheckedChange={(v) => setForm({ ...form, is_identifier: v })}
              />
              <Label htmlFor="pb-is-identifier">标识符 is_identifier</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                id="pb-is-label"
                checked={form.is_label}
                onCheckedChange={(v) => setForm({ ...form, is_label: v })}
              />
              <Label htmlFor="pb-is-label">标签 is_label</Label>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            取消
          </Button>
          <Button onClick={submit} disabled={mutation.isPending}>
            {mutation.isPending ? "保存中…" : isEdit ? "保存" : "创建绑定"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
