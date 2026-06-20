"use client";

import { useEffect, useState } from "react";
import { getModules, getClassHierarchy, getClassDetail } from "@/lib/api";
import type { Module, TreeNode, ClassDetail } from "@/lib/api";

function TreeItem({
  node,
  depth = 0,
  onSelect,
}: {
  node: TreeNode;
  depth?: number;
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
        className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm hover:bg-blue-50"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {hasChildren && (
          <span className="text-xs text-gray-400">{expanded ? "▼" : "▶"}</span>
        )}
        <span className="font-mono text-xs text-gray-500">{node.name}</span>
        {node.label && <span className="ml-1 text-gray-700">{node.label}</span>}
        {node.individual_count > 0 && (
          <span className="ml-auto rounded bg-gray-100 px-1.5 text-xs text-gray-500">
            {node.individual_count}
          </span>
        )}
      </button>
      {expanded &&
        node.children.map((child) => (
          <TreeItem key={child.iri} node={child} depth={depth + 1} onSelect={onSelect} />
        ))}
    </div>
  );
}

export default function OntologyPage() {
  const [modules, setModules] = useState<Module[]>([]);
  const [selectedModule, setSelectedModule] = useState<string>("drug");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [detail, setDetail] = useState<ClassDetail | null>(null);

  useEffect(() => {
    getModules().then(setModules).catch(console.error);
  }, []);

  useEffect(() => {
    if (selectedModule) {
      getClassHierarchy(selectedModule).then(setTree).catch(() => setTree([]));
    }
  }, [selectedModule]);

  const handleClassSelect = (iri: string) => {
    getClassDetail(iri).then(setDetail).catch(console.error);
  };

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">本体编辑器</h1>

      <div className="mb-4 flex gap-2">
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
        <div className="w-80 shrink-0 rounded-lg border bg-white p-3">
          <h2 className="mb-2 text-sm font-semibold text-gray-500">类层次</h2>
          {tree.map((node) => (
            <TreeItem key={node.iri} node={node} onSelect={handleClassSelect} />
          ))}
          {tree.length === 0 && (
            <p className="text-sm text-gray-400">加载中...</p>
          )}
        </div>

        <div className="flex-1 rounded-lg border bg-white p-5">
          {detail ? (
            <div>
              <h2 className="mb-1 text-lg font-bold">{detail.name}</h2>
              <p className="mb-3 text-sm text-gray-500">{detail.iri}</p>
              {detail.label_zh && (
                <p className="mb-1 text-sm">中文: {detail.label_zh}</p>
              )}
              {detail.label_en && (
                <p className="mb-1 text-sm">English: {detail.label_en}</p>
              )}
              {detail.comment && (
                <p className="mb-3 text-sm text-gray-600">{detail.comment}</p>
              )}

              {detail.parent_iris.length > 0 && (
                <div className="mb-3">
                  <h3 className="text-sm font-semibold text-gray-500">父类</h3>
                  {detail.parent_iris.map((p) => (
                    <span key={p} className="mr-2 inline-block rounded bg-gray-100 px-2 py-0.5 text-xs">
                      {p.split("/").pop()}
                    </span>
                  ))}
                </div>
              )}

              {detail.object_properties.length > 0 && (
                <div className="mb-3">
                  <h3 className="mb-1 text-sm font-semibold text-gray-500">对象属性</h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-xs text-gray-400">
                        <th className="pb-1">属性名</th>
                        <th className="pb-1">值域</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.object_properties.map((p) => (
                        <tr key={p.iri} className="border-b last:border-0">
                          <td className="py-1 font-mono text-xs">{p.name}</td>
                          <td className="py-1 text-xs text-gray-500">
                            {p.range.map((r) => r.split("/").pop()).join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {detail.data_properties.length > 0 && (
                <div className="mb-3">
                  <h3 className="mb-1 text-sm font-semibold text-gray-500">数据属性</h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-xs text-gray-400">
                        <th className="pb-1">属性名</th>
                        <th className="pb-1">类型</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.data_properties.map((p) => (
                        <tr key={p.iri} className="border-b last:border-0">
                          <td className="py-1 font-mono text-xs">{p.name}</td>
                          <td className="py-1 text-xs text-gray-500">
                            {p.range.join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <p className="mt-2 text-xs text-gray-400">
                个体数: {detail.individual_count}
              </p>
            </div>
          ) : (
            <p className="text-gray-400">选择左侧类查看详情</p>
          )}
        </div>
      </div>
    </div>
  );
}
