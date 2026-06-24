"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getClassHierarchy,
  getModules,
  getTBoxClass,
  listLinkTypes,
  type Module,
  type TBoxClass,
  type TBoxLinkType,
  type TreeNode,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import { TreeView, type TreeDataItem } from "@/components/tree-view";

const TABS = ["基本", "关系", "属性", "映射", "操作"] as const;
type Tab = (typeof TABS)[number];

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
  // 图谱关系边 ↔ 关系面板双向联动：当前聚焦的 link type slpra_iri。
  const [focusedLinkIri, setFocusedLinkIri] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("基本");
  const [classes, setClasses] = useState<TBoxClass[]>([]);
  const [linkTypes, setLinkTypes] = useState<TBoxLinkType[]>([]);
  const conflict = useVersionConflict();

  // 适配 mrlightful TreeView:TreeNode → TreeDataItem,并保留 iri→原始节点 映射,
  // 供 renderItem 同时取中文 label(主标)与英文类名(副标)。
  const { treeData, nodeByIri } = useMemo(() => {
    const nodeByIri = new Map<string, TreeNode>();
    const conv = (ns: TreeNode[]): TreeDataItem[] =>
      ns.map((n) => {
        nodeByIri.set(n.iri, n);
        return {
          id: n.iri,
          name: n.label || n.name,
          children: n.children.length ? conv(n.children) : undefined,
        };
      });
    return { treeData: conv(tree), nodeByIri };
  }, [tree]);

  useEffect(() => {
    getModules().then(setModules).catch(() => {});
  }, []);

  const loadGraph = useCallback((nodes: TreeNode[]) => {
    const iris = flatten(nodes);
    // 同时取类详情与全部关系；两个 setState 合并到一次回调 → 单次渲染 → 图只重建一次。
    // 关系在图里再按「模块内」过滤（domain/range 都在已加载类集合里）。
    Promise.all([
      Promise.allSettled(iris.map((iri) => getTBoxClass(iri))),
      listLinkTypes().catch(() => [] as TBoxLinkType[]),
    ]).then(([results, lts]) => {
      setClasses(
        results
          .filter((r): r is PromiseFulfilledResult<TBoxClass> => r.status === "fulfilled")
          .map((r) => r.value),
      );
      setLinkTypes(lts);
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
          setLinkTypes([]);
        });
    },
    [loadGraph],
  );

  useEffect(() => {
    if (selectedModule) loadTree(selectedModule);
  }, [selectedModule, loadTree]);

  // 选中类（来自左树或点击图节点）：换类即清掉关系聚焦（旧关系不属于新类）。
  const selectNode = useCallback((iri: string | null) => {
    setSelectedIri(iri);
    setFocusedLinkIri(null);
  }, []);

  // 点击图谱中的关系边：选中其源（domain）类、切到「关系」页签并高亮该关系行。
  const selectLink = useCallback((linkTypeIri: string, domainIri: string) => {
    setSelectedIri(domainIri);
    setTab("关系");
    setFocusedLinkIri(linkTypeIri);
  }, []);

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
        <Button
          onClick={() => {
            selectNode(null);
            setTab("基本");
          }}
          size="sm"
          className="h-auto px-3 py-1.5 text-sm"
        >
          + 新建类
        </Button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {modules.map((m) => (
          <Button
            key={m.key}
            onClick={() => setSelectedModule(m.key)}
            variant={selectedModule === m.key ? "default" : "secondary"}
            size="sm"
            className={`h-auto px-3 py-1.5 text-sm ${
              selectedModule === m.key
                ? ""
                : "hover:bg-secondary/70"
            }`}
          >
            {m.label || m.key}
            <span className="ml-1 text-xs opacity-70">({m.class_count})</span>
          </Button>
        ))}
      </div>

      <div className="flex gap-4">
        {/* 左：类层次 */}
        <Card className="w-72 shrink-0 rounded-lg p-3 shadow-none">
          <h2 className="mb-2 text-sm font-semibold text-muted-foreground">类层次</h2>
          {tree.length === 0 ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : (
            <div className="max-h-[70vh] overflow-y-auto">
            <TreeView
              key={selectedModule}
              data={treeData}
              initialSelectedItemId={selectedIri ?? undefined}
              onSelectChange={(item) => item && selectNode(item.id)}
              expandAll
              className="p-0"
              renderItem={({ item }) => {
                const node = nodeByIri.get(item.id);
                const label = node?.label;
                return (
                  <span className="flex-grow truncate text-left">
                    <span className="text-sm">{label || item.name}</span>
                    {label && node?.name && (
                      <span className="ml-1.5 font-mono text-xs text-muted-foreground">{node.name}</span>
                    )}
                  </span>
                );
              }}
            />
            </div>
          )}
        </Card>

        {/* 中：编辑面板（分页签） */}
        <div className="flex-1 space-y-4">
          <Card className="rounded-lg shadow-none">
            <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
              <div className="border-b border-border px-2 pt-2">
                <TabsList className="h-auto gap-1 bg-transparent p-0">
                  {TABS.map((t) => (
                    <TabsTrigger
                      key={t}
                      value={t}
                      className="rounded-b-none rounded-t border border-transparent px-3 py-1.5 text-sm text-muted-foreground shadow-none hover:text-foreground data-[state=active]:border-x data-[state=active]:border-t data-[state=active]:border-border data-[state=active]:bg-card data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
                    >
                      {t}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>
              <div className="p-4">
                <TabsContent value="基本" className="mt-0">
                  <ClassPanel key={selectedIri ?? "new"} iri={selectedIri} conflict={conflict} onChanged={handleChanged} />
                </TabsContent>
                <TabsContent value="关系" className="mt-0">
                  <div className="space-y-6">
                    <LinkTypePanel
                      key={selectedIri ?? "none"}
                      selectedClassIri={selectedIri}
                      focusedLinkIri={focusedLinkIri}
                      onFocusLink={setFocusedLinkIri}
                      onChanged={() => handleChanged(selectedIri ?? undefined)}
                    />
                    <RestrictionEditor key={selectedIri ?? "none"} classIri={selectedIri} conflict={conflict} onChanged={() => handleChanged(selectedIri ?? undefined)} />
                  </div>
                </TabsContent>
                <TabsContent value="属性" className="mt-0">
                  <DataPropertyPanel key={selectedIri ?? "none"} selectedClassIri={selectedIri} onChanged={() => handleChanged(selectedIri ?? undefined)} />
                </TabsContent>
                <TabsContent value="映射" className="mt-0">
                  <OntologyMappingPanel key={selectedIri ?? "none"} classIri={selectedIri} conflict={conflict} onChanged={() => handleChanged(selectedIri ?? undefined)} />
                </TabsContent>
                <TabsContent value="操作" className="mt-0">
                  <ActionPanel selectedClassIri={selectedIri} />
                </TabsContent>
              </div>
            </Tabs>
          </Card>

          <TtlToolbar onPublished={() => handleChanged(selectedIri ?? undefined)} />
        </div>

        {/* 右：图谱（与左树 / 中部面板双向联动） */}
        <div className="w-[42%] shrink-0">
          <GraphVisualization
            classes={classes}
            linkTypes={linkTypes}
            selectedIri={selectedIri}
            focusedLinkIri={focusedLinkIri}
            onSelectNode={selectNode}
            onSelectLink={selectLink}
          />
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
