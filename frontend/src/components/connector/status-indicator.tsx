import { cn } from "@/lib/utils";
import { CONNECTOR_STATUS_META, type ConnectorStatus } from "@/lib/api";

/**
 * 连接状态语义指示器（US2 · FR-012）——小圆点 + 中文标签，颜色仅用语义 token。
 * tone → 前景色 token；圆点与标签共用 currentColor，故只需一处色值。
 */
const TONE_TEXT: Record<
  "success" | "warning" | "destructive" | "muted",
  string
> = {
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
  muted: "text-muted-foreground",
};

export function StatusIndicator({
  status,
  className,
}: {
  status: ConnectorStatus;
  className?: string;
}) {
  const meta = CONNECTOR_STATUS_META[status];
  return (
    <span
      className={cn("inline-flex items-center gap-1.5", TONE_TEXT[meta.tone], className)}
      title={meta.label}
      aria-label={`连接状态：${meta.label}`}
    >
      <span className="size-2 shrink-0 rounded-full bg-current" aria-hidden="true" />
      <span className="text-xs font-medium">{meta.label}</span>
    </span>
  );
}
