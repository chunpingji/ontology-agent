"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getClassHierarchy,
  getModules,
  getTBoxClass,
  type Module,
  type TBoxClass,
  type TreeNode,
} from "@/lib/api";
import { useVersionConflict } from "@/components/ontology/use-version-conflict";
import { ConflictDialog } from "@/components/ontology/conflict-dialog";
import { ClassPanel } from "@/components/ontology/class-panel";
import { LinkTypePanel } from "@/components/ontology/link-type-panel";
import { DataPropertyPanel } from "@/components/ontology/data-property-panel";
import { ActionPanel } from "@/components/ontology/action-panel";
import { RestrictionEditor } from "@/components/ontology/restriction-editor";
import { OntologyMappingPanel } from "@/components/ontology/ontology-mapping-panel";
import { TtlToolbar } from "@/components/ontology/ttl-toolbar";
import { GraphVisualization } from "@/components/ontology/graph-visualization";

const TABS = ["基本", "关系", "属性", "映射", "操作"] as const;
type Tab = (typeof TABS)[number];

function TreeItem({
  node,
  depth = 0,
  selectedIri,
  onSelect,
}: {
  node: TreeNode;
  depth?: number;
  selectedIri: string | null;
  onSelect: (iri: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = node.children.length > 0;
  return (
    <div>
      <button
        onClick={() => {
          onSelect(node.iri);
          if (hasChildren) setExpanded(!expanded);
        }}
        className={`flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm hover:bg-blue-50 ${
          selectedIri === node.iri ? "bg-blue-100" : ""
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {hasChildren && <span className="text-xs text-gray-400">{expanded ? "▼" : "▶"}</span>}
        <span className="font-mono text-xs text-gray-500">{node.name}</span>
        {node.label && <span className="ml-1 text-gray-700">{node.label}</span>}
      </button>
      {expanded &&
        node.children.map((child) => (
          <TreeItem
            key={child.iri}
            node={child}
            depth={depth + 1}
            selectedIri={selectedIri}
            onSelect={onSelect}
          />
        ))}
    </div>
  );
}

const flatten = (nodes: TreeNode[]): string[] =>
  nodes.flatMap((n) => [n.iri, ...flatten(n.children)]);

/**
 * T-Box 知识模型维护工作台（能力一，T040）。
 * 只读浏览器 → 可编辑工作台壳：装配类 / 关系 / 属性 / 约束 / 映射 / 操作面板、
 * TTL 工具条与图谱，并通过 {@link useVersionConflict} 统一处理乐观并发冲突
 * （FR-011 / FR-011a，AS-1 / AS-2）。
 */
export default function OntologyWorkbenchPage() {
  const [modules, setModules] = useState<Module[]>([]);
  const [selectedModule, setSelectedModule] = useState<string>("drug");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [selectedIri, setSelectedIri] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("基本");
  const [classes, setClasses] = useState<TBoxClass[]>([]);
  const conflict = useVersionConflict();

  useEffect(() => {
    getModules().then(setModules).catch(() => {});
  }, []);

  const loadGraph = useCallback((nodes: TreeNode[]) => {
    const iris = flatten(nodes);
    Promise.allSettled(iris.map((iri) => getTBoxClass(iri))).then((results) => {
      setClasses(
        results
          .filter((r): r is PromiseFulfilledResult<TBoxClass> => r.status === "fulfilled")
          .map((r) => r.value),
      );
    });
  }, []);

  const loadTree = useCallback(
    (module: string) => {
      getClassHierarchy(module)
        .then((t) => {
          setTree(t);
          loadGraph(t);
        })
        .catch(() => {
          setTree([]);
          setClasses([]);
        });
    },
    [loadGraph],
  );

  useEffect(() => {
    if (selectedModule) loadTree(selectedModule);
  }, [selectedModule, loadTree]);

  // 任一面板写入后回调：刷新树 / 图谱并定位到（可能新建的）类。
  const handleChanged = useCallback(
    (iri?: string) => {
      if (iri !== undefined) setSelectedIri(iri || null);
      loadTree(selectedModule);
    },
    [selectedModule, loadTree],
  );

  const handleReloadAfterConflict = () => {
    conflict.clear();
    handleChanged(selectedIri ?? undefined);
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">T-Box 知识模型维护工作台</h1>
        <button
          onClick={() => {
            setSelectedIri(null);
            setTab("基本");
          }}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
        >
          + 新建类
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {modules.map((m) => (
          <button
            key={m.key}
            onClick={() => setSelectedModule(m.key)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              selectedModule === m.key
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            }`}
          >
            {m.label || m.key}
            <span className="ml-1 text-xs opacity-70">({m.class_count})</span>
          </button>
        ))}
      </div>

      <div className="flex gap-4">
        {/* 左：类层次 */}
        <div className="w-72 shrink-0 rounded-lg border bg-white p-3">
          <h2 className="mb-2 text-sm font-semibold text-gray-500">类层次</h2>
          {tree.map((node) => (
            <TreeItem
              key={node.iri}
              node={node}
              selectedIri={selectedIri}
              onSelect={(iri) => setSelectedIri(iri)}
            />
          ))}
          {tree.length === 0 && <p className="text-sm text-gray-400">加载中…</p>}
        </div>

        {/* 中：编辑面板（分页签） */}
        <div className="flex-1 space-y-4">
          <div className="rounded-lg border bg-white">
            <div className="flex gap-1 border-b px-2 pt-2">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded-t px-3 py-1.5 text-sm ${
                    tab === t
                      ? "border-x border-t bg-white font-semibold text-blue-700"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            <div className="p-4">
              {tab === "基本" && (
                <ClassPanel key={selectedIri ?? "new"} iri={selectedIri} conflict={conflict} onChanged={handleChanged} />
              )}
              {tab === "关系" && (
                <div className="space-y-6">
                  <LinkTypePanel selectedClassIri={selectedIri} onChanged={() => handleChanged(selectedIri ?? undefined)} />
                  <RestrictionEditor key={selectedIri ?? "none"} classIri={selectedIri} conflict={conflict} onChanged={() => handleChanged(selectedIri ?? undefined)} />
                </div>
              )}
              {tab === "属性" && (
                <DataPropertyPanel selectedClassIri={selectedIri} onChanged={() => handleChanged(selectedIri ?? undefined)} />
              )}
              {tab === "映射" && (
                <OntologyMappingPanel key={selectedIri ?? "none"} classIri={selectedIri} conflict={conflict} onChanged={() => handleChanged(selectedIri ?? undefined)} />
              )}
              {tab === "操作" && <ActionPanel selectedClassIri={selectedIri} />}
            </div>
          </div>

          <TtlToolbar onPublished={() => handleChanged(selectedIri ?? undefined)} />
        </div>

        {/* 右：图谱 */}
        <div className="w-[42%] shrink-0">
          <GraphVisualization classes={classes} />
        </div>
      </div>

      <ConflictDialog
        conflict={conflict.conflict}
        onReload={handleReloadAfterConflict}
        onDismiss={conflict.clear}
      />
    </div>
  );
}
