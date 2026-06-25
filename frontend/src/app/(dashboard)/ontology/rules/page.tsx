"use client";

import { useState } from "react";
import Link from "next/link";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { ClassificationCriteriaPanel } from "@/components/ontology/classification-criteria-panel";
import { DecisionRulesPanel } from "@/components/ontology/decision-rules-panel";
import { ConflictPoliciesPanel } from "@/components/ontology/conflict-policies-panel";

const TABS = ["分类判据", "决策规则", "冲突策略"] as const;
type Tab = (typeof TABS)[number];

/**
 * 声明式规则维护入口（能力六 / spec 006，US3 / T042）。
 *
 * 把 R-ED/R-SC/R-CP 决策规则、分类判据与冲突策略作为**可版本化数据**对外暴露：
 * 改阈值 / 加规则 / 换策略全程零源码改动，落草稿即参与推断，进入发布批次并留审计
 * （FR-016）。刻意只提供受限模式编辑器（解释器词汇内），不是通用类表达式编辑器。
 */
export default function DeclarativeRulesPage() {
  const [tab, setTab] = useState<Tab>("分类判据");

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">声明式规则维护</h1>
          <p className="text-xs text-muted-foreground">
            规则知识即可版本化数据 — 改阈值 / 加规则 / 换策略零源码改动，可审计、入发布批次（FR-016）
          </p>
        </div>
        <Link
          href="/ontology"
          className="rounded border px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent"
        >
          ← 返回 T-Box 工作台
        </Link>
      </div>

      <Card className="rounded-lg p-4 shadow-none">
        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
          <TabsList className="mb-4">
            {TABS.map((t) => (
              <TabsTrigger key={t} value={t} className="text-sm">
                {t}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="分类判据">
            <ClassificationCriteriaPanel />
          </TabsContent>
          <TabsContent value="决策规则">
            <DecisionRulesPanel />
          </TabsContent>
          <TabsContent value="冲突策略">
            <ConflictPoliciesPanel />
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
