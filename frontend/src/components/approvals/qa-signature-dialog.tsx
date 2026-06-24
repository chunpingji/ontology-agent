"use client";

import { useState } from "react";
import { signConclusion } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
  conclusionId: string;
  onSigned?: () => void;
  onClose?: () => void;
}

/** 21 CFR Part 11 QA 电子签名对话框：重认证 + 签名含义（能力六, T057）。 */
export function QaSignatureDialog({ conclusionId, onSigned, onClose }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [meaning, setMeaning] = useState("已复核批准生效");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await signConclusion({ conclusion_id: conclusionId, username, password, meaning });
      onSigned?.();
      onClose?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose?.(); }}>
      <DialogContent className="w-96 max-w-[calc(100vw-2rem)]">
        <DialogHeader>
          <DialogTitle>QA 电子签名（21 CFR Part 11）</DialogTitle>
          <DialogDescription>
            签名前须重新认证。签名将不可分割地绑定此结论并使其生效。
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="qa-sign-username">用户名</Label>
            <Input
              id="qa-sign-username"
              placeholder="用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="qa-sign-password">密码（重认证）</Label>
            <Input
              id="qa-sign-password"
              type="password"
              placeholder="密码（重认证）"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="qa-sign-meaning">签名含义</Label>
            <Input
              id="qa-sign-meaning"
              placeholder="签名含义"
              value={meaning}
              onChange={(e) => setMeaning(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" size="sm" className="h-auto px-3 py-1" onClick={onClose}>
            取消
          </Button>
          <Button
            size="sm"
            className="h-auto px-3 py-1"
            disabled={busy || !username || !password}
            onClick={submit}
          >
            签名并生效
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
