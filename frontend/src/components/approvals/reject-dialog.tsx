"use client";

import { useState } from "react";
import { rejectConclusion } from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

interface Props {
  conclusionId: string;
  onRejected?: () => void;
  onClose?: () => void;
}

/** QA 拒绝对话框（21 CFR Part 11 重认证 + 拒绝原因, T011）。 */
export function RejectDialog({ conclusionId, onRejected, onClose }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await rejectConclusion({
        conclusion_id: conclusionId,
        username,
        password,
        reason,
      });
      onRejected?.();
      onClose?.();
      if (r.voided_actions > 0) {
        // 信息性提示:被抑制的非终态动作随拒绝一并作废。
        console.info(`已作废 ${r.voided_actions} 个非终态动作`);
      }
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
          <DialogTitle>QA 拒绝结论（21 CFR Part 11）</DialogTitle>
          <DialogDescription>
            拒绝前须重新认证。拒绝将使结论进入终态，并作废其被抑制的非终态动作。
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="qa-reject-username">用户名</Label>
            <Input
              id="qa-reject-username"
              placeholder="用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="qa-reject-password">密码（重认证）</Label>
            <Input
              id="qa-reject-password"
              type="password"
              placeholder="密码（重认证）"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="qa-reject-reason">拒绝原因</Label>
            <Textarea
              id="qa-reject-reason"
              placeholder="拒绝原因"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" size="sm" className="h-auto px-3 py-1" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-auto px-3 py-1"
            disabled={busy || !username || !password || !reason}
            onClick={submit}
          >
            确认拒绝
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
