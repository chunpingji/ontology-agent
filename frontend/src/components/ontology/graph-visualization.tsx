"use client";

import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { TBoxClass, TBoxLinkType } from "@/lib/api";
import { Button } from "@/components/ui/button";

/**
 * 本体图谱可视化（T039）：以 d3 力导向图渲染类 / 父子继承 / 对象属性约束，
 * 边标注关系类型与基数，供分析师直观核查 T-Box 结构（FR-011，AS-2）。
 *
 * 视图增强：缩放/平移（滚轮 + 拖拽空白）、布局稳定后自动适配、「全局」一键
 * 缩放至铺满、「恢复」复位，以及右下角鹰眼缩略图（含当前视口指示框、可拖拽导航）。
 *
 * 树 ↔ 图联动：`selectedIri` 变化时高亮对应节点并平滑导航到它；点击图中节点
 * 反向回填 `onSelectNode`，驱动中部 / 右侧面板（避免重跑力导向布局：选中态走
 * ref + 独立 effect）。
 *
 * 关系（link type / 对象属性）叠加：`linkTypes` 中每条 `domain → range` 也画成边，
 * 但只画「模块内」的——domain 与 range 都在当前已加载类集合里（FR-011，AS-2）。
 * 用紫色虚线 + 独立箭头与 OWL 约束边（灰色实线）区分。
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
  lt?: boolean; // true = link type（对象属性声明）；否则为继承 / OWL 约束边
  ltIri?: string; // 仅 link type 边：其 slpra_iri，用于与关系面板双向高亮
}

const short = (iri: string) => iri.split(/[/#]/).pop() ?? iri;
const SELECTED = "#f59e0b"; // 选中节点的高亮描边（amber-500）
const LT_COLOR = "#7c3aed"; // 关系（link type）边的紫色（violet-600）
const LT_HOT = "#5b21b6"; // 被聚焦的关系边的加深紫（violet-800）
const NO_LINKS: TBoxLinkType[] = []; // 稳定空引用：未传 linkTypes 时不触发图重建

export function GraphVisualization({
  classes,
  linkTypes = NO_LINKS,
  selectedIri = null,
  focusedLinkIri = null,
  onSelectNode,
  onSelectLink,
}: {
  classes: TBoxClass[];
  linkTypes?: TBoxLinkType[];
  selectedIri?: string | null;
  focusedLinkIri?: string | null;
  onSelectNode?: (iri: string) => void;
  onSelectLink?: (linkTypeIri: string, domainIri: string) => void;
}) {
  const ref = useRef<SVGSVGElement | null>(null);
  const minimapRef = useRef<SVGSVGElement | null>(null);
  // 由 effect 内部赋值，供工具条按钮 / 联动调用（避免把 d3 状态提升到 React）。
  const fitRef = useRef<() => void>(() => {});
  const resetRef = useRef<() => void>(() => {});
  const focusRef = useRef<(iri: string | null, pan: boolean) => void>(() => {});
  // 关系边高亮：由 effect 内部赋值，供 [focusedLinkIri] 独立 effect 调用。
  const focusLinkRef = useRef<(iri: string | null) => void>(() => {});
  // 始终持有最新的 prop / 选中态，供 [classes] effect 闭包读取而不进依赖（否则会重建图）。
  const onSelectNodeRef = useRef(onSelectNode);
  const onSelectLinkRef = useRef(onSelectLink);
  const selectedIriRef = useRef(selectedIri);
  const focusedLinkIriRef = useRef(focusedLinkIri);
  // 标记“本次选中来自点击图节点本身”，据此跳过自动平移（节点已在眼前，无需跳动）。
  const selfSelect = useRef(false);

  // 每次渲染后把最新 prop / 选中态同步进 ref（latest-ref 模式；声明在 [classes] effect
  // 之前，确保同一提交里先刷新、后被图构建闭包读取）。
  useEffect(() => {
    onSelectNodeRef.current = onSelectNode;
    onSelectLinkRef.current = onSelectLink;
    selectedIriRef.current = selectedIri;
    focusedLinkIriRef.current = focusedLinkIri;
  });

  useEffect(() => {
    const svgEl = ref.current;
    const minimapEl = minimapRef.current;
    if (!svgEl || !minimapEl) return;
    const svg = d3.select(svgEl);
    const mm = d3.select(minimapEl);
    svg.selectAll("*").remove();
    mm.selectAll("*").remove();
    if (!classes.length) return;

    const width = 640;
    const height = 420;
    const MM_W = 150;
    const MM_H = 100;
    const MM_PAD = 6;

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

    // 关系（link type）叠加：只画「模块内」的——domain 与 range 都已作为类加载进来。
    // 跨模块的边会指向图中不存在的节点，故跳过；继承得到的关系（inherited_from_iri）也跳过。
    const classIris = new Set(classes.map((c) => c.slpra_iri));
    for (const lt of linkTypes) {
      if (lt.inherited_from_iri) continue;
      if (!lt.domain_iri || !lt.range_iri) continue;
      if (!classIris.has(lt.domain_iri) || !classIris.has(lt.range_iri)) continue;
      links.push({
        source: lt.domain_iri,
        target: lt.range_iri,
        label: lt.label || short(lt.slpra_iri),
        lt: true,
        ltIri: lt.slpra_iri,
      });
    }

    const nodes = Array.from(nodeMap.values());

    const sim = d3
      .forceSimulation<GNode>(nodes)
      .force("link", d3.forceLink<GNode, GLink>(links).id((d) => d.id).distance(110))
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(34));

    svg.attr("viewBox", `0 0 ${width} ${height}`).attr("class", "w-full");

    // 箭头标记挂在 svg 上（不随缩放容器变换）。约束边（灰）与关系边（紫）各一套。
    const defs = svg.append("defs");
    const addArrow = (id: string, fill: string) =>
      defs
        .append("marker")
        .attr("id", id)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 26)
        .attr("refY", 0)
        .attr("markerWidth", 6)
        .attr("markerHeight", 6)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", fill);
    addArrow("arrow", "#9ca3af");
    addArrow("arrow-lt", LT_COLOR);

    // 可缩放/平移的容器：所有图元都画在这里，缩放只改它的 transform。
    const container = svg.append("g");

    const link = container
      .append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", (d) => (d.lt ? LT_COLOR : "#cbd5e1"))
      .attr("stroke-width", 1.2)
      .attr("stroke-dasharray", (d) => (d.lt ? "4 3" : null))
      .attr("marker-end", (d) => (d.lt ? "url(#arrow-lt)" : "url(#arrow)"));

    const linkLabel = container
      .append("g")
      .selectAll("text")
      .data(links)
      .join("text")
      .attr("font-size", 9)
      .attr("fill", (d) => (d.lt ? LT_COLOR : "#64748b"))
      .attr("text-anchor", "middle")
      .text((d) => d.label);

    // 关系边点击 → 选中源节点 + 切到其关系面板 + 高亮该关系。线很细，叠一层透明粗线扩大命中区。
    const onLinkClick = (d: GLink) => {
      if (!d.ltIri) return;
      const domainIri = typeof d.source === "string" ? d.source : d.source.id;
      // 只有当源节点确实会切换时才置位（否则 [selectedIri] effect 不触发，标志位会残留）。
      if (domainIri !== selectedIriRef.current) selfSelect.current = true;
      onSelectLinkRef.current?.(d.ltIri, domainIri);
    };
    const linkHit = container
      .append("g")
      .selectAll<SVGLineElement, GLink>("line")
      .data(links.filter((d) => d.lt))
      .join("line")
      .attr("stroke", "transparent")
      .attr("stroke-width", 10)
      .style("cursor", "pointer")
      .on("click", (_e, d) => onLinkClick(d));
    linkLabel
      .filter((d) => !!d.lt)
      .style("cursor", "pointer")
      .on("click", (_e, d) => onLinkClick(d));

    let dragMoved = false;
    const node = container
      .append("g")
      .selectAll<SVGGElement, GNode>("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer")
      .call(
        d3
          .drag<SVGGElement, GNode>()
          .on("start", (event, d) => {
            dragMoved = false;
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            dragMoved = true;
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null;
            d.fy = null;
            // 未拖动 = 单击：反向联动选中（已是当前选中则忽略，避免无谓回填）。
            if (!dragMoved && d.id !== selectedIriRef.current) {
              selfSelect.current = true;
              onSelectNodeRef.current?.(d.id);
            }
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

    // 鹰眼缩略图：整图缩小 + 当前视口红框。
    // 底层透明命中矩形：保证在缩略图空白处也能起手拖拽 / 点击。
    mm.append("rect").attr("width", MM_W).attr("height", MM_H).attr("fill", "transparent");
    const mmLinkSel = mm
      .append("g")
      .attr("stroke-width", 0.5)
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", (d) => (d.lt ? LT_COLOR : "#cbd5e1"));
    const mmNodeSel = mm
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", 1.6)
      .attr("fill", (d) => (d.kind === "class" ? "#2563eb" : "#10b981"));
    const mmViewport = mm
      .append("rect")
      .attr("fill", "hsl(var(--primary) / 0.08)")
      .attr("stroke", "#ef4444")
      .attr("stroke-width", 1)
      .attr("pointer-events", "none");

    let currentTransform = d3.zoomIdentity;

    // 整图包围盒 → 鹰眼坐标的映射（随仿真推进每帧重算，布局稳定后即固定）。
    const mmMap = () => {
      const xs = nodes.map((n) => n.x ?? 0);
      const ys = nodes.map((n) => n.y ?? 0);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const w = maxX - minX || 1;
      const h = maxY - minY || 1;
      const s = Math.min((MM_W - 2 * MM_PAD) / w, (MM_H - 2 * MM_PAD) / h);
      const ox = MM_PAD + (MM_W - 2 * MM_PAD - s * w) / 2 - s * minX;
      const oy = MM_PAD + (MM_H - 2 * MM_PAD - s * h) / 2 - s * minY;
      return { s, ox, oy };
    };

    const drawViewport = () => {
      const { s, ox, oy } = mmMap();
      const tl = currentTransform.invert([0, 0]);
      const br = currentTransform.invert([width, height]);
      mmViewport
        .attr("x", s * tl[0] + ox)
        .attr("y", s * tl[1] + oy)
        .attr("width", s * (br[0] - tl[0]))
        .attr("height", s * (br[1] - tl[1]));
    };

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on("zoom", (event) => {
        currentTransform = event.transform;
        container.attr("transform", event.transform.toString());
        drawViewport();
      });
    svg.call(zoom);

    // 鹰眼交互：在缩略图上点击 / 拖拽，把主视图平移到对应位置（保持当前缩放）。
    const mmPanTo = (mx: number, my: number) => {
      const { s, ox, oy } = mmMap();
      const gx = (mx - ox) / s;
      const gy = (my - oy) / s;
      const k = currentTransform.k;
      svg.call(
        zoom.transform,
        d3.zoomIdentity.translate(width / 2 - k * gx, height / 2 - k * gy).scale(k),
      );
    };
    mm.call(
      d3
        .drag<SVGSVGElement, unknown>()
        .container(minimapEl)
        .on("start", (event) => mmPanTo(event.x, event.y))
        .on("drag", (event) => mmPanTo(event.x, event.y)),
    );

    const fitToView = () => {
      const pad = 48;
      const xs = nodes.map((n) => n.x ?? 0);
      const ys = nodes.map((n) => n.y ?? 0);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const w = maxX - minX || 1;
      const h = maxY - minY || 1;
      const scale = Math.min(width / (w + pad * 2), height / (h + pad * 2), 2);
      const tx = (width - scale * (minX + maxX)) / 2;
      const ty = (height - scale * (minY + maxY)) / 2;
      svg
        .transition()
        .duration(400)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    };

    const reset = () => {
      svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity);
    };

    // 选中态高亮：放大 + 琥珀色描边 + 标签加粗（主图与鹰眼同步）。
    const applyHighlight = (iri: string | null) => {
      node
        .select<SVGCircleElement>("circle")
        .attr("r", (d) => (d.id === iri ? 17 : 14))
        .attr("stroke", (d) => (d.id === iri ? SELECTED : "#fff"))
        .attr("stroke-width", (d) => (d.id === iri ? 3 : 1.5));
      node
        .select<SVGTextElement>("text")
        .attr("font-weight", (d) => (d.id === iri ? 700 : 400))
        .attr("fill", (d) => (d.id === iri ? "#b45309" : "#111827"));
      mmNodeSel
        .attr("r", (d) => (d.id === iri ? 3.2 : 1.6))
        .attr("fill", (d) => (d.id === iri ? SELECTED : d.kind === "class" ? "#2563eb" : "#10b981"));
    };

    // 关系边高亮：与关系面板联动。iri 命中的关系边加粗、加深紫并提亮标签（鹰眼同步）。
    // 只有 link type 边带 ltIri，故 `d.ltIri === iri` 天然只命中关系边；iri 为 null 即全部复位。
    const applyLinkHighlight = (iri: string | null) => {
      const hot = (d: GLink) => d.ltIri != null && d.ltIri === iri;
      link
        .attr("stroke", (d) => (hot(d) ? LT_HOT : d.lt ? LT_COLOR : "#cbd5e1"))
        .attr("stroke-width", (d) => (hot(d) ? 2.6 : 1.2));
      linkLabel
        .attr("font-weight", (d) => (hot(d) ? 700 : 400))
        .attr("fill", (d) => (hot(d) ? LT_HOT : d.lt ? LT_COLOR : "#64748b"));
      mmLinkSel
        .attr("stroke", (d) => (hot(d) ? LT_HOT : d.lt ? LT_COLOR : "#cbd5e1"))
        .attr("stroke-width", (d) => (hot(d) ? 1.6 : 0.5));
    };

    // 平移并缩放到目标节点（节点居中；视图过远时拉近到至少 1.4 倍）。
    const panToNode = (n: GNode) => {
      const k = Math.max(currentTransform.k, 1.4);
      svg
        .transition()
        .duration(500)
        .call(
          zoom.transform,
          d3.zoomIdentity.translate(width / 2 - k * (n.x ?? 0), height / 2 - k * (n.y ?? 0)).scale(k),
        );
    };

    // 若布局尚未铺开（节点无坐标），先记下待定目标，等 sim 稳定后再导航。
    let pendingFocus: string | null = null;
    const focusNode = (iri: string | null, pan: boolean) => {
      applyHighlight(iri);
      if (!iri || !pan) return;
      const n = nodeMap.get(iri);
      if (!n || n.x == null || n.y == null) {
        pendingFocus = iri;
        return;
      }
      panToNode(n);
    };

    fitRef.current = fitToView;
    resetRef.current = reset;
    focusRef.current = focusNode;
    focusLinkRef.current = applyLinkHighlight;

    // 初次构建即按当前选中态 / 聚焦关系点亮（切模块后图重建时复原高亮）。
    applyHighlight(selectedIriRef.current);
    applyLinkHighlight(focusedLinkIriRef.current);

    let autoFitted = false;
    sim.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as GNode).x ?? 0)
        .attr("y1", (d) => (d.source as GNode).y ?? 0)
        .attr("x2", (d) => (d.target as GNode).x ?? 0)
        .attr("y2", (d) => (d.target as GNode).y ?? 0);
      linkHit
        .attr("x1", (d) => (d.source as GNode).x ?? 0)
        .attr("y1", (d) => (d.source as GNode).y ?? 0)
        .attr("x2", (d) => (d.target as GNode).x ?? 0)
        .attr("y2", (d) => (d.target as GNode).y ?? 0);
      linkLabel
        .attr("x", (d) => (((d.source as GNode).x ?? 0) + ((d.target as GNode).x ?? 0)) / 2)
        .attr("y", (d) => (((d.source as GNode).y ?? 0) + ((d.target as GNode).y ?? 0)) / 2);
      node.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);

      const { s, ox, oy } = mmMap();
      mmNodeSel.attr("cx", (d) => s * (d.x ?? 0) + ox).attr("cy", (d) => s * (d.y ?? 0) + oy);
      mmLinkSel
        .attr("x1", (d) => s * ((d.source as GNode).x ?? 0) + ox)
        .attr("y1", (d) => s * ((d.source as GNode).y ?? 0) + oy)
        .attr("x2", (d) => s * ((d.target as GNode).x ?? 0) + ox)
        .attr("y2", (d) => s * ((d.target as GNode).y ?? 0) + oy);
      drawViewport();
    });

    // 力导向布局首次稳定后：有待定导航目标则聚焦它，否则自动适配铺满。
    sim.on("end", () => {
      if (pendingFocus) {
        const n = nodeMap.get(pendingFocus);
        pendingFocus = null;
        autoFitted = true;
        if (n) {
          panToNode(n);
          return;
        }
      }
      if (autoFitted) return;
      autoFitted = true;
      fitToView();
    });

    return () => {
      sim.stop();
    };
  }, [classes, linkTypes]);

  // 选中态变化 → 高亮并（若非点击图节点本身触发）平滑导航到对应节点。
  useEffect(() => {
    const pan = !selfSelect.current;
    selfSelect.current = false;
    focusRef.current(selectedIri, pan);
  }, [selectedIri]);

  // 聚焦关系变化（来自关系面板或点击图边）→ 高亮对应关系边。
  useEffect(() => {
    focusLinkRef.current(focusedLinkIri);
  }, [focusedLinkIri]);

  if (!classes.length) {
    return <p className="p-4 text-xs text-muted-foreground">暂无可视化数据（请先创建类与约束）</p>;
  }

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center gap-3 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">图谱</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-primary" />类</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full bg-success" />填充类</span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: LT_COLOR }} />关系
        </span>
        <div className="ml-auto flex gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-auto px-2 py-1 text-xs"
            onClick={() => fitRef.current()}
          >
            全局
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-auto px-2 py-1 text-xs"
            onClick={() => resetRef.current()}
          >
            恢复
          </Button>
        </div>
      </div>
      <div className="relative">
        <svg ref={ref} className="h-[420px] w-full cursor-grab active:cursor-grabbing" />
        <svg
          ref={minimapRef}
          viewBox="0 0 150 100"
          className="absolute bottom-2 right-2 h-[100px] w-[150px] cursor-crosshair rounded border border-border bg-background/80 shadow-sm"
        />
      </div>
    </div>
  );
}
