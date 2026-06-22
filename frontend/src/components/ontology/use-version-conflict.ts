"use client";

import { useCallback, useState } from "react";
import { VersionConflictError } from "@/lib/api";

/**
 * 共享乐观并发冲突处理（FR-011a 用户侧）。
 *
 * 任一写端点返回 409 时，`run()` 捕获 {@link VersionConflictError}，
 * 记录冲突态供「重新加载最新 / 查看差异 / 放弃」对话框使用，而非吞掉异常。
 * 其余异常继续上抛由调用方处理。
 */
export interface ConflictState {
  message: string;
  currentVersion: number | null;
}

export function useVersionConflict() {
  const [conflict, setConflict] = useState<ConflictState | null>(null);

  const run = useCallback(
    async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
      try {
        const result = await fn();
        setConflict(null);
        return result;
      } catch (err) {
        if (err instanceof VersionConflictError) {
          setConflict({ message: err.message, currentVersion: err.currentVersion });
          return undefined;
        }
        throw err;
      }
    },
    [],
  );

  const clear = useCallback(() => setConflict(null), []);

  return { conflict, run, clear };
}
