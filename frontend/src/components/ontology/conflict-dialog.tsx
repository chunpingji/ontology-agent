"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { ConflictState } from "./use-version-conflict";

/**
 * 版本冲突对话框：当某写操作返回 409 时弹出，提供
 * 「重新加载最新 / 查看差异 / 放弃」三种处置（FR-011a）。
 */
export function ConflictDialog({
  conflict,
  onReload,
  onViewDiff,
  onDismiss,
}: {
  conflict: ConflictState | null;
  onReload: () => void;
  onViewDiff?: () => void;
  onDismiss: () => void;
}) {
  if (!conflict) return null;
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onDismiss();
      }}
    >
      <DialogContent className="w-[28rem] max-w-[28rem]">
        <DialogHeader>
          <DialogTitle className="text-base font-bold text-warning">他人已更新该实体</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">{conflict.message}</DialogDescription>
        </DialogHeader>
        {conflict.currentVersion != null && (
          <p className="text-xs text-muted-foreground">
            服务端当前版本：v{conflict.currentVersion}
          </p>
        )}
        <DialogFooter className="flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={onDismiss}
            className="h-auto px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent"
          >
            放弃
          </Button>
          {onViewDiff && (
            <Button
              variant="outline"
              onClick={onViewDiff}
              className="h-auto px-3 py-1.5 text-sm"
            >
              查看差异
            </Button>
          )}
          <Button
            onClick={onReload}
            className="h-auto px-3 py-1.5 text-sm"
          >
            重新加载最新
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
