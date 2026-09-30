"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type Sigma from "sigma";

import { Button } from "@/components/ui/button";
import type { DocumentHarnessEntity, DocumentHarnessGraph, DocumentHarnessRelation } from "@/lib/api";
import { rootedCircleLayout } from "@/lib/source-harness-graph-layout";
import {
  buildHarnessHierarchy, harnessAncestorPredicateGroupIds, harnessPredicateGroupId,
  HARNESS_STATES, relationQualifier,
} from "@/lib/source-harness";

type RelationGroup = {
  id: string;
  subjectId: string;
  predicateIri: string;
  label: string;
  relations: DocumentHarnessRelation[];
  open: boolean;
};
type GraphNode = { id: string; x: number; y: number; size: number; label: string; color: string };
type GraphEdge = { id: string; source: string; target: string; label: string; color: string; size: number };

function relationColor(relation: DocumentHarnessRelation) {
  if (relation.state === "rejected" || relation.polarity === "negative") return "#ef4444";
  if (relation.state === "accepted") return "#059669";
  if (relation.state === "unresolved") return "#d97706";
  return "#64748b";
}

function entityColor(entity: DocumentHarnessEntity) {
  if (entity.role === "document_root") return "#2563eb";
  if (entity.state === "accepted") return "#059669";
  if (entity.state === "rejected") return "#ef4444";
  if (entity.state === "unresolved") return "#d97706";
  return "#64748b";
}

export function HarnessRelationCanvas({ graph, subject, onSelectEntity, visible, maxDepth = Infinity, revealGroups = false }: {
  graph: DocumentHarnessGraph;
  subject?: DocumentHarnessEntity;
  onSelectEntity: (id: string) => void;
  visible?: Set<string>;
  maxDepth?: number;
  revealGroups?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const renderer = useRef<Sigma | null>(null);
  const cameraState = useRef<{ x: number; y: number; ratio: number; angle: number } | null>(null);
  const rootFitState = useRef<{ x: number; y: number; ratio: number; angle: number } | null>(null);
  const selectedId = useRef(subject?.id);
  const selectEntity = useRef(onSelectEntity);
  const toggleGroup = useRef<(id: string) => void>(() => undefined);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [hovered, setHovered] = useState("");
  const [renderError, setRenderError] = useState<string | null>(null);
  const hierarchy = useMemo(() => buildHarnessHierarchy(graph), [graph]);

  const display = useMemo(() => {
    const selectedPath = harnessAncestorPredicateGroupIds(hierarchy, subject?.id);
    const eligible = graph.entities.filter((entity) => (!visible || visible.has(entity.id))
      && (hierarchy.depth.get(entity.id) ?? 0) <= maxDepth);
    const ordered = [...eligible].sort((a, b) => (hierarchy.depth.get(a.id) ?? Infinity)
      - (hierarchy.depth.get(b.id) ?? Infinity));
    const eligibleIds = new Set(ordered.map((entity) => entity.id));
    const byId = new Map<string, RelationGroup>();
    for (const relation of graph.relations) {
      if (!eligibleIds.has(relation.subject_id) || !eligibleIds.has(relation.object_id)) continue;
      const id = harnessPredicateGroupId(relation.subject_id, relation.predicate_iri);
      const group = byId.get(id) ?? {
        id, subjectId: relation.subject_id, predicateIri: relation.predicate_iri,
        label: relation.label, relations: [], open: false,
      };
      group.relations.push(relation);
      byId.set(id, group);
    }
    const groupsBySubject = new Map<string, RelationGroup[]>();
    for (const group of byId.values()) {
      group.open = revealGroups || !collapsedGroups.has(group.id) || selectedPath.has(group.id);
      groupsBySubject.set(group.subjectId, [...groupsBySubject.get(group.subjectId) ?? [], group]);
    }
    const groupedObjects = new Set([...byId.values()].flatMap((group) => group.relations.map((item) => item.object_id)));
    const queue = [
      ...hierarchy.roots.filter((entity) => eligibleIds.has(entity.id)).map((entity) => entity.id),
      ...hierarchy.disconnected.filter((entity) => eligibleIds.has(entity.id) && !groupedObjects.has(entity.id)).map((entity) => entity.id),
      ...(revealGroups || !collapsedGroups.size ? ordered.map((entity) => entity.id) : []),
    ];
    if (subject && eligibleIds.has(subject.id)) queue.push(subject.id);
    if (!queue.length && ordered[0]) queue.push(ordered[0].id);
    const shown = new Set<string>();
    const groups: RelationGroup[] = [];
    for (let index = 0; index < queue.length; index++) {
      const id = queue[index];
      if (shown.has(id)) continue;
      shown.add(id);
      for (const group of groupsBySubject.get(id) ?? []) {
        groups.push(group);
        if (group.open) queue.push(...group.relations.map((relation) => relation.object_id));
      }
    }
    const entities = ordered.filter((entity) => shown.has(entity.id));
    const layers = new Map<number, DocumentHarnessEntity[]>();
    for (const entity of entities) {
      const level = hierarchy.depth.get(entity.id) ?? -1;
      layers.set(level, [...layers.get(level) ?? [], entity]);
    }
    const groupLayers = new Map<number, RelationGroup[]>();
    for (const group of groups) {
      const level = hierarchy.depth.get(group.subjectId) ?? -1;
      groupLayers.set(level, [...groupLayers.get(level) ?? [], group]);
    }
    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];
    const maxConnectedRows = Math.max(1, ...[...layers, ...groupLayers]
      .filter(([level]) => level >= 0).map(([, items]) => items.length));
    for (const [level, items] of layers) {
      items.forEach((entity, index) => {
        const disconnected = level < 0;
        nodes.push({
          id: `entity:${entity.id}`,
          x: disconnected ? index % 3 * 8 : level * 8,
          y: disconnected ? -maxConnectedRows * 2.8 - 5 - Math.floor(index / 3) * 2.8
            : -index * 2.8,
          size: entity.role === "document_root" ? 19 : 15,
          label: entity.label,
          color: entityColor(entity),
        });
      });
    }
    for (const [level, items] of groupLayers) {
      items.forEach((group, index) => {
        const source = `entity:${group.subjectId}`;
        const id = `group:${group.id}`;
        nodes.push({
          id, x: level < 0 ? 3.5 + index % 3 * 8 : level * 8 + 3.5,
          y: level < 0 ? -maxConnectedRows * 2.8 - 5 - Math.floor(index / 3) * 2.8
            : -index * 2.8,
          size: 11, label: group.label, color: "#7c3aed",
        });
        edges.push({ id: `group-link:${group.id}`, source, target: id,
          label: group.label, color: "#94a3b8", size: 1.5 });
        if (group.open) for (const relation of group.relations) {
          if (!shown.has(relation.object_id)) continue;
          edges.push({ id: `relation:${relation.id}`, source: id,
            target: `entity:${relation.object_id}`,
            label: `${relation.label} · ${HARNESS_STATES[relation.state]}`,
            color: relationColor(relation), size: relation.state === "accepted" ? 2.5 : 1.7 });
        }
      });
    }
    return { entities, groups, nodes, edges,
      rootId: hierarchy.roots.find((entity) => shown.has(entity.id))?.id,
      entityById: new Map(entities.map((entity) => [`entity:${entity.id}`, entity])),
      groupById: new Map(groups.map((group) => [`group:${group.id}`, group])),
      relationById: new Map(graph.relations.map((relation) => [`relation:${relation.id}`, relation])),
    };
  }, [graph, hierarchy, visible, maxDepth, collapsedGroups, subject, revealGroups]);

  useEffect(() => {
    selectedId.current = subject?.id;
    selectEntity.current = onSelectEntity;
    toggleGroup.current = (id) => {
      const group = display.groups.find((item) => item.id === id);
      if (!group) return;
      if (harnessAncestorPredicateGroupIds(hierarchy, subject?.id).has(id)) onSelectEntity(group.subjectId);
      setCollapsedGroups((current) => {
        const next = new Set(current);
        if (group.open) next.add(id); else next.delete(id);
        return next;
      });
    };
  }, [display, hierarchy, onSelectEntity, subject?.id]);

  useEffect(() => {
    const target = container.current;
    if (!target || !display.nodes.length) return;
    let cancelled = false;
    let mounted: Sigma | null = null;
    const previousCamera = cameraState.current;
    setRenderError(null);
    void Promise.all([import("graphology"), import("graphology-layout-forceatlas2"), import("sigma")])
      .then(([graphology, forceAtlas2, sigma]) => {
        if (cancelled) return;
        const network = new graphology.default({ type: "directed", multi: true });
        for (const node of display.nodes) network.addNode(node.id, node);
        for (const edge of display.edges) network.addEdgeWithKey(edge.id, edge.source, edge.target,
          { label: edge.label, color: edge.color, size: edge.size, type: "arrow" });
        if (network.order > 1) forceAtlas2.default.assign(network, {
          iterations: network.order > 400 ? 40 : 80,
          settings: { ...forceAtlas2.default.inferSettings(network), adjustSizes: true,
            barnesHutOptimize: network.order > 100 },
        });
        const positions = rootedCircleLayout(display.nodes.map((node) => ({
          id: node.id, x: network.getNodeAttribute(node.id, "x"), y: network.getNodeAttribute(node.id, "y"),
        })), display.edges, display.rootId && `entity:${display.rootId}`);
        for (const [id, position] of positions) network.mergeNodeAttributes(id, position);
        mounted = new sigma.default(network, target, {
          enableEdgeEvents: true, renderEdgeLabels: false,
          labelColor: { color: getComputedStyle(target).color },
          labelSize: 13, labelDensity: 0.8, labelRenderedSizeThreshold: 7,
          stagePadding: 90, minCameraRatio: 0.08, maxCameraRatio: 8,
          nodeReducer: (node, data) => node === `entity:${selectedId.current}`
            ? { ...data, size: Number(data.size) + 5, color: "#2563eb", forceLabel: true,
              highlighted: true } : data,
        });
        renderer.current = mounted;
        const rootData = display.rootId && mounted.getNodeDisplayData(`entity:${display.rootId}`);
        rootFitState.current = rootData ? {
          x: rootData.x, y: rootData.y, angle: 0,
          ratio: Math.max(1, ...display.nodes.map((node) => {
            const data = mounted!.getNodeDisplayData(node.id);
            return data ? 2.1 * Math.max(Math.abs(data.x - rootData.x), Math.abs(data.y - rootData.y)) : 1;
          })),
        } : null;
        if (previousCamera || rootFitState.current) mounted.getCamera().setState(previousCamera ?? rootFitState.current!);
        mounted.on("clickNode", ({ node }) => {
          const entity = display.entityById.get(node);
          const group = display.groupById.get(node);
          if (entity) selectEntity.current(entity.id);
          if (group) toggleGroup.current(group.id);
        });
        mounted.on("clickEdge", ({ edge }) => {
          const relation = display.relationById.get(edge);
          if (relation) selectEntity.current(relation.object_id);
        });
        mounted.on("enterNode", ({ node }) => {
          const entity = display.entityById.get(node);
          const group = display.groupById.get(node);
          setHovered(entity ? `${entity.label} · ${entity.class_label || "类型尚未对齐"} · ${HARNESS_STATES[entity.state]}`
            : group ? `${group.label} · ${group.relations.length} 项 · 点击${group.open ? "收起" : "展开"}` : "");
        });
        mounted.on("leaveNode", () => setHovered(""));
        mounted.on("enterEdge", ({ edge }) => {
          const relation = display.relationById.get(edge);
          if (relation) setHovered(`${relation.label} · ${HARNESS_STATES[relation.state]}${relationQualifier(relation) ? ` · ${relationQualifier(relation)}` : ""}`);
        });
        mounted.on("leaveEdge", () => setHovered(""));
      }).catch(() => { if (!cancelled) setRenderError("关系图布局或渲染失败，请刷新页面重试。"); });
    return () => {
      cancelled = true;
      if (mounted) {
        cameraState.current = mounted.getCamera().getState();
        mounted.kill();
        if (renderer.current === mounted) renderer.current = null;
      }
    };
  }, [display]);

  useEffect(() => { renderer.current?.refresh(); }, [subject?.id]);

  return <div className="min-w-0 overflow-hidden rounded-lg border bg-muted/10">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-card px-4 py-2">
      <p className="text-xs text-muted-foreground">{display.rootId ? "以文档根为圆心" : "当前视图无文档根，按圆形排列"} · 默认展开全部关系 · 拖动平移、滚轮缩放，点击实体查看属性，点击紫色谓词节点收起关系</p>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" aria-label="缩小关系图" onClick={() => void renderer.current?.getCamera().animatedUnzoom()}>−</Button>
        <Button size="sm" variant="ghost" aria-label="放大关系图" onClick={() => void renderer.current?.getCamera().animatedZoom()}>+</Button>
        <Button size="sm" variant="outline" onClick={() => {
          const camera = renderer.current?.getCamera();
          if (rootFitState.current) void camera?.animate(rootFitState.current);
          else void camera?.animatedReset();
        }}>适应画布</Button>
      </div>
    </div>
    {!display.entities.length ? <p className="p-8 text-center text-sm text-muted-foreground">当前筛选下暂无可展示的实体。</p>
      : <div ref={container} role="img" aria-label="Sigma.js 实体关系图" className="relative h-[560px] w-full overflow-hidden bg-card" />}
    {renderError && <p role="alert" className="border-t p-3 text-sm text-destructive">{renderError}</p>}
    {hovered && <p role="status" className="border-t px-3 py-2 text-xs">{hovered}</p>}
    {display.entities.length > 0 && <details className="border-t bg-card p-3 text-xs">
      <summary className="cursor-pointer">键盘操作与图中节点（{display.entities.length} 个实体、{display.groups.length} 个谓词分组）</summary>
      <div className="mt-3 flex max-h-36 flex-wrap gap-2 overflow-auto" aria-label="图中节点列表">
        {display.groups.map((group) => <button key={group.id} type="button"
          data-predicate-iri={group.predicateIri} data-subject-id={group.subjectId}
          aria-expanded={group.open} onClick={() => toggleGroup.current(group.id)}
          className="rounded border px-2 py-1 text-left text-violet-700 hover:bg-muted dark:text-violet-300">
          {group.open ? "收起" : "展开"}{group.label}（{group.relations.length}）
        </button>)}
        {display.entities.map((entity) => <button key={entity.id} type="button"
          data-entity-id={entity.id} aria-pressed={entity.id === subject?.id}
          onClick={() => onSelectEntity(entity.id)}
          className="rounded border px-2 py-1 text-left hover:bg-muted">
          {entity.label} · {HARNESS_STATES[entity.state]}
        </button>)}
      </div>
      <div className="mt-3 flex max-h-28 flex-wrap gap-2 overflow-auto" aria-label="图中关系列表">
        {display.edges.filter((edge) => edge.id.startsWith("relation:")).map((edge) => {
          const relation = display.relationById.get(edge.id)!;
          return <span key={edge.id} data-relation-id={edge.id} data-state={relation.state}
            data-polarity={relation.polarity} className="rounded border px-2 py-1"
            style={{ color: relationColor(relation) }}>
            {relation.label} · {HARNESS_STATES[relation.state]}{relationQualifier(relation) && ` · ${relationQualifier(relation)}`}
          </span>;
        })}
      </div>
    </details>}
  </div>;
}
