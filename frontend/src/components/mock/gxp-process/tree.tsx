"use client";

import { ChevronDown, ChevronRight, FolderTree, LockKeyhole, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Operation, ProcessStep } from "./data";

export function ProcessTree({ steps, operations, selectedId, search, expanded, disabled, onSearch, onExpand, onSelect, onAddStep, onAddOperation, onCollapse, batchMode = false }: {
  steps: ProcessStep[]; operations: Operation[]; selectedId: string; search: string; expanded: string[]; disabled: boolean;
  onSearch: (value: string) => void; onExpand: (id: string) => void; onSelect: (id: string) => void;
  onAddStep: () => void; onAddOperation: () => void; onCollapse: () => void;
  batchMode?: boolean;
}) {
  const query = search.trim().toLocaleLowerCase();
  const matches = (value: string) => value.toLocaleLowerCase().includes(query);
  const visible = steps.filter((step) => matches(`${step.id} ${step.name}`) || operations.some((operation) => operation.stepId === step.id && matches(`${operation.id} ${operation.name}`)));
  return <aside aria-label="工艺步骤导航" className="min-w-0 border-b bg-background p-3 lg:sticky lg:top-0 lg:max-h-[calc(100vh-56px)] lg:overflow-y-auto lg:border-b-0 lg:border-r">
    <div className="mb-3 flex items-center justify-between px-1"><h2 className="text-sm font-semibold">{batchMode ? "批工艺流程" : "工艺步骤"}</h2><span className="text-xs text-muted-foreground">{steps.length} 个步骤</span></div>
    <div className="relative"><Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" /><Input aria-label="搜索步骤或操作" placeholder="搜索步骤 / 操作名称" value={search} onChange={(event) => onSearch(event.target.value)} className="h-9 pl-8 text-xs" /></div>
    <div className="my-3 grid grid-cols-2 gap-2"><Button variant="outline" size="sm" onClick={onAddStep} disabled={disabled}><Plus />新增步骤</Button><Button variant="outline" size="sm" onClick={onAddOperation} disabled={disabled}><Plus />新增操作</Button></div>
    <div className="mb-2 flex items-center justify-between gap-1 px-1 text-[11px] text-muted-foreground"><span>{batchMode ? "按报告示例顺序 · 新增操作排在末尾" : "全部 · 系统预设与自定义"}</span>{!batchMode && <button type="button" className="shrink-0 rounded px-1 py-1 hover:text-primary focus-visible:ring-1 focus-visible:ring-ring" onClick={onCollapse}>{expanded.length ? "收起全部" : "展开全部"}</button>}</div>
    <nav aria-label={batchMode ? "批工艺操作" : "操作模板库"} className="max-h-80 space-y-0.5 overflow-y-auto lg:max-h-none lg:overflow-visible">
      {batchMode ? operations.map((operation, index) => {
        const step = steps.find((item) => item.id === operation.stepId)!;
        if (!matches(`${operation.id} ${operation.name} ${step.id} ${step.name}`)) return null;
        return <button type="button" key={operation.id} aria-current={operation.id === selectedId ? "page" : undefined} onClick={() => onSelect(operation.id)} className={cn("flex w-full items-center gap-2 rounded-md px-2 py-2.5 text-left text-xs focus-visible:ring-1 focus-visible:ring-ring", operation.id === selectedId ? "bg-primary text-primary-foreground" : "hover:bg-muted")}><span className="font-mono opacity-70">{String(index + 1).padStart(2, "0")}</span><span className="min-w-0 flex-1"><span className="font-medium">{operation.name || "未命名操作"}</span><span className="mt-1 block text-[10px] opacity-70">{operation.id} · {step.name}</span></span>{!operation.enabled && <span className="text-[10px]">已停用</span>}</button>;
      }) : visible.map((step) => {
        const open = Boolean(query) || expanded.includes(step.id);
        const children = operations.filter((operation) => operation.stepId === step.id && (!query || matches(`${step.id} ${step.name}`) || matches(`${operation.id} ${operation.name}`)));
        const active = children.some((operation) => operation.id === selectedId);
        return <div key={step.id}>
          <button type="button" aria-expanded={open} onClick={() => onExpand(step.id)} className={cn("flex w-full items-center gap-1.5 rounded px-1.5 py-2 text-left text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring", active && "bg-primary/5 font-medium text-primary")}>
            {open ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}<span className="w-5 shrink-0 font-mono text-[11px] text-muted-foreground">{step.id.slice(2)}</span><span className="flex-1">{step.name}</span>{step.custom && <span className="text-[10px] text-muted-foreground">自定义</span>}
          </button>
          {open && <div className="ml-5 space-y-0.5 border-l pl-2">{children.map((operation) => <button type="button" key={operation.id} aria-current={operation.id === selectedId ? "page" : undefined} onClick={() => onSelect(operation.id)} className={cn("flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring", operation.id === selectedId ? "bg-primary font-medium text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground")}><span className="size-1 rounded-full bg-current" /><span className="flex-1">{operation.name || "未命名操作"}</span>{!operation.enabled && <span className="text-[10px]">已停用</span>}</button>)}{!children.length && <p className="p-2 text-xs text-muted-foreground">暂无操作</p>}</div>}
        </div>;
      })}
      {!visible.length && <p role="status" className="px-2 py-8 text-center text-xs text-muted-foreground">未找到匹配的步骤或操作</p>}
    </nav>
    <div className="mt-4 space-y-1 rounded-md bg-muted/60 p-3"><p className="flex items-center gap-2 text-xs font-medium"><FolderTree className="size-3.5 text-primary" />{batchMode ? "批号专属操作" : "操作模板库"}</p><p className="text-[11px] text-muted-foreground">选择操作，配置记录要求与处置规则</p></div>
    <div className="mt-3 space-y-2 border-t p-3 text-[11px] text-muted-foreground"><p className="flex items-center gap-2 text-xs font-medium text-foreground"><LockKeyhole className="size-3.5" />配置权限</p><p>查看：已授权成员</p><p>编辑：工艺管理员、授权配置员</p><p>发布审核：质量负责人</p><p>无编辑权限时，以只读方式查看。</p></div>
  </aside>;
}
