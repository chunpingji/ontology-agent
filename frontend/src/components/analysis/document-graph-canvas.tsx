"use client";

import { useEffect, useId, useMemo, useRef } from "react";
import * as d3 from "d3";
import { Maximize, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DocumentAnalysisGraphArtifact } from "@/lib/api";
import {
  buildDocumentGraph, graphSelectionKey,
  type DocumentCanvasLink, type DocumentCanvasNode, type GraphSelection,
} from "@/lib/document-graph";

interface Node extends DocumentCanvasNode, d3.SimulationNodeDatum {}
interface Link extends Omit<DocumentCanvasLink, "source" | "target">, d3.SimulationLinkDatum<Node> {
  source: string | Node;
  target: string | Node;
  bend: number;
}

export function DocumentGraphCanvas({ artifact, selected, onSelect }: {
  artifact: DocumentAnalysisGraphArtifact;
  selected: GraphSelection | null;
  onSelect: (selection: GraphSelection) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const markerId = `document-arrow-${useId().replace(/:/g, "")}`;
  // Polling may return a new artifact object for the same graph. Preserve the
  // layout/zoom/focus unless the actual visual nodes or links have changed.
  const modelJson = JSON.stringify(buildDocumentGraph(artifact));
  const model = useMemo(() => JSON.parse(modelJson) as ReturnType<typeof buildDocumentGraph>, [modelJson]);
  const callbacks = useRef({ selected, onSelect });
  const controls = useRef<{ fit: () => void; zoom: (scale: number) => void }>({ fit: () => {}, zoom: () => {} });
  const highlight = useRef(() => {});

  useEffect(() => {
    callbacks.current = { selected, onSelect };
    highlight.current();
  }, [selected, onSelect]);

  useEffect(() => {
    const element = svgRef.current;
    if (!element || !model.nodes.length) return;
    const svg = d3.select(element);
    let width = element.clientWidth || 720;
    const height = 480;
    svg.attr("viewBox", `0 0 ${width} ${height}`);
    const nodes: Node[] = model.nodes.map((node) => ({ ...node }));
    const pairs = d3.group(model.links, (link) => JSON.stringify([link.source, link.target].sort()));
    const links: Link[] = model.links.map((link) => {
      const siblings = pairs.get(JSON.stringify([link.source, link.target].sort()))!;
      return { ...link, bend: (siblings.indexOf(link) - (siblings.length - 1) / 2) * 44 };
    });
    const defs = svg.append("defs");
    defs.append("marker").attr("id", markerId).attr("viewBox", "0 -5 10 10")
      .attr("refX", 9).attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto")
      .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "currentColor").attr("class", "text-muted-foreground");
    const container = svg.append("g");
    const zoom = d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.08, 4])
      .on("zoom", (event) => container.attr("transform", event.transform.toString()));
    svg.call(zoom).on("dblclick.zoom", null);
    const simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink<Node, Link>(links).id((node) => node.id).distance(180))
      .force("charge", d3.forceManyBody().strength(-650))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("x", d3.forceX(width / 2).strength(0.06))
      .force("y", d3.forceY(height / 2).strength(0.06))
      .force("collide", d3.forceCollide(74));

    const select = (selection: GraphSelection) => callbacks.current.onSelect(selection);
    const edge = container.append("g").selectAll<SVGGElement, Link>("g").data(links).join("g")
      .attr("role", "button").attr("tabindex", 0)
      .attr("aria-label", (link) => `查看${link.group ? "关系组连接" : "关系"} ${link.label}`)
      .attr("class", "cursor-pointer text-muted-foreground outline-none focus:text-primary")
      .on("click", (_event, link) => select(link.selection))
      .on("keydown", (event: KeyboardEvent, link) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(link.selection); }
      });
    // Transparent wide paths make narrow edges selectable without changing their meaning.
    const hit = edge.append("path").attr("fill", "none").attr("stroke", "transparent").attr("stroke-width", 16);
    const line = edge.append("path").attr("fill", "none").attr("stroke", "currentColor")
      .attr("stroke-width", 1.6).attr("stroke-dasharray", (link) => link.qualified ? "5 4" : null)
      .attr("marker-end", `url(#${markerId})`);
    const label = edge.append("text").attr("text-anchor", "middle").attr("font-size", 11)
      .attr("fill", "currentColor").attr("stroke", "hsl(var(--background))").attr("stroke-width", 4)
      .attr("paint-order", "stroke").text((link) => link.label);
    edge.append("title").text((link) => link.label);

    const node = container.append("g").selectAll<SVGGElement, Node>("g").data(nodes).join("g")
      .attr("role", "button").attr("tabindex", 0)
      .attr("aria-label", (item) => `查看${item.kind === "entity" ? "实体" : "关系组"} ${item.label}，${item.subtitle}`)
      .attr("data-node-kind", (item) => item.kind)
      .attr("class", "cursor-pointer outline-none text-muted-foreground focus:text-primary")
      .on("click", (event, item) => { if (!event.defaultPrevented) select(item.selection); })
      .on("keydown", (event: KeyboardEvent, item) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(item.selection); }
      })
      .call(d3.drag<SVGGElement, Node>()
        .on("start", (event, item) => {
          if (!event.active) simulation.alphaTarget(0.25).restart();
          item.fx = item.x; item.fy = item.y;
        })
        .on("drag", (event, item) => { item.fx = event.x; item.fy = event.y; })
        .on("end", (event, item) => {
          if (!event.active) simulation.alphaTarget(0);
          item.fx = null; item.fy = null;
        }));
    node.append("path")
      .attr("d", (item) => item.kind === "relationship_group"
        ? "M0,-32L44,0L0,32L-44,0Z" : "M-28,0a28,28 0 1,0 56,0a28,28 0 1,0 -56,0")
      .attr("fill", "hsl(var(--background))").attr("stroke", "currentColor").attr("stroke-width", 2.5);
    node.filter((item) => item.root).append("circle").attr("r", 22)
      .attr("fill", "none").attr("stroke", "currentColor").attr("stroke-width", 1);
    node.append("text").attr("text-anchor", "middle").attr("dy", 4).attr("font-size", 11)
      .attr("fill", "currentColor").text((item) => item.root ? "根" : item.kind === "entity" ? "实体" : "组");
    node.append("text").attr("text-anchor", "middle").attr("y", 48).attr("font-size", 13)
      .attr("fill", "hsl(var(--foreground))").text((item) => item.label.length > 20 ? `${item.label.slice(0, 20)}…` : item.label);
    node.append("text").attr("text-anchor", "middle").attr("y", 65).attr("font-size", 10)
      .attr("fill", "hsl(var(--muted-foreground))").text((item) => item.subtitle);
    node.append("title").text((item) => `${item.label}\n${item.subtitle}`);

    highlight.current = () => {
      const current = callbacks.current.selected;
      const key = current ? graphSelectionKey(current) : null;
      node.attr("aria-pressed", (item) => String(item.id === key))
        .attr("class", (item) => `cursor-pointer outline-none focus:text-primary ${item.id === key || item.root ? "text-primary" : "text-muted-foreground"}`);
      edge.attr("aria-pressed", (item) => String(graphSelectionKey(item.selection) === key))
        .attr("class", (item) => `cursor-pointer outline-none focus:text-primary ${graphSelectionKey(item.selection) === key ? "text-primary" : "text-muted-foreground"}`);
    };
    highlight.current();
    function geometry(link: Link) {
      const source = link.source as Node, target = link.target as Node;
      const sx = source.x ?? 0, sy = source.y ?? 0, tx = target.x ?? 0, ty = target.y ?? 0;
      if (source.id === target.id) {
        const radius = 65 + Math.abs(link.bend);
        return { path: `M${sx - 20},${sy - 22}C${sx - radius},${sy - radius * 2} ${sx + radius},${sy - radius * 2} ${sx + 20},${sy - 22}`,
          x: sx, y: sy - radius * 1.5 };
      }
      const dx = tx - sx, dy = ty - sy, length = Math.hypot(dx, dy) || 1;
      const sign = source.id < target.id ? 1 : -1;
      const cx = (sx + tx) / 2 - dy / length * link.bend * sign;
      const cy = (sy + ty) / 2 + dx / length * link.bend * sign;
      const trim = (x: number, y: number, radius: number) => {
        const distance = Math.hypot(cx - x, cy - y) || 1;
        return [x + (cx - x) / distance * radius, y + (cy - y) / distance * radius];
      };
      const start = trim(sx, sy, source.kind === "entity" ? 30 : 40);
      const end = trim(tx, ty, target.kind === "entity" ? 33 : 43);
      return { path: `M${start}Q${cx},${cy} ${end}`, x: (sx + 2 * cx + tx) / 4, y: (sy + 2 * cy + ty) / 4 - 7 };
    }
    const draw = () => {
      node.attr("transform", (item) => `translate(${item.x},${item.y})`);
      hit.attr("d", (link) => geometry(link).path);
      line.attr("d", (link) => geometry(link).path);
      label.attr("x", (link) => geometry(link).x).attr("y", (link) => geometry(link).y);
    };
    const fit = () => {
      const bounds = container.node()?.getBBox();
      if (!bounds?.width || !bounds.height) return;
      const scale = Math.max(0.08, Math.min(1.4, (width - 48) / bounds.width, (height - 48) / bounds.height));
      svg.call(zoom.transform, d3.zoomIdentity.translate(width / 2, height / 2).scale(scale)
        .translate(-bounds.x - bounds.width / 2, -bounds.y - bounds.height / 2));
    };
    controls.current = { fit, zoom: (scale) => { svg.call(zoom.scaleBy, scale); } };
    // Stabilize the initial layout before fitting; subsequent dragging remains live.
    simulation.stop().tick(160);
    draw(); fit();
    simulation.on("tick", draw);
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width <= 0) return;
      width = entry.contentRect.width;
      svg.attr("viewBox", `0 0 ${width} ${height}`);
      simulation.force("center", d3.forceCenter(width / 2, height / 2))
        .force("x", d3.forceX(width / 2).strength(0.06));
      fit();
    });
    observer.observe(element);
    return () => {
      simulation.stop(); observer.disconnect(); svg.on(".zoom", null); svg.selectAll("*").remove();
      controls.current = { fit: () => {}, zoom: () => {} }; highlight.current = () => {};
    };
  }, [model, markerId]);

  return (
    <section aria-label="本体实例关系图" className="min-w-0 overflow-hidden rounded-lg border bg-muted/10">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-background p-3">
        <div className="text-sm font-medium">{artifact.entities.length} 个实体 · {artifact.relationships.length} 条关系 · {artifact.relationship_groups?.length ?? 0} 个关系组</div>
        <div className="flex gap-1">
          <Button size="icon" variant="ghost" aria-label="放大图谱" onClick={() => controls.current.zoom(1.3)}><Plus className="size-4" /></Button>
          <Button size="icon" variant="ghost" aria-label="缩小图谱" onClick={() => controls.current.zoom(1 / 1.3)}><Minus className="size-4" /></Button>
          <Button size="sm" variant="outline" onClick={() => controls.current.fit()}><Maximize className="size-4" />适配全图</Button>
        </div>
      </div>
      {model.nodes.length ? <svg ref={svgRef} role="group" aria-label="可交互关系图谱" className="h-[480px] w-full touch-none" />
        : <p className="flex h-64 items-center justify-center p-6 text-sm text-muted-foreground">当前视图暂无图谱节点。</p>}
      <p className="border-t bg-background p-3 text-xs leading-relaxed text-muted-foreground">圆形：本体实体 · 双圈：文档根 · 菱形：关系组（不计入实体数） · 虚线：组连接或带限定的关系。滚轮缩放，拖动节点或空白平移，点击或按 Enter 查看详情。</p>
      {model.missingEndpoints > 0 && <p role="status" className="px-3 pb-3 text-xs text-muted-foreground">{model.missingEndpoints} 处连接缺少当前投影中的精确版本端点，未绘制连线；仍可在关系详情查看。</p>}
    </section>
  );
}
