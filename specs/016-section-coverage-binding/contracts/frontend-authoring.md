# Contract: Frontend Authoring — Section Coverage Editor

**Feature**: 016-section-coverage-binding
**Files**: `frontend/src/components/extraction/template-slot-editor.tsx`, `frontend/src/lib/api.ts`

Adds a section-level coverage authoring surface and an unresolved-candidate disposition affordance; **removes** the legacy per-field suggestion stream — the two `→ manual` collapse sites and the machinery that fed them are deleted, not merely neutralized. Also makes "关联文档类型" (associated document type) a **required** template attribute that grounds AI analysis. No new API endpoint — reuses `getRelationSchema` and the template create/update round-trip.

**Convergence (final)**: AI analysis returns only `{document_summary, coverage, unresolved_candidates}` (see suggest-slots-api.md). The editor therefore has **no** slot-suggestion stream to render: `suggestionToSlot`, the ghost-row block, `pending`/`aiSkipped` state, «全部采纳»/«已跳过» controls, and the `SuggestedSlot`/`SuggestedGroup`/`SuggestedSection` types are removed. Already-persisted slots still render/edit via the normal slot tree (FE3).

> **⚠️ SUPERSEDED (禁止原文取数候选, post-016)** — the `unresolved_candidates` stream (rows **F4 / FE1(part) / FE4**, and the `待解析候选` panel `pV1Mc` / candidate cards `OpHeT` `RT3aU` below) has since been **removed entirely** and this supersedes **FR-008a**. AI analysis now returns **only** `{document_summary, coverage}`; positions that bind to no ontology menu edge are **silently ignored** (never surfaced as candidates, never collapsed to `manual`). The `UnresolvedCandidate` type, `candidateToSlot`/`disposeCandidate`, `unresolvedCandidates` state, and `UnresolvedCandidatesPanel` are deleted from `template-slot-editor.tsx` + `api.ts`. Treat every F4/FE4/FR-008a/`unresolved_candidates` mention in this doc as historical. The **grounding** half of the change stands: "关联文档类型" drives `docClassIri` live from the metadata Select, and AI analysis is blocked until it is set.

> **➕ AMENDED (恢复结构骨架, Option A / post-`unresolved_candidates`)** — bug report「AI 分析 …Section 结构为何全丢了？」: AI analysis now returns `{document_summary, coverage, **sections**}` (see suggest-slots-api.md AMENDED note). `sections` is the LLM Round-1 document skeleton (`AiStructureSection[]`), returned verbatim with **no** ontology IRI binding. **New editor behavior**: `materializeSkeleton(res.sections)` builds the section→group→slot tree, each candidate → an author-fillable **`semantic`** slot (same shape as `addSlot`'s default: `{kind:"semantic", prompt:null, coverage_refs:[]}`), materialized **only when the template has 0 sections** (`sections.length === 0 && skeleton.length > 0`) — it never clobbers author edits, a re-run never stacks. Coverage still overlays as amber `pendingCoverage`. This **replaces** the empty-`ensureSeedSection` seed of **F11** (now the zero-section fallback only when the skeleton is empty). `AiStructureSection`/`AiStructureGroup`/`AiStructureCandidate` are added to `api.ts` and `SuggestSlotsResponse` gains `sections?`. **F12** and the strengthened **FE7** below govern; the buggy `_bind_ontology_iris` auto-binding is **not** revived.

---

## Type additions (`lib/api.ts`)

```ts
// mirrors backend OntologyRelationBinding / CoverageDeclaration
export interface OntologyRelationBinding {
  kind: "ontology_relation";
  doc_class_iri: string;
  predicate_iri: string;
  range_class_iri: string;
  required: boolean;               // default true
  required_properties?: string[];
  label?: string;
}
export interface FactSourceBinding { kind: "fact_source"; source: string; selector?: string; label?: string; }
export type CoverageBinding = OntologyRelationBinding | FactSourceBinding;

export interface UnresolvedCandidate {
  proposed_label: string; evidence: string; reason_unbound: string;
  suggested_disposition?: "bind" | "constant" | "manual" | "discard";
}

// AI Round-1 skeleton (verbatim from backend; shape = slot_suggester._ROUND1_SCHEMA). No IRI
// binding — the editor materializes each candidate into an author-fillable `semantic` slot.
export interface AiStructureCandidate { label: string; evidence_span?: string; evidence_offset?: number; }
export interface AiStructureGroup { title: string; candidates: AiStructureCandidate[]; }
export interface AiStructureSection { title: string; groups: AiStructureGroup[]; }

// SuggestSlotsResponse (016, see AMENDED note):  { document_summary: string; coverage: CoverageBinding[]; sections?: AiStructureSection[] }
//   — `unresolved_candidates` is deleted (SUPERSEDED note); the old per-slot slots/total_suggested/
//     skipped_duplicates/truncated fields + SuggestedSlot/SuggestedGroup/SuggestedSection stay deleted.
//     `sections` is the inert Round-1 skeleton, NOT their revival.
// SuggestSlotsRequest gains:   doc_class_iri?: string;
```

Existing `getRelationSchema()` / `RelationSchemaEdge` (`api.ts:87-101`) are **reused** for the relationship/type selectors. One new client method backs the "仅启用已建模类型" gating (bugfix Defect A):

```ts
export interface CoverageDocClassesResponse { capable: string[]; }
export const getCoverageDocClasses = (docClassIris: string[]) =>   // POST /api/ast-templates/coverage-doc-classes
  fetchAPI<CoverageDocClassesResponse>(...);                        // → modeled subset (today [...CMCReport])
```

## Editor model additions (`template-slot-editor.tsx`)

```ts
type SectionDef = {
  // ... existing (section_id, title, prompt, groups) ...
  coverage?: CoverageBinding[];        // NEW — round-trips inside schema_json opaquely (page.tsx handleSave)
};
```

---

## Behavior contract

| # | Requirement | Where |
|---|-------------|-------|
| F1 | **Delete collapse site #1**: `suggestionToSlot` is removed entirely (no suggestion stream exists to map). AI output flows only into `pendingCoverage` (coverage) and `unresolvedCandidates`. | (removed) |
| F2 | **Delete collapse site #2**: the editor's AI-suggestion ghost-row block is removed; there is no per-slot AI proposal to render. (Distinct from the coverage-**view** validation rows, which stay — that is the report-coverage surface, not the authoring suggestion stream.) | (removed) |
| F3 | New `SectionCoverageArea` (modeled on `SectionPromptArea:2198`, mounted near `2148-2157`) renders each `OntologyRelationBinding` as: relationship selector (from `getRelationSchema(doc_class_iri)`), target-type display, `required` toggle. Author can add/remove/mark-optional. | new component |
| F4 | Unresolved candidates render as a **pending disposition** list; each offers bind / convert-to-constant / convert-to-manual / discard (FR-008a). A candidate is NOT pre-classified as `manual`. | new candidate UI |
| F5 | Rule / constant / manual slots (`SlotInlineEditor:2260`) render and edit **unchanged**; they are not re-classified. | existing editor |
| F6 | Coverage round-trips with **no** `api.ts`/`page.tsx` change: `handleSave` (`page.tsx:957`) already sends `schema_json` opaquely; `section.coverage` nests inside it. | `handleSave` |
| F7 | `doc_class_iri` for selectors/AI is the template's **required** `关联文档类型` = `iriPattern` (a full class IRI). Precedence: prefer `iriPattern` when it is a full `http(s)://…` IRI, else fall back to `sourceDocClass?.doc_class_iri` (legacy templates whose `iri_pattern` is a partial substring). | `docClassIri` useMemo |
| F9 | **Required attribute enforcement (UI-only)**: the create wizard (`ast-templates/page.tsx`) and the edit-mode Basic-Info tab both surface `关联文档类型` as a required `DOCUMENT_TYPE_GROUPS` select (writing the full class IRI into `iriPattern`); the create «进入编辑器» button and `handleSaveMeta` are disabled/blocked when it is empty. The backend `iri_pattern` column stays nullable (no migration; legacy/default/file-fallback rows may lack it). | create + basic-info |
| F8 | Removing or marking-optional a declaration and saving changes the re-validated omission signal (US3 scenario 3). | save → re-validate |
| F10 | **Bugfix Defect A — 仅启用已建模类型**: `getCoverageDocClasses(全量 DOCUMENT_TYPE_GROUPS IRI)` (one `useQuery`, `staleTime` 5min) → `capableSet: Set<string> \| null`. The `关联文档类型` dropdown disables every non-capable `SelectItem` with a 「（暂未建模）」 suffix + a hint line 「仅『已建模本体关系』的类型可用于覆盖声明与 AI 分析（当前：…）；其余类型待本体补充关系后自动启用」. **`docClassUnmodeled`** (`capableSet` known ∧ selected ∉ set) also disables the AI-分析 button (title explains) and hard-intercepts `runAiAnalysis` with an explicit error instead of a silent empty result. **Degradation**: query loading/failed ⇒ `capableSet = null` ⇒ nothing disabled (never block authoring, Principle VI). | `coverageCapableQuery`, dropdown, `runAiAnalysis` |
| F11 | **Bugfix Defect B — 建议落点 (fallback)**: `pendingCoverage` only renders inside a section's `SectionCoverageArea`, but a create-flow template starts with **zero** sections. When AI returns a non-empty `nextPending` **but an empty skeleton** (`res.sections` empty), `ensureSeedSection()` seeds one default section (race-free `setSections(prev => prev.length ? prev : [默认分节])`, auto-expanded) so amber AI suggestions stay visible/adoptable. Now the **fallback** to F12 (skeleton materialization takes precedence). Seeds **only** when zero sections exist; repeated analysis never stacks. | `runAiAnalysis`, `ensureSeedSection` |
| F12 | **Bugfix — 恢复结构骨架 (Option A, primary)**: when the template has **0 sections** and `res.sections` is non-empty, `materializeSkeleton(res.sections)` builds the section→group→slot tree from the Round-1 skeleton — each `candidate` → an author-fillable **`semantic`** slot (`{kind:"semantic", prompt:null, coverage_refs:[]}`; `required:false`, `on_missing:"annotate"`, default placeholder — same shape as `addSlot`). Empty-`groups` sections get one `{title:"新分组", kind:"fields", slots:[]}` fallback; ids use `ts + index` suffixes (`sec_${ts}_${i}`/`grp_${ts}_${i}_${j}`/`${grpId}.ai_${k}`) to avoid synchronous `Date.now()` collision; race-free `setSections(prev => prev.length ? prev : built)`, all ids auto-expanded. `evidence_span`/`evidence_offset` are dropped (no `SlotDef` home). Never clobbers author edits; a re-run never stacks. **No** `_bind_ontology_iris` auto-binding revived — slots are inert until the author fills them. | `runAiAnalysis`, `materializeSkeleton` |

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| FE1 | No editor path forces a graph-sourced position to `manual` (both collapse sites removed). | FR-002, FR-008a / defect 1 |
| FE2 | Coverage is authored as relationship/type selectors, never per-field extraction bindings; selectors are populated from `getRelationSchema`, so no free-text/individual can be entered. | FR-003, FR-009 / SC-002 |
| FE3 | Rule/constant/manual slots are preserved and independently editable. | FR-008 |
| FE4 | Unresolved candidates require explicit author disposition. | FR-008a |
| FE5 | Persistence requires no schema/endpoint change (opaque `schema_json` round-trip). | Minimal Complexity (V) |
| FE6 | Only ontology-modeled document types are selectable/analyzable; "modeled" is computed live from the ontology (auto-widens as the T-Box grows — no frontend list to maintain). A probe hiccup degrades to all-enabled, never all-disabled. | Defect A / 仅启用已建模类型 / VI |
| FE7 | AI analysis **reproduces the uploaded sample's Section skeleton** as editable `semantic` slots (F12), not merely "provides a landing surface": on a zero-section template, `res.sections` materializes the real SECTION Ⅰ/Ⅱ + sub-groups + fields (附件/风险回顾日期/QA意见/批准人…) the author saw in the document. Ontology coverage still overlays as amber pending. Empty-skeleton fallback seeds one default section (F11) so no coverage suggestion is ever computed-but-invisible. | Defect B / Option A |

---

## Design reference

The approved visual design for this surface is `design.pen` → frame **"Slot Tree Component — Full View"** (`vwW7Q`): a full-height, no-scroll, no-clip render of the reusable **Slot Tree** component (`JKSZm`), which is the AST editor's Right-Panel slot tree. It is the layout source of truth for `template-slot-editor.tsx`. The frame renders two worked sections — «1. 概述» (`Ox2NN`) and «2. 检测结果» (`H2CjZ`) — plus a `+ 添加分节` affordance (`EC9lh`).

| Design node (in `JKSZm`) | Rendered element | Contract |
|---|---|---|
| **本体覆盖声明** area `DcRjT` (§概述) / `zt2iu` (§检测结果) | `SectionCoverageArea`: `git-branch` header, Object Chip = doc entity type (药品产品 / 质量标准), `+ 添加关系` | **F3, FE2** |
| Decl rows `WrePQ` `describes→药品产品`, `hjgm6` `hasSpecification→质量标准`, `DFavp` `hasTestItem→检测项目` | one `OntologyRelationBinding` row: predicate (JetBrains Mono local-name) + `arrow-right` + target type, with the `slpra:*` **type** IRI beneath — never a sample individual | **F3, FE2 / SC-002** |
| Required control `E1GLkD`/`kewX5`; Req Switch `qkoDx` (on, 必填) / `vrXJb` (off, 选填) | `required` toggle, required-by-default; author demotes to optional | **F8 / FR-005a** |
| Prop checklist `LrfhC` («属性覆盖 · 4 项（1 必填缺失）»), `ghs0b` («全部命中»), `SbBzd` (含量测定/有关物质/溶出度) | ontology property expansion of the relationship's target type | **FR-004** |
| Status pills — green `#16A34A` (Status Pill `O90a0`, Hit Pill `oAF9y`), amber `#FBBF24` (Prop 溶出度 `wmPCU`), border-only Miss Pill `pi2Np` + «缺失不计入遗漏» (`w5vGd`) | position status FILLED / MISSING_REQUIRED / BLANK_OPTIONAL (not-required → not counted) | **V2, V3, V4 rendered** |
| **Coverage Decl AI** `ikcOK` — amber card, `hasSynthesisRoute→合成路线`, «AI 建议», «采纳 / 忽略», «采纳后按本体展开属性 · 默认必填» | AI-proposed graph-sourced binding rendered as a coverage declaration — **never** collapsed to `manual` | **F1 / S1, FR-005a** |
| **待解析候选** panel `pV1Mc` — `triangle-alert` header, count `qqXWF`, hint `qH0VX` «不会自动降级为人工插槽» | unresolved-candidate pending list | **F4, FE4 / FR-008a** |
| Candidate cards `OpHeT` (设备编号 646; reason `yUbic` «匹配到样本个体而非本体类型»), `RT3aU` (SOP-QC-114): evidence + reason + 绑定关系 / 设为常量 / 设为人工 / 丢弃 | per-candidate disposition = bind / constant / manual / discard | **F4, FE4** |
| Non-Ontology Caption `iUqjt` (§概述) / `wopEz` (§检测结果) «其他插槽 · 规则 / 常量 / 人工（不参与本体覆盖）» | visual separation of non-graph slots from ontology coverage | **F5, FE3 / FR-008** |
| Slot Groups `rXC8T` (基本信息) / `lusM4` (数据汇总) + slot rows with Kind badges (常量/人工/规则/手工/固定字段), Required/可选/已禁用 badges, grip reorder, `+ 添加插槽` | rule/constant/manual slots preserved and independently editable | **F5, FE3** |
| **Inline Editor** `LrIAX` (Row ID+Label / Source+IRI / Required+Missing / Actions) | `SlotInlineEditor:2260` unchanged | **F5** |
| Section Prompt Area `XiB0n` («行文 Prompt», §概述) | 015 `Section.prompt` surface — out of 016 scope, shown to confirm untouched coexistence | (015, preserved) |
| Section header signals — Count Badge «N 覆盖» (`tLR7V`/`q8fYu`), Pending Badge «+1» (`v82EL`), Prompt Indicator sparkles (`iVdN2`/`hTiRR`) | per-section coverage count + pending-candidate count | F3/F4 surfacing |

**Componentization**: `JKSZm` is marked `reusable:true` (the "Slot Tree" component). The in-editor Right Panel and the full-view frame both instance it, so the shipped editor and its design stay in lockstep; the full-view frame merely grows to the tree's natural height for scroll-free design review.

---

## Verification (no JS test runner in repo — manual/quickstart)

Frontend behavior is verified through `quickstart.md` editor walkthrough:
1. Run AI analysis on a CMC sample → section shows coverage declarations (relationships), **not** a list of manual fields (FE1/SC-001).
2. Inspect a declaration → it names a **type** (e.g., `DrugProduct`), no individual id (FE2/SC-002).
3. A `rule`/`constant`/`manual` slot in the same template is untouched (FE3).
4. Save → reload → coverage persists (FE5); mark a declaration optional → re-validate → omission signal changes (F8).
5. **Bugfix — 恢复结构骨架 (F12/FE7)** — "从样例文档创建" → 选 **CMC 报告** → 「AI 分析」on a zero-section template: **multiple sections** materialize from the sample's Round-1 skeleton (SECTION Ⅰ/Ⅱ + sub-groups), each field an editable **`semantic`** slot (附件/风险回顾日期/QA意见/批准人…). Amber AI coverage suggestions still overlay and can be 采纳/忽略. Editing then re-running AI does **not** clobber existing sections (only pending coverage refreshes). Empty-skeleton fallback still seeds one default section (F11).
6. **Bugfix Defect A (F10/FE6)** — open the `关联文档类型` dropdown: all types except CMC 报告 are greyed with 「（暂未建模）」 and the hint line is visible. Selecting a (pre-existing) unmodeled type disables the AI button with an explanatory title; clicking is intercepted with an explicit message rather than yielding a silent empty result.
