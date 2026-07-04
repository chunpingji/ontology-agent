"use client";

import { useState } from "react";
import { Ban, PenLine } from "lucide-react";

import { QaSignatureDialog } from "@/components/approvals/qa-signature-dialog";
import { RejectDialog } from "@/components/approvals/reject-dialog";
import { Button } from "@/components/ui/button";

interface DecisionActionsProps {
  conclusionId: string;
  /** 仅 QA 角色可执行签批/拒绝（role === "qa"）。 */
  canDecide: boolean;
  /** 做出决定后回调（页面据此刷新队列与时间线）。 */
  onDecided: () => void;
}

/** 决策区：签批（QaSignatureDialog）/ 拒绝（RejectDialog），非 QA 时禁用（FR-019 / FR-006）。 */
function DecisionActions({ conclusionId, canDecide, onDecided }: DecisionActionsProps) {
  const [signing, setSigning] = useState(false);
  const [rejecting, setRejecting] = useState(false);

  const handleDecided = () => {
    onDecided();
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={!canDecide}
          onClick={() => setSigning(true)}
        >
          <PenLine />
          签批
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!canDecide}
          onClick={() => setRejecting(true)}
          className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
        >
          <Ban />
          拒绝
        </Button>
      </div>

      {!canDecide && (
        <p className="text-xs text-muted-foreground">
          仅 QA 角色可执行签批 / 拒绝操作，请切换至 QA 身份后重试。
        </p>
      )}

      {signing && (
        <QaSignatureDialog
          conclusionId={conclusionId}
          onSigned={handleDecided}
          onClose={() => setSigning(false)}
        />
      )}
      {rejecting && (
        <RejectDialog
          conclusionId={conclusionId}
          onRejected={handleDecided}
          onClose={() => setRejecting(false)}
        />
      )}
    </div>
  );
}

export { DecisionActions };
