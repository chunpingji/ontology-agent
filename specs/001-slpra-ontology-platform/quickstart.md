# Quickstart: 能力一 T-Box 维护工作台 — US1 端到端验证

**Feature**: 001-slpra-ontology-platform | **Date**: 2026-06-20 | **Phase**: 1

**Input**: [plan.md](./plan.md) · [data-model.md](./data-model.md) · [contracts/ontology-tbox-api.md](./contracts/ontology-tbox-api.md)

> 本指南验证 **US1（高级分析师维护知识模型）** 的端到端闭环：新建类 → 映射 → 约束 → 校验 → 批次发布 → TTL diff 回写。聚焦"可运行的验证场景"，不含实现代码（实现细节属 `tasks.md` 与实现阶段）。

## 1. 前置条件

- Docker / docker-compose 可用；仓库根存在 `docker-compose.yml`（db / backend / frontend 三服务）。
- 权威 TTL 在 `ontology/slpra/*.ttl`（7 模块、~720 公理）。
- 后端已接线 Alembic（R6）：启动时 `alembic upgrade head` 建元数据表，并由 TTL **投影补种**（幂等）。
- 新增后端依赖 `rdflib` 已加入 `backend/pyproject.toml`。
- 身份：经网关注入 `X-User` / `X-Role`；本验证以 `X-Role: senior_analyst` 调用写端点（R7）。

## 2. 启动

```bash
docker compose up -d db
docker compose up -d backend     # 启动即应用迁移 + TTL 投影补种
docker compose up -d frontend
# 健康检查
curl -s localhost:8000/api/ontology/modules | head
```

预期：返回 7 个模块；元数据表已含由 TTL 投影的现有类（`status=published`）。

## 3. US1 主流程（API 级验证）

设 `H='-H X-User:analyst -H X-Role:senior_analyst -H Content-Type:application/json'`。

### 步骤 1 — 新建类（Class）
```bash
curl -s -X POST localhost:8000/api/ontology/classes $H -d '{
  "slpra_iri":"http://slpra/ont#HighPotentCompound",
  "label":"高活性化合物","module":"drug",
  "parent_iri":"http://slpra/ont#Compound","bfo_category":"BFO:0000040"}'
```
**预期**：`201`，体含 `version:1`、`status:"draft"`。

### 步骤 2 — 添加映射（SLPRA·BFO·字段）
```bash
curl -s -X POST 'localhost:8000/api/ontology/classes/http%3A%2F%2Fslpra%2Font%23HighPotentCompound/mappings' $H \
  -d '{"mapping_type":"bfo","target":"BFO:0000040"}'
curl -s -X POST 'localhost:8000/api/ontology/classes/http%3A%2F%2Fslpra%2Font%23HighPotentCompound/mappings' $H \
  -d '{"mapping_type":"source_field","target":"Excel:物料台账.活性等级","source_system":"Excel"}'
```
**预期**：两条 `201`；`GET /mappings/health` 中该类不再出现在 `unmapped`。

### 步骤 3 — 加约束（some + 基数）
```bash
curl -s -X POST 'localhost:8000/api/ontology/classes/http%3A%2F%2Fslpra%2Font%23HighPotentCompound/restrictions' $H \
  -d '{"kind":"some","property_iri":"http://slpra/ont#hasOEB","property_kind":"data","filler_iri":"http://slpra/ont#OEBBand"}'
```
**预期**：`201`；`GET /classes/{iri}` 详情的 restrictions 含该 `some` 约束（经 Owlready2 `is_a`，R1）。

### 步骤 4 — 风险属性向导（受控词表）
```bash
curl -s localhost:8000/api/ontology/risk-vocabularies            # 列 OEB/PDE/致敏 词表
curl -s -X POST localhost:8000/api/ontology/data-properties/risk $H -d '{
  "slpra_iri":"http://slpra/ont#hasOEB","label":"OEB 等级",
  "domain_iri":"http://slpra/ont#HighPotentCompound","datatype":"string","vocab":"OEB"}'
```
**预期**：`201`，`controlled_vocab` 已预填 OEB 词表。

### 步骤 5 — 乐观并发自检（R4）
```bash
# 用过期 expected_version 触发冲突
curl -s -o /dev/null -w "%{http_code}\n" -X PUT \
  'localhost:8000/api/ontology/classes/http%3A%2F%2Fslpra%2Font%23HighPotentCompound' $H \
  -d '{"label":"高活性化合物(改)","expected_version":1}'   # 已被步骤推进
```
**预期**：版本已前进时返回 `409`；带正确 `expected_version` 重试返回 `200`。

### 步骤 6 — 一致性/健康度校验（R9）
```bash
curl -s -X POST localhost:8000/api/ontology/validate $H
```
**预期**：`blocking:[]`（若缺 slpra_iri/bfo 映射则 blocking 非空）；`reasoner.ran` 视 JVM 而定，缺失则 `false` 且不阻断。

### 步骤 7 — 创建发布批次并提交审核
```bash
RID=$(curl -s -X POST localhost:8000/api/ontology/releases $H -d '{"title":"R2026.06.20-01 高活性化合物建模"}' | jq -r .id)
curl -s -X POST localhost:8000/api/ontology/releases/$RID/submit $H   # 跑 validate；blocking→409
```
**预期**：批次聚合本次所有 `draft` 变更（change_log 非空），`draft→in_review`。

### 步骤 8 — 导出 diff 预览（FR-009a）
```bash
curl -s "localhost:8000/api/ontology/export/diff?release_id=$RID" | jq '.triples_added | length'
```
**预期**：`triples_added` 含新类/属性/约束/映射三元组；未建模三元组（注释/对齐/SWRL）**不**出现在 removed。

### 步骤 9 — 发布（外科式回写 + 一次 Git 提交，FR-008a）
```bash
curl -s -X POST localhost:8000/api/ontology/releases/$RID/publish $H | jq '{status,ttl_commit_sha}'
```
**预期**：`status:"published"`，`ttl_commit_sha` 非空；`ontology/slpra/drug.ttl` 经 `git diff` 仅新增受管三元组，注释/对齐公理逐字保留。

## 4. 前端验证（手动）

打开 `http://localhost:3000/(dashboard)/ontology`：
1. 左侧树可见新类"高活性化合物"（草稿标记）。
2. Class 面板可编辑标签/父类/字段 schema；约束编辑器可视化 `some/exactly/disjoint`。
3. 映射面板显示健康度徽标（ok/unmapped/drift/orphan）。
4. TTL 工具条可"预览 diff"并"发布"；发布后徽标转为已发布、版本号 +1。
5. d3 图谱显示新类与带基数关系边。

### 本体类搜索（2026-09-15）

打开 `/ontology`，在当前模块的“类层次”搜索框验证：

1. 分别输入某个类的标签片段、仅在注释中出现的词、完整或部分 IRI，均应找到对应类；英文大小写变化和首尾空格不影响结果。
2. 搜索深层子类时祖先路径自动展开，不匹配的兄弟节点隐藏，匹配数量不包含仅用于保留路径的祖先，也不重复计算同一 IRI。
3. 点击结果，确认基本面板和图谱选中对应类；清空关键词恢复完整树。
4. 不存在的关键词显示“未找到匹配的类”；切换模块保留关键词并仅展示新模块结果；空模块显示“当前模块暂无类”。
5. 类详情尚未返回时提示加载中；部分详情失败时提示部分注释未加载，已取得的标签和 IRI 仍可搜索。搜索和清空不发起本体写请求。
6. 每级有子节点的类均排在叶子类之前，各组按英文类名 A–Z 排列（忽略大小写）；搜索与清空后排序一致，搜索隐藏子节点不改变其父类的排序分组。

浏览器使用合成 API 数据时，此检查只证明前端交互，不代表真实后端集成验收。

## 5. 通过判据（对应 Success Criteria / FR）

| 判据 | 来源 |
|------|------|
| 类/属性/约束/Action 可增改删并留痕 | FR-001..007, US1 |
| 映射健康度可见且阻断未映射发布 | FR / R9 |
| 并发保存冲突可检测（409） | FR-011a / R4 |
| 发布为批次、一次导出+一次提交 | FR-008a / R5 |
| TTL 外科式合并、diff 可预览、未建模公理保真 | FR-009a / R3 |
| 写/发布受角色门禁 | R7 |

## 6. 回滚 / 清理

```bash
curl -s -X POST localhost:8000/api/ontology/releases/$RID/rollback $H   # in_review→draft
docker compose down
```
> 已 `published` 的批次不可回滚；如需撤销以新批次反向变更并重新发布（保持 TTL/Git 线性可追溯）。
