"use client";

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-[28rem] rounded-lg bg-white p-5 shadow-xl">
        <h3 className="mb-2 text-base font-bold text-amber-700">他人已更新该实体</h3>
        <p className="mb-1 text-sm text-gray-600">{conflict.message}</p>
        {conflict.currentVersion != null && (
          <p className="mb-4 text-xs text-gray-400">
            服务端当前版本：v{conflict.currentVersion}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            onClick={onDismiss}
            className="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
          >
            放弃
          </button>
          {onViewDiff && (
            <button
              onClick={onViewDiff}
              className="rounded-md border px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              查看差异
            </button>
          )}
          <button
            onClick={onReload}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          >
            重新加载最新
          </button>
        </div>
      </div>
    </div>
  );
}
