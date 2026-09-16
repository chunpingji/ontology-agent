"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Search, X } from "lucide-react";
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
import { Input } from "@/components/ui/input";
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

const classNameCollator = new Intl.Collator("en", { sensitivity: "base" });

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
  const [searchQuery, setSearchQuery] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "partial" | "error">("loading");
  const loadRequest = useRef(0);
  const normalizedSearch = searchQuery.trim().toLowerCase();
  const [selectedIri, setSelectedIri] = useState<string | null>(null);
  // 图谱关系边 ↔ 关系面板双向联动：当前聚焦的 link type slpra_iri。
  const [focusedLinkIri, setFocusedLinkIri] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("基本");
  const [classes, setClasses] = useState<TBoxClass[]>([]);
  const [linkTypes, setLinkTypes] = useState<TBoxLinkType[]>([]);
  const [restrictionReload, setRestrictionReload] = useState(0);
  const conflict = useVersionConflict();

  // 适配 mrlightful TreeView:TreeNode → TreeDataItem,并保留 iri→原始节点 映射,
  // 供 renderItem 同时取中文 label(主标)与英文类名(副标)。
  const { treeData, nodeByIri, matchedCount } = useMemo(() => {
    const nodeByIri = new Map<string, TreeNode>();
    const classByIri = new Map(classes.map((cls) => [cls.slpra_iri, cls]));
    const matchedIris = new Set<string>();
    const conv = (ns: TreeNode[]): TreeDataItem[] =>
      // 每层先排有子类的节点，再按英文类名排序；复制数组，保留原始层次数据。
      [...ns].sort((a, b) =>
        Number(b.children.length > 0) - Number(a.children.length > 0)
        || classNameCollator.compare(a.name || a.iri, b.name || b.iri),
      ).flatMap((n) => {
        nodeByIri.set(n.iri, n);
        const cls = classByIri.get(n.iri);
        const children = conv(n.children);
        const matches = !normalizedSearch || [n.label, cls?.label, cls?.comment, n.iri]
          .some((value) => value?.toLowerCase().includes(normalizedSearch));
        if (matches) matchedIris.add(n.iri);
        if (!matches && !children.length) return [];
        return [{
          id: n.iri,
          name: n.label || n.name,
          children: children.length ? children : undefined,
          className: normalizedSearch && matches ? "text-primary" : undefined,
        }];
      });
    const treeData = conv(tree);
    return { treeData, nodeByIri, matchedCount: matchedIris.size };
  }, [tree, classes, normalizedSearch]);

  useEffect(() => {
    getModules().then(setModules).catch(() => {});
  }, []);

  const loadTree = useCallback((module: string) => {
    const request = ++loadRequest.current;
    return getClassHierarchy(module).then(async (nodes) => {
      if (request !== loadRequest.current) return;
      setTree(nodes);
      // 复用图谱加载的类详情供注释搜索；同一 IRI 只读取一次。
      const iris = [...new Set(flatten(nodes))];
      const [results, lts] = await Promise.all([
        Promise.allSettled(iris.map((iri) => getTBoxClass(iri))),
        listLinkTypes().catch(() => [] as TBoxLinkType[]),
      ]);
      if (request !== loadRequest.current) return;
      setClasses(
        results
          .filter((r): r is PromiseFulfilledResult<TBoxClass> => r.status === "fulfilled")
          .map((r) => r.value),
      );
      setLinkTypes(lts);
      setLoadState(results.some((r) => r.status === "rejected") ? "partial" : "ready");
    }).catch(() => {
      if (request !== loadRequest.current) return;
      setTree([]);
      setClasses([]);
      setLinkTypes([]);
      setLoadState("error");
    });
  }, []);

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
      setLoadState("loading");
      loadTree(selectedModule);
    },
    [selectedModule, loadTree],
  );

  const handleReloadAfterConflict = () => {
    conflict.clear();
    setRestrictionReload((version) => version + 1);
    handleChanged(selectedIri ?? undefined);
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">T-Box 知识模型维护工作台</h1>
        <div className="flex items-center gap-2">
          <Link
            href="/ontology/rules"
            className="rounded border px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent"
          >
            声明式规则 →
          </Link>
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
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {modules.map((m) => (
          <Button
            key={m.key}
            onClick={() => {
              if (m.key !== selectedModule) {
                setLoadState("loading");
                setSelectedModule(m.key);
              }
            }}
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
          <div className="relative mb-2">
            <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              type="search"
              aria-label="搜索本体类"
              aria-describedby="ontology-search-status"
              placeholder="搜索标签、注释、IRI"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="pl-8 pr-8 [&::-webkit-search-cancel-button]:appearance-none"
            />
            {searchQuery && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0.5 top-0.5 h-8 w-8"
                aria-label="清空搜索"
                onClick={() => setSearchQuery("")}
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </Button>
            )}
          </div>
          <p id="ontology-search-status" role="status" className="mb-2 text-xs text-muted-foreground">
            {loadState === "loading" ? "正在加载当前模块…" : loadState === "error" ? "当前模块加载失败" : (
              normalizedSearch ? `当前模块匹配 ${matchedCount} 个类` : "在当前模块内搜索"
            )}
          </p>
          {loadState === "partial" && (
            <p className="mb-2 text-xs text-destructive">部分注释未加载，搜索结果可能不完整。</p>
          )}
          {loadState === "loading" ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : loadState === "error" ? (
            <Button variant="outline" size="sm" onClick={() => handleChanged()}>重新加载</Button>
          ) : tree.length === 0 ? (
            <p className="text-sm text-muted-foreground">当前模块暂无类</p>
          ) : treeData.length === 0 ? (
            <p className="text-sm text-muted-foreground">未找到匹配的类</p>
          ) : (
            <div className="max-h-[70vh] overflow-y-auto">
            <TreeView
              key={`${selectedModule}:${normalizedSearch}`}
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
                  {selectedIri && (
                    <div className="mt-6 border-t pt-4">
                      <RestrictionEditor
                        key={`axioms-${selectedIri}-${restrictionReload}`}
                        classIri={selectedIri}
                        binding={null}
                        conflict={conflict}
                        onChanged={() => handleChanged(selectedIri)}
                      />
                    </div>
                  )}
                </TabsContent>
                <TabsContent value="关系" className="mt-0">
                  <LinkTypePanel
                    key={`link-${selectedIri ?? "none"}`}
                    selectedClassIri={selectedIri}
                    focusedLinkIri={focusedLinkIri}
                    onFocusLink={setFocusedLinkIri}
                    onChanged={() => handleChanged(selectedIri ?? undefined)}
                    renderRestrictions={selectedIri ? (link) => (
                      <RestrictionEditor
                        key={`restr-${selectedIri}-${link.slpra_iri}-${link.version}-${link.range_iri}-${restrictionReload}`}
                        classIri={selectedIri}
                        binding={{ propertyIri: link.slpra_iri, propertyKind: "object", defaultFillerIri: link.range_iri }}
                        conflict={conflict}
                        onChanged={() => handleChanged(selectedIri)}
                      />
                    ) : undefined}
                  />
                </TabsContent>
                <TabsContent value="属性" className="mt-0">
                  <DataPropertyPanel
                    key={selectedIri ?? "none"}
                    selectedClassIri={selectedIri}
                    onChanged={() => handleChanged(selectedIri ?? undefined)}
                    renderRestrictions={selectedIri ? (property) => (
                      <RestrictionEditor
                        key={`restr-${selectedIri}-${property.slpra_iri}-${property.version}-${restrictionReload}`}
                        classIri={selectedIri}
                        binding={{ propertyIri: property.slpra_iri, propertyKind: "data", defaultFillerIri: null }}
                        conflict={conflict}
                        onChanged={() => handleChanged(selectedIri)}
                      />
                    ) : undefined}
                  />
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
