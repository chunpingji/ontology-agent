"use client";

import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { TBoxClass } from "@/lib/api";

/**
 * 本体图谱可视化（T039）：以 d3 力导向图渲染类 / 父子继承 / 对象属性约束，
 * 边标注关系类型与基数，供分析师直观核查 T-Box 结构（FR-011，AS-2）。
 */
interface GNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  kind: "class" | "filler";
}
interface GLink extends d3.SimulationLinkDatum<GNode> {
  source: string | GNode;
  target: string | GNode;
  label: string;
}

const short = (iri: string) => iri.split(/[/#]/).pop() ?? iri;

export function GraphVisualization({ classes }: { classes: TBoxClass[] }) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    if (!classes.length) return;

    const width = 640;
    const height = 420;

    const nodeMap = new Map<string, GNode>();
    const addNode = (iri: string, kind: GNode["kind"]) => {
      if (!nodeMap.has(iri)) nodeMap.set(iri, { id: iri, label: short(iri), kind });
    };
    const links: GLink[] = [];

    for (const c of classes) {
      addNode(c.slpra_iri, "class");
      if (c.parent_iri) {
        addNode(c.parent_iri, "class");
        links.push({ source: c.slpra_iri, target: c.parent_iri, label: "是一种" });
      }
      for (const r of c.restrictions) {
        if (!r.filler_iri) continue;
        addNode(r.filler_iri, "filler");
        const card = r.cardinality != null ? ` (${r.cardinality})` : "";
        const prop = r.property_iri ? short(r.property_iri) : r.kind;
        links.push({ source: c.slpra_iri, target: r.filler_iri, label: `${prop} ${r.kind}${card}` });
      }
    }

    const nodes = Array.from(nodeMap.values());

    const sim = d3
      .forceSimulation<GNode>(nodes)
      .force("link", d3.forceLink<GNode, GLink>(links).id((d) => d.id).distance(110))
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(34));

    const stage = svg
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("class", "w-full");

    stage
      .append("defs")
      .append("marker")
      .attr("id", "arrow")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 26)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", "#9ca3af");

    const link = stage
      .append("g")
      .attr("stroke", "#cbd5e1")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke-width", 1.2)
      .attr("marker-end", "url(#arrow)");

    const linkLabel = stage
      .append("g")
      .selectAll("text")
      .data(links)
      .join("text")
      .attr("font-size", 9)
      .attr("fill", "#64748b")
      .attr("text-anchor", "middle")
      .text((d) => d.label);

    const node = stage
      .append("g")
      .selectAll<SVGGElement, GNode>("g")
      .data(nodes)
      .join("g")
      .call(
        d3
          .drag<SVGGElement, GNode>()
          .on("start", (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }),
      );

    node
      .append("circle")
      .attr("r", 14)
      .attr("fill", (d) => (d.kind === "class" ? "#2563eb" : "#10b981"))
      .attr("stroke", "#fff")
      .attr("stroke-width", 1.5);

    node
      .append("text")
      .attr("x", 18)
      .attr("y", 4)
      .attr("font-size", 11)
      .attr("fill", "#111827")
      .text((d) => d.label);

    sim.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as GNode).x ?? 0)
        .attr("y1", (d) => (d.source as GNode).y ?? 0)
        .attr("x2", (d) => (d.target as GNode).x ?? 0)
        .attr("y2", (d) => (d.target as GNode).y ?? 0);
      linkLabel
        .attr("x", (d) => (((d.source as GNode).x ?? 0) + ((d.target as GNode).x ?? 0)) / 2)
        .attr("y", (d) => (((d.source as GNode).y ?? 0) + ((d.target as GNode).y ?? 0)) / 2);
      node.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => {
      sim.stop();
    };
  }, [classes]);

  if (!classes.length) {
    return <p className="p-4 text-xs text-gray-400">暂无可视化数据（请先创建类与约束）</p>;
  }

  return (
    <div className="rounded-lg border bg-white">
      <div className="flex items-center gap-3 border-b px-3 py-2 text-xs text-gray-500">
        <span className="font-semibold text-gray-700">图谱</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-blue-600" />类</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />填充类</span>
      </div>
      <svg ref={ref} className="h-[420px] w-full" />
    </div>
  );
}
