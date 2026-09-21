# 90 个源记录的当前识别与关系核验范围

运行 `20bf2d25-98f3-4efe-99f0-1e5dbbae5d05`；采样 `2026-09-21T05:48:59.262638+00:00`；当时状态 `paused`，work_version=51。这是只读快照，未暂停或修改运行。

逐项 JSON：[对应数据](final-scope-review-schema-boundary-37.json)。脚本和捕获原件位于 `/tmp/ontology-target-main-20260921/`。

| 范围 | 数量/状态 |
|---|---|
| 源记录 / 读取组 | 90 / 43 |
| 记录路由 | {"routed": 72, "unrouted": 18} |
| 具有已确认识别调用的记录 | 41 |
| 发现工作状态 | {"incomplete": 41, "unattempted": 49} |
| 当前任务范围状态 | {"incomplete": 41, "unattempted": 49} |
| 唯一确认调用 | 37；{"discovery": 20, "verification": 17} |
| 未确认请求 | 0 |
| 无法对应源记录的确认调用 | 0 |

“已路由”“有确认调用”“发现 examined”“当前关系计划完成”分别展示。确认调用按任务明确输入范围关联，不代表每条内容均被理解；verification 调用也不代表核验通过。当前范围 examined 只表示发现及当前已建关系任务已结束，不是所有本体关系/属性均完整。

未路由、未调用、等待端点、技术未完成及明确阴性互不替代。一次组调用会关联多条记录，下表调用数不能相加作为全局调用数。

| 原文 | 阅读组（0基） | 路由 | 确认调用：发现/核验 | 发现状态 | 当前范围状态 | 当前关系执行 |
|---|---|---|---|---|---|---|
| P8 | 0 | routed | 2/1 | incomplete | incomplete | no_current_relation_plan |
| P20 | 0 | routed | 2/1 | incomplete | incomplete | no_current_relation_plan |
| P26 | 1 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called；waiting_endpoints×1 |
| P27 | 1 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P31 | 2 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P32 | 2 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P33 | 2 | routed | 2/2 | incomplete | incomplete | incomplete；incomplete×1 |
| P34 | 2 | routed | 2/2 | incomplete | incomplete | incomplete；incomplete×1 |
| P35 | 2 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P36 | 2 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P37 | 2 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P38 | 2 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P39 | 3 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P40 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P41 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P42 | 3 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| P43 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P44 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P45 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P46 | 3 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P47 | 4 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P48 | 4 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P49 | 4 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P53 | 5 | routed | 1/2 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| P66 | 5 | routed | 1/2 | incomplete | incomplete | planned_not_model_called |
| T1R1 | 6 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T1R2 | 6 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T1R3 | 6 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T1R4 | 7 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T2R2 | 8 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T3R2 | 9 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P74 | 10 | routed | 1/0 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| T4R2 | 11 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P77 | 12 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T5R2 | 13 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P80 | 14 | routed | 1/1 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| T6R2 | 15 | routed | 0/0 | unattempted | unattempted | planned_not_model_called；waiting_endpoints×1 |
| T6R3 | 16 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T6R4 | 17 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T6R5 | 18 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T7R2 | 19 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T7R3 | 20 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T7R4 | 21 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T7R5 | 22 | routed | 1/1 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| T7R6 | 23 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R2 | 24 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R3 | 24 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R4 | 24 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R5 | 24 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R6 | 25 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R7 | 25 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R8 | 25 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R9 | 25 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T8R10 | 26 | unrouted | 0/0 | unattempted | unattempted | planned_not_model_called |
| T9R1 | 27 | routed | 1/1 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×2 |
| T9R2 | 27 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| T9R3 | 27 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| T9R4 | 27 | routed | 1/1 | incomplete | incomplete | planned_not_model_called |
| T9R5 | 28 | routed | 1/0 | incomplete | incomplete | planned_not_model_called |
| T9R6 | 28 | routed | 1/0 | incomplete | incomplete | planned_not_model_called |
| T9R7 | 28 | routed | 1/0 | incomplete | incomplete | planned_not_model_called |
| T9R8 | 28 | routed | 1/0 | incomplete | incomplete | planned_not_model_called |
| T9R9 | 29 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P88 | 30 | routed | 2/1 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| P90 | 31 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T10R2 | 32 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T10R3 | 33 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P96 | 34 | routed | 2/4 | incomplete | incomplete | incomplete；incomplete×1 |
| P97 | 34 | routed | 1/2 | incomplete | incomplete | planned_not_model_called |
| P100 | 35 | unrouted | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P102 | 36 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| T11R2 | 37 | routed | 2/1 | incomplete | incomplete | planned_not_model_called；waiting_endpoints×1 |
| T11R3 | 38 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P103 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P104 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P105 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P106 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P107 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P108 | 39 | routed | 0/0 | unattempted | unattempted | no_current_relation_plan |
| P109 | 39 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P110 | 39 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P111 | 40 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P112 | 40 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P113 | 40 | routed | 0/0 | unattempted | unattempted | planned_not_model_called |
| P116 | 41 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| T12R2 | 41 | routed | 1/1 | incomplete | incomplete | no_current_relation_plan |
| P117 | 42 | routed | 1/0 | incomplete | incomplete | no_current_relation_plan |
| P122 | 42 | routed | 1/0 | incomplete | incomplete | no_current_relation_plan |
| P123 | 42 | routed | 1/0 | incomplete | incomplete | no_current_relation_plan |
| P124 | 42 | routed | 1/0 | incomplete | incomplete | no_current_relation_plan |

逐项主体、谓词、计划覆盖状态、执行状态、任务状态、语义结果和确认调用 ID 见 JSON 的 relation_plans / relation_tasks。未创建关系计划保留为 no_current_relation_plan，不按已完成或阴性处理。

复用命令（重新捕获当前范围，不调用模型）：

```bash
python /tmp/ontology-target-main-20260921/final-scope-review.py
```

主任务明确要求冻结时，可执行 `--freeze <新标签>`；该选项只创建新文件，已有同名文件会拒绝覆盖，不改变应用运行状态。
