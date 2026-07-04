"use client";

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertCircle, Loader2, UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useIdentity } from "@/lib/use-identity";
import {
  DEFAULT_DOC_TYPE,
  DOCUMENT_TYPE_GROUPS,
  phaseLabel,
  prepareUpload,
  submitUpload,
  type PreparedUpload,
} from "@/lib/api";

/**
 * 文档上传（研发文档库 · 报告中心）。
 *
 * 左侧分类是**研发阶段**（上传目标 = 选中的阶段，其 IRI 随信封落 hasDevelopmentPhase）。
 * 上传分两步、两个正交轴：
 *   1) 研发阶段（文件夹轴）——跟随左侧选中项，父组件以 `phaseIri` 传入；未选（全部/
 *      报告类型/未分阶段）时为 null → 提示先在左侧选定研发阶段，禁用选择文件。
 *   2) 文档**类型**（`RegulatoryDocument` 子类）——选定文件后由用户在此就地指定。
 *
 * 复用 doc_repo `upload` 接入模式：确认后把文件构造为标准上传信封（`prepareUpload`，
 * 携文档类型 classIri + 阶段 phaseIri）→ 建连接器并立即同步（`submitUpload`，下发
 * doc_type_to_class 覆盖）。上传瞬间经 `onStart` 交出「处理中」占位（据预测 IRI 乐观
 * 入列），同步完成经 `onEnd` 翻牌「就绪」，失败则移除占位。仅 senior_analyst 可见。
 */
export function Upload({
  phaseIri,
  onStart,
  onEnd,
}: {
  /** 上传目标研发阶段 IRI（跟随左侧选中分类）；null=当前选中非研发阶段，不可上传。 */
  phaseIri: string | null;
  /** 上传开始：交出已构造的占位（父组件据此乐观入列为「处理中」）。 */
  onStart: (prepared: PreparedUpload[]) => void;
  /** 上传结束：ok=true 翻牌「就绪」，ok=false 移除占位。 */
  onEnd: (iris: string[], ok: boolean) => void;
}) {
  const { role } = useIdentity();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  // 已选定、待指定文档类型的文件（第 2 步就地选择类型后再提交）。
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [docType, setDocType] = useState<string>(DEFAULT_DOC_TYPE);

  const upload = useMutation({
    mutationFn: (prepared: PreparedUpload[]) => submitUpload(prepared),
    onSuccess: (_result, prepared) => {
      if (inputRef.current) inputRef.current.value = "";
      onEnd(
        prepared.map((p) => p.iri),
        true,
      );
    },
    onError: (_error, prepared) => {
      onEnd(
        prepared.map((p) => p.iri),
        false,
      );
    },
  });

  // 角色门禁：仅 senior_analyst 可上传（FR-022/FR-023）。
  if (role !== "senior_analyst") return null;

  const canPick = phaseIri !== null && !upload.isPending;
  const phaseTarget = phaseIri ? phaseLabel(phaseIri) : null;

  const stageFiles = (fileList: FileList | null) => {
    if (!phaseIri) return; // 未选定研发阶段，忽略。
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    setPendingFiles(files); // 暂存 → 展开文档类型选择器，确认后再提交。
  };

  const confirmUpload = () => {
    if (!phaseIri || pendingFiles.length === 0) return;
    const prepared = pendingFiles.map((f) => prepareUpload(f, { docType, phaseIri }));
    setPendingFiles([]);
    if (inputRef.current) inputRef.current.value = "";
    onStart(prepared);
    upload.mutate(prepared);
  };

  const cancelPending = () => {
    setPendingFiles([]);
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="space-y-2">
      <div
        onDragOver={(event) => {
          if (!canPick) return;
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(event) => {
          if (!canPick) return;
          event.preventDefault();
          setIsDragging(false);
          stageFiles(event.dataTransfer.files);
        }}
        className={cn(
          "flex flex-wrap items-center gap-3 rounded-lg border border-dashed px-5 py-4 transition-colors sm:flex-nowrap sm:gap-4",
          phaseIri === null
            ? "border-border bg-muted/30"
            : isDragging
              ? "border-primary bg-accent"
              : "border-border bg-card/40 hover:border-primary/50",
        )}
      >
        <span className="shrink-0 text-muted-foreground [&_svg]:size-6">
          {upload.isPending ? <Loader2 className="animate-spin" /> : <UploadCloud />}
        </span>
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          {phaseIri === null ? (
            "请先在左侧选择研发阶段（如临床Ⅰ期、NDA/BLA 申报），文件将归入该阶段；上传后再指定文档类型"
          ) : (
            <>
              上传至研发阶段{" "}
              <span className="font-medium text-foreground">「{phaseTarget}」</span>
              ——拖拽文件到此处或点击右侧按钮，选定文件后指定文档类型
            </>
          )}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={!canPick}
          onClick={() => inputRef.current?.click()}
        >
          选择文件
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => stageFiles(event.target.files)}
        />
      </div>

      {/* 第 2 步：为已选文件指定文档类型（RegulatoryDocument 子类），确认后提交。 */}
      {pendingFiles.length > 0 && (
        <div className="space-y-3 rounded-lg border border-border bg-card/60 px-5 py-4">
          <div className="space-y-1">
            <p className="text-sm font-medium text-foreground">指定文档类型</p>
            <p className="text-xs text-muted-foreground">
              {pendingFiles.length} 个文件将归入研发阶段
              <span className="text-foreground">「{phaseTarget}」</span>
              ，请选择其文档类型。
            </p>
          </div>
          <ul className="max-h-24 space-y-0.5 overflow-y-auto text-xs text-muted-foreground">
            {pendingFiles.map((f, index) => (
              <li key={`${f.name}-${index}`} className="truncate" title={f.name}>
                · {f.name}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              disabled={upload.isPending}
              className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {DOCUMENT_TYPE_GROUPS.map((group) => (
                <optgroup key={group.group} label={group.group}>
                  {group.options.map((option) => (
                    <option key={option.localName} value={option.localName}>
                      {option.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <Button type="button" size="sm" disabled={upload.isPending} onClick={confirmUpload}>
              确认上传
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={upload.isPending}
              onClick={cancelPending}
            >
              取消
            </Button>
          </div>
        </div>
      )}

      {upload.isError && (
        <p className="flex items-center gap-1.5 text-xs text-destructive">
          <AlertCircle className="size-3.5" />
          上传失败，请稍后重试。
        </p>
      )}
    </div>
  );
}
