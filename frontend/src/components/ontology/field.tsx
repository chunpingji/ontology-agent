import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";

/**
 * 表单字段包装：在控件上方常驻一个字段标签，避免编辑已有值时 placeholder 消失
 * 导致用户无法分辨各输入框含义。点击标签即聚焦其中的控件。
 */
export function Field({
  label,
  hint,
  className = "",
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Label className={cn("block text-[11px] font-medium text-muted-foreground", className)}>
      <span>
        {label}
        {hint && <span className="ml-1 font-normal text-muted-foreground">{hint}</span>}
      </span>
      <div className="mt-0.5 font-normal">{children}</div>
    </Label>
  );
}
