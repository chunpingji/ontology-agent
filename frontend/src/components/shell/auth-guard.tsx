"use client";

// 前端路由访问控制：受保护区域挂载时校验本地是否持有登录令牌。无令牌 → 跳登录页
// （带 next 以便登录后回跳）。令牌存于 localStorage（仅客户端），故守卫在客户端运行；
// 确认前不渲染受保护内容，避免未登录闪现。后端 auth_required=True 时是最终防线，此守卫
// 仅负责体验层的拦截与回跳。
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getToken } from "@/lib/api";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (getToken()) {
      setAuthed(true);
      return;
    }
    setAuthed(false);
    const next = encodeURIComponent(pathname ?? "/");
    router.replace(`/login?next=${next}`);
  }, [pathname, router]);

  if (!authed) return null;
  return <>{children}</>;
}
