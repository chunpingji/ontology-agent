# 修复方案：风险评估报告「评估小组 / 审批人小组」在行文预览中无法载入

状态：已批准（用户选 A：先落文档，再按文档执行）
关联模板：`7fa1236c-5906-47c9-b89c-69347921eb43`（「风险评估文档」v2）
关联本体：`https://ontology.pharma-gmp.cn/slpra/risk/RiskAssessmentReport`

---

## 1. 问题陈述

两件事，同一根链条：

1. **本体任务（原始需求）**：`RiskAssessmentReport` 应「含评估小组」（`hasAssessmentTeam → AssessmentTeam`，小组含角色关系）与「含审批人小组」（`hasApproverTeam → ApproverTeam`，小组含角色关系）。
2. **缺陷（实测）**：模板 `7fa1236c` 编辑页「行文 Prompt 预览」无法载入评估小组成员。

用户假设：本体图谱只从模板「关联文档类型」（源文档 = `CMCReport`）取关系，预览时**从不**物化 `RiskAssessmentReport` 自身的 `hasAssessmentTeam` / `hasApproverTeam` mock 数据。已实证证实该假设成立。

---

## 2. 根因：四层断链（含实证）

抽取管线单一事实脊（fact-spine）：
`edges → edges_to_facts → 规则 → validate_coverage / coverage_scoped_edges → narrative_generator._format_facts（LLM 合成）`

产品报告（`RiskAssessmentReport`）**自身**的关系（评估小组、审批人小组、风险评分…）不在源文档里，需由主数据/其它 A-Box 物化成 edge 注入同一条 `edges`。这条支路（Gap B）当前是断的：

| 层 | 断点 | 实证 |
|---|---|---|
| **L1 管线接线** | `product_report_edges_for_template()` **零调用者**；预览端点只用 annotation 缓存 edge（源文档 `CMCReport` 关系），从不物化 `hasAssessmentTeam` edge。段级行文预览 `generate_section_narratives` 仅 `_format_facts(edges)` 转储，连 fact_source 都不解析。 | `grep product_report_edges_for_template app/` → 仅定义处。预览端点 `ast_templates.py:760-787` 直接用 `result["relationships"]`。 |
| **L2 模板声明** | 模板 `7fa1236c` `n_coverage=0`：评估小组插槽是普通语义插槽，无 coverage 绑定。即便 L1 接好，`_declared_predicates()` 返回空集 → 什么都不物化。 | `product_report_edges_for_template(None, tpl)` 返回 `[]`。 |
| **L3 审批人缺失** | 无 `hasApproverTeam` finder，无审批人 mock 源。 | `_PRODUCT_FINDERS_BY_PREDICATE` 只有 `HAS_ASSESSMENT_TEAM_IRI`。 |
| **L4 本体未加载** | 新 TTL 类 `AssessmentTeam` / `ApproverTeam` 尚未进运行态 OWL 库。 | `slpra.sqlite3` 中新类 0 次出现。 |

Mock 侧是好的：`get_assessment_team_source().list_members()` 稳定返回 5 名成员（QA/WH/EHS/PPA/ENG）。

**关键实证**：`narrative_generator._format_facts()` 会把 edge 的 `object_data_properties` 逐条渲染为 `(label: value)`。评估小组 edge 的成员名册正是放在 `object_data_properties`（label=角色, value=姓名（部门））。**所以只要该 edge 进入 `edges`，段级行文预览就会显示名册** —— 这决定了正确修法是 `ontology_relation` + `product_report_edges`（把小组物化成 edge），而非旧 `fact_source` 路径（仅 `generate_semantic_slots` 语义插槽可见，段级行文预览看不到）。

---

## 3. 修复设计（Option A）

### 设计决策

1. **用 `ontology_relation` 绑定，不用 `fact_source`**
   段级行文预览走 `generate_section_narratives → _format_facts(edges)`，只认 `edges` 里的 edge。`fact_source` 绑定只在 `generate_semantic_slots`（语义插槽）里被 `resolve_fact_source` 解析，段级预览看不到。物化成 edge 才能同时覆盖：段级行文预览、语义插槽、确定性覆盖校验、真实报告。既有 `scripts/seed_assessment_team_binding.py`（fact_source 路径）保留但不作为本修复主线。

2. **富集（enrich）edge 的责任放在 generator，`assess_deterministic` 回传富集后的 edges**
   - `generate_with_coverage`（真实报告，唯一咽喉点；`extraction.py:1012/1144` 调用方传原始 edge、且不单独调 narrative 函数）：取到 engine 后立即 `edges = edges + product_report_edges_for_template(engine, template)`。下游 `edges_to_facts` / `validate_coverage` / `_try_narrative_generation` 全部见富集后 edge。
   - `assess_deterministic`（仅预览端点调用）：同样富集，并把富集后的 `edges` 作为返回三元组 `(post_rows, manifest, edges)` 回传。
   - 预览端点：`rows, manifest, edges = assess_deterministic(edges)` 后，把**同一份**富集 edges 传给 `preview_section_narrative`。→ 富集只发生一次，无重复 edge，确定性上下文与叙述所见 edge 完全一致。
   - 幂等/零爆炸半径：`product_report_edges_for_template` 只物化模板**已声明**的谓词；未声明模板（含绝大多数测试与既有真实模板）`_declared_predicates` 为空 → 富集为 `[]` → 逐字节不变。

3. **审批人对称补齐**：新增自包含审批人 mock 源 + `hasApproverTeam` finder。审批权限角色（MAH/受托方/管理层/QA 审核）不在 `role_source` 的 5 个职能角色里，故审批人源自带确定性名册（映射 personnel 本体 accountability/QA 角色类 IRI），不强行复用评估的 5 角色。

### 分层改动清单

**L4 本体（已在前序完成 TTL 编辑，本次仅重载）**
`ontology/slpra/slpra-risk.ttl` 已加：`RiskAssessmentReport` 的两条 `someValuesFrom` 限制、`AssessmentTeam` / `ApproverTeam` 类（`⊑ slpra-pers:Organization`，含 `hasMember some Person` + `includesRole some GxPRole`）、`hasAssessmentTeam` / `hasApproverTeam` / `hasMember` / `includesRole` 四个对象属性。→ **重启后端**触发 `OntologyEngine.load()`（每次启动删库重建）+ `project_from_ttl()` 重投影 DB meta。

**L3 审批人 mock + finder**
- 新增 `backend/app/services/extraction/approver_team_source.py`：`ApproverTeamMemberFact` + `MockApproverTeamSource.list_members()`（确定性审批人名册）+ `get_approver_team_source()`。镜像 `assessment_team_source.py` 的降级契约（源不可用/空 → `[]`）。
- `backend/app/services/reporting/product_report_edges.py`：加 `HAS_APPROVER_TEAM_IRI` / `APPROVER_TEAM_IRI` 常量、`_approver_team_edge(engine)`（镜像 `_assessment_team_edge`，`predicate_label="含审批人小组"`，`source_ref="external.approver_team"`），注册进 `_PRODUCT_FINDERS_BY_PREDICATE`。
- （对称）`backend/app/services/reporting/fact_sources.py`：注册 `external.approver_team` fact source，镜像 `external.assessment_team`。

**L1 管线接线**
- `backend/app/services/reporting/risk_report_generator.py`：
  - 私有 `_enriched_edges(self, edges, engine)` = `edges + product_report_edges_for_template(engine, self._template)`。
  - `generate_with_coverage`：取 engine 后 `edges = self._enriched_edges(edges, engine)`。
  - `assess_deterministic`：同上，返回改为 `(post_rows, manifest, edges)`。
- `backend/app/api/ast_templates.py` 预览端点：改为 `rows, manifest, edges = ...assess_deterministic(edges)`，把富集 edges 传给 `preview_section_narrative`。
- `backend/tests/test_reporting/test_risk_report_generator.py:222`：解包改为三元组。

**L2 模板声明（seed 脚本）**
- 新增 `backend/scripts/seed_report_team_coverage.py`：幂等，给目标模板（默认 `7fa1236c…`）注入两条 `ontology_relation` coverage 绑定：
  - `hasAssessmentTeam → AssessmentTeam`（doc=`RiskAssessmentReport`, label=评估小组）
  - `hasApproverTeam → ApproverTeam`（doc=`RiskAssessmentReport`, label=审批人小组）
  - 就近挂到标题含「评估小组」「审批人/审批」的分节；找不到则挂到首节（`_declared_predicates` 全模板扫描，段级预览即可见）。可选：把对应语义插槽 `coverage_refs` 指向这两个 `coverage_key`（`coverage.hasAssessmentTeam__AssessmentTeam` / `coverage.hasApproverTeam__ApproverTeam`），供真实报告语义插槽 scope。
  - `flag_modified(row, "schema_json")` 持久化。
  - 运行：`DATABASE_URL=postgresql://slpra:slpra_dev@localhost:55432/slpra uv run python scripts/seed_report_team_coverage.py <template_id>`

---

## 4. 验证

1. **单测**：`uv run pytest tests/test_reporting/test_risk_report_generator.py tests/test_reporting/test_fact_sources.py tests/test_extraction -q`（含新增 `test_approver_team_source.py`；确认 `test_assess_deterministic_matches_generate_with_coverage` 三元组后仍绿）。
2. **富集函数**：脚本内直接 `product_report_edges_for_template(engine, tpl_7fa1236c)` 应返回 2 条 edge（评估小组 + 审批人小组），各带成员 `object_data_properties`。
3. **端到端预览**：重启后端 → 对模板 `7fa1236c` 已标注 job 调 `/api/ast-templates/preview-section-narrative`（评估小组分节），叙述应含成员名册（王玉华/刘建国/陈志强/张伟民/李国栋 + 部门）。
4. **本体加载**：`slpra.sqlite3` 或 meta API 中 `AssessmentTeam` / `ApproverTeam` / `hasAssessmentTeam` / `hasApproverTeam` 可查。

## 5. 爆炸半径

- 未声明该谓词的模板/测试：`_declared_predicates` 为空 → 富集为空 → 逐字节不变。
- 唯一签名变更：`assess_deterministic` 返回三元组（仅 1 个测试 + 预览端点调用，均同步更新）。
- Mock 源不可用/空：finder 返回 `None` → 无 edge → 该声明位读作 BLANK（Principle VI 优雅降级），不抛错。
