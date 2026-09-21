"use client";

import { useId, useMemo, useState } from "react";
import { Minus, Plus, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DocumentAnalysisTargetGraph } from "@/lib/api";
import { entityRefKey } from "@/lib/document-graph";
import { buildTargetGraph, REVIEW_STATE_LABELS } from "@/lib/target-graph";

export function TargetGraphCanvas({ artifact, selectedSubject, onSelectSubject, onSelectTarget, showNotAccepted = false }: {
  artifact: DocumentAnalysisTargetGraph;
  selectedSubject: string;
  onSelectSubject: (id: string) => void;
  onSelectTarget: (id: string) => void;
  showNotAccepted?: boolean;
}) {
  const rootId = entityRefKey(artifact.root);
  const candidatePhase = artifact.phase === "candidate_graph";
  const reviewPhase = artifact.phase === "evidence_review";
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([rootId]));
  const [zoom, setZoom] = useState(0.85);
  const [limit, setLimit] = useState(80);
  const markerId = useId().replace(/:/g, "");
  const graph = useMemo(() => {
    const rootGraph = buildTargetGraph(artifact, expanded, limit, undefined, showNotAccepted);
    return rootGraph.nodes.some((node) => node.id === selectedSubject) ? rootGraph
      : buildTargetGraph(artifact, expanded, limit, selectedSubject, showNotAccepted);
  }, [artifact, expanded, limit, selectedSubject, showNotAccepted]);
  const layout = useMemo(() => {
    const rows = new Map<number, number>();
    return graph.nodes.map((node) => {
      const row = rows.get(node.depth) ?? 0;
      rows.set(node.depth, row + 1);
      return { ...node, x: 30 + node.depth * 440, y: 35 + row * 115 };
    });
  }, [graph.nodes]);
  const nodeMap = new Map(layout.map((node) => [node.id, node]));
  const width = Math.max(900, ...layout.map((node) => node.x + 260));
  const height = Math.max(500, ...layout.map((node) => node.y + 115));
  const toggleExpanded = (id: string) => setExpanded((value) => {
    const next = new Set(value);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
          {!candidatePhase && <span className="flex items-center gap-2"><span className="w-7 border-t-2 border-primary" />{reviewPhase ? "原文已采信" : "原文已实证"}</span>}
          <span className="flex items-center gap-2"><span className="w-7 border-t-2 border-dashed border-muted-foreground" />{candidatePhase ? "原文候选 / 本体目标 · 未核验" : reviewPhase ? "待定候选 / 本体目标" : "待识别 / 待核验"}</span>
          {reviewPhase && showNotAccepted && <span className="flex items-center gap-2 text-destructive"><span className="w-7 border-t-2 border-dashed border-destructive" />未采信</span>}
          <span>{candidatePhase ? "点击节点查看候选，点击 + 展开关系；也可从下方选择未关联实体" : "点击节点查看属性，点击 + 展开关系"}</span>
        </div>
        <div className="flex items-center gap-1">
          <Button size="icon" variant="ghost" aria-label="缩小图谱" onClick={() => setZoom((value) => Math.max(0.25, value - 0.15))}><Minus className="size-4" /></Button>
          <span className="w-12 text-center text-xs">{Math.round(zoom * 100)}%</span>
          <Button size="icon" variant="ghost" aria-label="放大图谱" onClick={() => setZoom((value) => Math.min(2, value + 0.15))}><Plus className="size-4" /></Button>
          <Button size="icon" variant="ghost" aria-label="回到根节点" onClick={() => { setExpanded(new Set([rootId])); onSelectSubject(rootId); setZoom(0.85); }}><RotateCcw className="size-4" /></Button>
        </div>
      </div>
      <div className="h-[520px] overflow-auto bg-muted/20" aria-label="本体目标关系图谱">
        <svg width={width * zoom} height={height * zoom} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={candidatePhase ? "候选图谱：所有关系均为虚线，尚未核验" : reviewPhase ? "原文证据图谱：实线表示已采信，虚线表示待定或未采信" : "实线表示原文实证，虚线表示尚未实证的关系"}>
          <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
          {graph.edges.map((edge) => {
            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            if (!source || !target) return null;
            const forward = source.x <= target.x;
            const x1 = source.x + (forward ? 224 : 0);
            const x2 = target.x + (forward ? 0 : 224);
            const y1 = source.y + 39;
            const y2 = target.y + 39;
            const path = `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
            // Keep labels beside the deeper node: curve midpoints compress
            // the vertical spacing of a root's many outgoing relations.
            const labelNode = source.depth > target.depth ? source : target;
            const select = () => onSelectTarget(edge.targetId);
            const status = candidatePhase ? edge.id.startsWith("target:") ? "本体关系目标，尚无原文候选" : "原文候选，未核验"
              : reviewPhase ? edge.reviewState ? REVIEW_STATE_LABELS[edge.reviewState] : "本体关系目标"
              : edge.supported ? "原文已实证" : "待核验";
            return <g key={edge.id} role="button" tabIndex={0} aria-label={`${edge.label}，${status}`} onClick={select} onKeyDown={(event) => { if (event.key === "Enter") select(); }} className={`cursor-pointer outline-none focus:text-primary ${edge.supported ? "text-primary" : edge.reviewState === "not_accepted" ? "text-destructive" : "text-muted-foreground"}`}>
              <title>{`${edge.label} · ${status}`}</title>
              <path d={path} fill="none" stroke="transparent" strokeWidth={16} />
              <path d={path} fill="none" stroke={edge.supported ? "hsl(var(--primary))" : "currentColor"} strokeWidth={edge.supported ? 2 : 1.5} strokeDasharray={edge.supported ? undefined : "6 5"} markerEnd={`url(#${markerId})`} />
              <text x={labelNode.x - 12} y={labelNode.y + 29} textAnchor="end" className="fill-current text-[11px]" paintOrder="stroke" stroke="hsl(var(--card))" strokeWidth={4}>{edge.label.length > 19 ? `${edge.label.slice(0, 18)}…` : edge.label}</text>
            </g>;
          })}
          {layout.map((node) => {
            const selected = node.id === selectedSubject;
            const select = () => node.targetId ? onSelectTarget(node.targetId) : onSelectSubject(node.id);
            return <g key={node.id} transform={`translate(${node.x},${node.y})`}>
              <g role="button" tabIndex={0} aria-label={`${node.label}，${node.subtitle}`} onClick={select} onKeyDown={(event) => { if (event.key === "Enter") select(); }} className="cursor-pointer outline-none">
                <title>{`${node.label} · ${node.subtitle}`}</title>
                <rect width={224} height={78} rx={12} fill={selected ? "hsl(var(--accent))" : "hsl(var(--card))"} stroke={selected || node.root ? "hsl(var(--primary))" : "hsl(var(--border))"} strokeWidth={selected ? 2 : 1.5} strokeDasharray={node.kind === "placeholder" ? "5 4" : undefined} />
                <text x={14} y={29} className="fill-foreground text-[13px] font-medium">{node.label.length > 15 ? `${node.label.slice(0, 14)}…` : node.label}</text>
                <text x={14} y={53} className="fill-muted-foreground text-[11px]">{node.subtitle.length > 23 ? `${node.subtitle.slice(0, 22)}…` : node.subtitle}</text>
              </g>
              {node.expandable && <g role="button" tabIndex={0} aria-label={`${expanded.has(node.id) ? "收起" : "展开"}${node.label}的关系`} onClick={() => toggleExpanded(node.id)} onKeyDown={(event) => { if (event.key === "Enter") toggleExpanded(node.id); }} className="cursor-pointer text-primary">
                <circle cx={211} cy={13} r={12} fill="hsl(var(--background))" stroke="currentColor" />
                <text x={211} y={18} textAnchor="middle" className="fill-current text-base">{expanded.has(node.id) ? "−" : "+"}</text>
              </g>}
            </g>;
          })}
        </svg>
      </div>
      {graph.hiddenItems > 0 && <div className="flex items-center justify-between gap-3 border-t px-4 py-2 text-xs text-muted-foreground"><span>当前显示 {graph.nodes.length} 个节点，还有展开内容未显示。</span><Button size="sm" variant="outline" onClick={() => setLimit((value) => value + 80)}>再显示 80 个节点</Button></div>}
    </div>
  );
}
