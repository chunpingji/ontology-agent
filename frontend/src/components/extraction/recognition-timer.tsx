"use client";

import { useEffect, useState } from "react";

/** Keep the one-second clock outside the editor and evidence tree. */
export function RecognitionTimer({ startedAt }: { startedAt?: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (!startedAt) return null;
  const seconds = Math.max(0, Math.floor(now - startedAt));
  return <span className="text-xs text-muted-foreground">
    本次运行 {Math.floor(seconds / 60)} 分 {seconds % 60} 秒
  </span>;
}
