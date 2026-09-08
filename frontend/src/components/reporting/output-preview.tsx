"use client";

import { useState, type ReactNode } from "react";
import type { OutputNode } from "@/lib/reporting-v2";

export function OutputPreview({ ast, compact = false, onLocateSource }: {
  ast: OutputNode | null; compact?: boolean; onLocateSource?: (node: OutputNode) => void;
}) {
  const [selected, setSelected] = useState<OutputNode | null>(null);
  if (!ast) return <p className="p-6 text-muted-foreground">尚未生成预览。</p>;
  function render(node: OutputNode): ReactNode {
    const children = node.children?.map(render);
    const text = node.text;
    const key = node.node_id;
    switch (node.kind) {
      case "document": return <div key={key}>{children}</div>;
      case "envelope": return <section key={key} className="mt-6 border-t pt-4"><h3>{text}</h3>{children}</section>;
      case "table":
        return <div key={key} className="overflow-auto my-4">{text && <p>{text}</p>}
          <table className="w-full border-collapse text-sm"><tbody>{children}</tbody></table></div>;
      case "row": return <tr key={key}>{children}</tr>;
      case "cell": return node.header
        ? <th key={key} className="border p-2 text-left bg-muted">{text}{children}</th>
        : <td key={key} className="border p-2 align-top">{text}{children}</td>;
      case "section": return <section key={key} id={key} className="my-6"><h2 className="text-xl font-semibold">{text}</h2>{children}</section>;
      case "group": return <section key={key} id={key} className="my-4">{text && <h3 className="font-medium mb-2">{text}</h3>}{children}</section>;
      case "paragraph": return <p key={key} className="whitespace-pre-wrap leading-7">{text}{children}</p>;
      case "form_field": return <div key={key} className="grid grid-cols-[10rem_1fr] border-b py-2"><strong>{text}</strong><div>{children}</div></div>;
      case "list": {
        const items = node.children?.map((child) => <li key={child.node_id}>{render(child)}</li>);
        return node.ordered ? <ol key={key} className="list-decimal pl-6">{items}</ol> : <ul key={key} className="list-disc pl-6">{items}</ul>;
      }
      case "value": return <button key={key} type="button" onClick={() => setSelected(node)}
        className={"rounded px-0.5 text-left underline decoration-dotted underline-offset-4 " + (node.state && node.state !== "ready" ? "bg-amber-50 text-amber-900" : "")}
        title="查看固定输入和事实依据">{text}{children}</button>;
      case "signature_region": return <span key={key} className="inline-block border border-dashed px-4 py-1 text-muted-foreground">{text || "待签署"}</span>;
      case "signature": return <p key={key} className="border-l-2 pl-3 my-2">{text}</p>;
      default: return <span key={key}>{text}{children}</span>;
    }
  }
  return <div className={compact ? "space-y-3" : "grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]"}>
    <article className={"bg-white text-slate-950 rounded border min-w-0 " + (compact ? "p-3" : "p-6")}>{render(ast)}</article>
    {selected && <aside aria-label="输入与依据" className={"max-w-full border rounded p-4 space-y-3 text-sm break-all " + (compact ? "w-full" : "w-80")}>
      <button className="float-right" onClick={() => setSelected(null)} aria-label="关闭追溯">×</button>
      <strong>输入与依据</strong>
      <p>{selected.text}</p><p>状态：{selected.state || "ready"}</p>
      <p>输入：{selected.input_ref?.input_id}</p>
      <p>字段：{selected.input_ref?.field_path?.join(" / ") || "整体"}</p>
      <p>记录：{selected.input_ref?.record_id || "—"}</p>
      <p>事实引用：{selected.fact_refs?.join("、") || "无"}</p>
      {onLocateSource && !!selected.provenance_refs?.length && <button className="text-primary underline" onClick={() => onLocateSource(selected)}>定位源文档</button>}
      <details><summary>原文定位</summary><pre className="whitespace-pre-wrap">{JSON.stringify(selected.provenance_refs, null, 2)}</pre></details>
    </aside>}
  </div>;
}
