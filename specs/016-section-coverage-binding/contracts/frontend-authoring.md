# Contract: Frontend Authoring — Section Coverage Editor

**Feature**: 016-section-coverage-binding
**Files**: `frontend/src/components/extraction/template-slot-editor.tsx`, `frontend/src/lib/api.ts`

Adds a section-level coverage authoring surface and an unresolved-candidate disposition affordance; **stops** the two `→ manual` collapse sites. No new API endpoint — reuses `getRelationSchema` and the template create/update round-trip.

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

// SuggestSlotsResponse gains:  coverage: CoverageBinding[]; unresolved_candidates: UnresolvedCandidate[];
// SuggestSlotsRequest gains:   doc_class_iri?: string;
```

Existing `getRelationSchema()` / `RelationSchemaEdge` (`api.ts:87-101`) are **reused** for the relationship/type selectors — no new client method.

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
| F1 | **Stop collapse site #1**: `suggestionToSlot` (`214-227`) MUST NOT map non-`extraction` suggestions to `manual`. Graph-sourced suggestions become `coverage` entries, not slots. | `suggestionToSlot` |
| F2 | **Stop collapse site #2**: the ghost-row badge (`2099-2104`) MUST NOT relabel coverage/`llm_extraction` positions as `manual`; render them as ontology-coverage rows. | ghost-row render |
| F3 | New `SectionCoverageArea` (modeled on `SectionPromptArea:2198`, mounted near `2148-2157`) renders each `OntologyRelationBinding` as: relationship selector (from `getRelationSchema(doc_class_iri)`), target-type display, `required` toggle. Author can add/remove/mark-optional. | new component |
| F4 | Unresolved candidates render as a **pending disposition** list; each offers bind / convert-to-constant / convert-to-manual / discard (FR-008a). A candidate is NOT pre-classified as `manual`. | new candidate UI |
| F5 | Rule / constant / manual slots (`SlotInlineEditor:2260`) render and edit **unchanged**; they are not re-classified. | existing editor |
| F6 | Coverage round-trips with **no** `api.ts`/`page.tsx` change: `handleSave` (`page.tsx:957`) already sends `schema_json` opaquely; `section.coverage` nests inside it. | `handleSave` |
| F7 | `doc_class_iri` for selectors/AI comes from the editor's existing `iriPattern`/`sourceDocClass` prop (`iriPattern` at `120`). | request build |
| F8 | Removing or marking-optional a declaration and saving changes the re-validated omission signal (US3 scenario 3). | save → re-validate |

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| FE1 | No editor path forces a graph-sourced position to `manual` (both collapse sites removed). | FR-002, FR-008a / defect 1 |
| FE2 | Coverage is authored as relationship/type selectors, never per-field extraction bindings; selectors are populated from `getRelationSchema`, so no free-text/individual can be entered. | FR-003, FR-009 / SC-002 |
| FE3 | Rule/constant/manual slots are preserved and independently editable. | FR-008 |
| FE4 | Unresolved candidates require explicit author disposition. | FR-008a |
| FE5 | Persistence requires no schema/endpoint change (opaque `schema_json` round-trip). | Minimal Complexity (V) |

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
3. An unresolved candidate appears in a pending list with disposition actions (FE4).
4. A `rule`/`constant`/`manual` slot in the same template is untouched (FE3).
5. Save → reload → coverage persists (FE5); mark a declaration optional → re-validate → omission signal changes (F8).
