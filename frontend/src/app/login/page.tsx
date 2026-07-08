"use client";

// 登录页（还原 design.pen「登录」帧）。位于 (dashboard) 路由组之外，故不套 ShellFrame，
// 无侧栏/顶栏。提交调用 api.login() 换取 Bearer 令牌并写入 localStorage，再跳转 next。
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Hexagon, Loader2, AlertCircle } from "lucide-react";

import { login } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";

/** 仅允许站内相对路径，杜绝开放重定向（//host、http://…）。 */
function safeNext(): string {
  if (typeof window === "undefined") return "/overview";
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/overview";
}

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    setLoading(true);
    try {
      await login(username.trim(), password);
      router.replace(safeNext());
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试");
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f5f5f5] p-6">
      <div className="flex w-full max-w-[400px] flex-col gap-6">
        {/* 品牌 */}
        <div className="flex items-center justify-center gap-2.5">
          <div className="flex h-10 w-10 items-center justify-center rounded-[10px] bg-primary">
            <Hexagon className="h-[22px] w-[22px] text-primary-foreground" />
          </div>
          <span className="text-[22px] font-bold text-foreground">恒瑞智能文档分析系统</span>
        </div>

        {/* 登录卡片 */}
        <Card>
          <CardHeader className="gap-1.5">
            <CardTitle className="text-2xl">欢迎回来</CardTitle>
            <CardDescription>登录以继续使用恒瑞智能文档分析系统</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="username">用户名</Label>
                <Input
                  id="username"
                  autoComplete="username"
                  autoFocus
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入用户名"
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                />
              </div>

              <div className="flex items-center justify-between">
                <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
                  <Checkbox
                    checked={remember}
                    onCheckedChange={(v) => setRemember(v === true)}
                  />
                  记住我
                </label>
                <span className="text-sm font-medium text-primary">忘记密码？</span>
              </div>

              {error && (
                <div className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <Button type="submit" size="lg" className="w-full" disabled={loading}>
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                {loading ? "登录中…" : "登录"}
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* 页脚 */}
        <div className="flex items-center justify-center gap-1 text-sm text-muted-foreground">
          <span>还没有账户？</span>
          <span className="font-medium text-primary">联系管理员</span>
        </div>
      </div>
    </div>
  );
}
