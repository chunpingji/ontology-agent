"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Loader2, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { TemplateReportPreview } from "@/components/reporting/template-report-preview";
import { getAstTemplate } from "@/lib/api";
import { isTemplateV2 } from "@/lib/reporting-v2";
import { useIdentity } from "@/lib/use-identity";
import { reportDocumentError } from "./report-word-workspace";

export function FinderReportActions({ templateId, sourceJobId, documentTitle }: {
  templateId: string; sourceJobId: string; documentTitle: string;
}) {
  const { identity: { username, role } } = useIdentity();
  const [menuOpen, setMenuOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const pendingDrawer = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const template = useQuery({
    queryKey: ["report-action-template", username, role, templateId],
    queryFn: async ({ signal }) => {
      const result = await getAstTemplate(templateId, signal);
      if (!isTemplateV2(result.schema_json)) throw new Error("当前模板不支持报告生成，请检查模板版本。");
      return { ...result, schema_json: result.schema_json };
    },
    enabled: drawerOpen,
    retry: false,
  });

  return <>
    <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
      <DropdownMenuTrigger asChild>
        <Button ref={trigger}><Sparkles className="mr-1.5 size-4" />操作<ChevronDown className="ml-1.5 size-4" /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64" onCloseAutoFocus={(event) => {
        // Wait for the menu focus scope to close before opening the modal drawer.
        if (!pendingDrawer.current) return;
        event.preventDefault();
        pendingDrawer.current = false;
        setDrawerOpen(true);
      }}>
        <DropdownMenuItem disabled={!sourceJobId} onSelect={() => {
          pendingDrawer.current = true;
          setMenuOpen(false);
        }}>
          <ShieldCheck className="mr-2 size-4 text-amber-600" />生成风险评估报告
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
      <SheetContent size="xl" className="max-w-full gap-0 p-0 sm:max-w-[85vw]" onCloseAutoFocus={(event) => {
        event.preventDefault();
        trigger.current?.focus();
      }}>
        <SheetHeader className="shrink-0 border-b px-6 py-4 pr-12">
          <SheetTitle>生成风险评估报告</SheetTitle>
          <SheetDescription className="break-words">{documentTitle}</SheetDescription>
          {template.data && !template.error && <p className="text-sm text-muted-foreground">
            模板：{template.data.name} · {template.data.version}
          </p>}
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {template.error ? <div className="space-y-3 p-6">
            <p role="alert" className="text-sm text-destructive">{reportDocumentError(template.error)}</p>
            <Button variant="outline" disabled={template.isFetching} onClick={() => void template.refetch()}>重新加载模板</Button>
          </div> : template.data ? <TemplateReportPreview
            key={template.data.schema_hash ?? template.data.updated_at ?? templateId}
            templateId={templateId} templateName={template.data.name}
            defaultSourceJobId={sourceJobId} draftSchema={template.data.schema_json}
            recognitionMode="finder_legacy"
          /> : <p role="status" className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />正在加载报告模板…
          </p>}
        </div>
      </SheetContent>
    </Sheet>
  </>;
}
