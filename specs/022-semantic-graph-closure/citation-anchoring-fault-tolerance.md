# 引文锚定容错机制改进方案（含表格内容专项）

诊断基线：报告模板 `8542466b-d6e6-4052-ae7e-05ca9c99a1c3`，文档 `885c6aec-9b09-49ec-a368-926100e93a02`，
`usesEquipment` 关系任务 `ecd1b727717d` 的 8 个提议全部锚定失败。

## 0. 结论先行

**问题不是"缺少容错机制"，而是"已建成的容错机制没有接到出问题的那条链路上"，外加一处反馈回路 bug。**

仓库中已经存在 Codex 与我各自独立推导出的全部关键构件：

| 需要的能力 | 已有实现 | 是否接入失败链路 |
|---|---|---|
| 记录级目标授权（整逻辑行为 fact 目标，表头保持 binding-only） | `ontology_guided/records.py` `RecordIndex` + `ontology_guided/context.py:95` `assemble_context` | ❌ 否 |
| 原子引用协议（整格只给 ID、禁模型坐标、禁跨格拼接、FactSpan/BindingSpan 分离） | `ontology_guided/citations.py` `CitationProtocol`（`evaluation-atomic-citations-v4`） | ❌ 否 |
| 有界重提议（不静默改写锚点） | `citation_repair.py` `repair_feedback` | ⚠️ 接入了，但传错了权限域 |
| 证明完整性门槛 | `ontology_guided/verification.py` `ProofGate` | ❌ 否 |

关系图谱面板读的是**旧标注链路**：
`api/extraction.py:579 configured_generic_runner` → `GenericExtractionRunner`
→ `hierarchical_context.build_context` → `ModelProtocol`。

新内核只在文档分析链路：`document_analysis/execution.py:69` → `OntologyGuidedExecutor`。

---

## 1. 根因：关系抽取的结构性死锁

### 1.1 证据

`usesEquipment` 任务持有 8 个 `object_candidates`（Reactor / Centrifuge …，均由先前实体任务成功抽出）。
模型回答"主体使用这些设备"，并引用了**设备自身的出处**——这是文档中唯一见证该关系的位置。

| evidence_id | 引文 | 错误码 | 在 target_ranges |
|---|---|---|---|
| `35b4f5d593ad` | `1000L反应釜（RE64615/RE64215）` | `source_excerpt_mismatch` | ✅（但该单元无设备号） |
| `27e2ff546e25` | `800mm离心机（CT64611/ CT64214）` | `source_quote_outside_scope` | ❌ |
| `1b4d399eb5ca` | `1000L反应釜（RE64615/RE64215）` | `source_quote_outside_scope` | ❌ |
| `c43e42240e40` | `800mm离心机（CT64611/CT64214）` | `source_quote_outside_scope` | ❌ |
| `e74c5559ac99` | `500L反应釜（RE64614/RE64214）` | `source_quote_outside_scope` | ❌ |
| `66b0bb89e2bf` | `500L反应釜（RE64614/RE64214）` | `source_quote_outside_scope` | ❌ |
| `9b1886931ef7` | `RE64614/RE64214` | `source_quote_outside_scope` | ❌ |
| `9661b9cf2ae2` | `50L压滤器（PF64613/PF64213）` | `source_quote_outside_scope` | ❌ |

### 1.2 死锁的代码路径

- `hierarchical_context.py:302-313`：对象候选证据以 `purpose="subject_evidence"`、
  **`fact_eligible=False`** 进入 `allowed_binding_regions`。
- `extraction_tasks.py:664-668`：关系的 `assertion_spans` **只**对 `allowed_fact_regions` 锚定。
- `extraction_tasks.py:693-694`：`if not anchors: raise no_relationship_evidence`。

> 凡是对象出处落在主体 `target_ranges` 之外的关系任务，**结构上不可能产出任何引文**。
> 表格密集文档尤其致命——表格里对象天然在别的单元格。

### 1.3 新内核不存在该死锁

`ontology_guided/context.py:95-100`：

```python
("record_heading_or_header", False, record.header_units),
("parent_table_context",     False, record.parent_units),
("target",                   True,  record.source_units),   # 整逻辑行的全部非表头格
("table_note_metadata",      False, record.note_units),
```

`record.source_units` = 逻辑表格行的**全部非表头单元格**（`records.py:56-58`），全部 `fact_eligible=True`。
表头始终保持非 fact。任务以冻结的 `RecordView` 为目标，而非主体作用域。

---

## 2. 次要失败模式（旧协议自带）

| 模式 | 实例 | 归因 |
|---|---|---|
| 不可见空白 | `800mm离心机（CT64611/ CT64214）` vs `（CT64611/CT64214）`；`SM5592-A14` vs 原文 `产物SM5592- A14` | DOCX run 分割；模型在一处归一化 |
| 尾部截断 | `于1000L反应釜（RE64615/RE64215）中加入230kg的1，4-二氧六环...` | 模型不愿复制整格长文本 |
| 内部省略 | `在...水中几乎不溶`（原文 `在0.1mol/L氢氧化钠溶液和水中几乎不溶`） | 同上 |
| 错误 evidence_id | `35b4f5d593ad` 挂设备号 | 模型混淆来源 |
| 短引文歧义 | `否` 在 `是否需要灭活：否。` 中命中两次 | 重复子串 |

**这五类中的前三类，`CitationProtocol` 按构造即可消灭**：整单元引用只给 `evidence_id`、`text` 省略，
模型无需复制原文，也就无从产生空白差异、截断和省略号。

---

## 3. 已确认的 bug：修复反馈回路指向错误权限域

`extraction_tasks.py:1338-1339`：

```python
self._citation_feedback[task.task_id] = repair_feedback(
    outcomes, ir, envelope.allowed_binding_regions,
)
```

`repair_feedback` 在传入的允许域中搜索 `exact_matches` 作为定位提示回喂模型。
对**关系**任务，assertion span 必须落在 `allowed_fact_regions`，但反馈搜索的是其超集
`allowed_binding_regions`。于是 `exact_matches` 精确指向了模型**无权作为事实引用**的单元，
重试被引导回同一个 `source_quote_outside_scope`。

**修复回路不仅没救回这 8 条，反而在确认模型的错误选择。**

修法：按字段角色分域搜索——`assertion_spans` 用 fact 域（property 任务用 binding 域，
与 `assertion_candidate` 的分支保持一致），`conditions` 用 binding 域。
让 outcome 事件携带字段路径，`repair_feedback` 据此选域。

---

## 4. 方案

### 轨道 A（根治，推荐主线）：关系图谱面板改用 ontology_guided 结果

理由：所需机制在新内核中已完整实现且相互自洽（记录级授权 + 原子引用 + ProofGate），
在旧链路上重建等于把新内核的记录模型分叉复制一份。

步骤：
1. **先解阻两次死掉的文档分析运行**：`2f6940fa`（`blocked_dependency` / `fingerprint_mismatch`）、
   `a484f2bf`（`paused` / `operator_pause`）。
2. **验证前置条件（必须先做，尚无正面证据）**：确认 `OntologyGuidedExecutor` 在本模板上
   确实产出关系候选，且 `preview_relationships` 能消费其投影（`ontology_guided/projection.py`
   / `document_analysis/public_projection.py`）。**在这一步通过之前不要承诺轨道 A。**
3. 让标注端点在存在成功的文档分析运行时改读其投影，否则回退旧链路。
4. `_ANNOTATOR_VERSION` 递增使标注缓存失效（缓存只按 `_version` 键控）。

### 轨道 B（止血，若面板必须继续由旧链路供数）

按"风险调整后收益"排序：

**B0｜修复反馈域 bug（§3）** — 改动最小、无协议变更、无缓存失效风险。
应无条件先做，与选哪条轨道无关。

**B1｜把 `CitationProtocol` 接入 `GenericExtractionRunner._invoke`**（`extraction_tasks.py:472`）
`CitationProtocol` 与 `ModelProtocol` 接口同构（system/user/schema/decode），
按构造消灭空白 / 截断 / 省略号 / 跨格拼接四类，并保守拒绝重复子串。
需把 `PROTOCOL_VERSION` 纳入 context hash，`MODEL_CONTEXT_VERSION` v6 → v7，全量重跑。
⚠️ **B1 不解决 §1 的死锁**——其指令反而强化该边界
（"subject_evidence 等背景来源不能代替本次事实目标"）。

**B2｜记录级目标授权移植到旧链路** — 唯一能解 7 条 `outside_scope` 的办法。
用现成的 `RecordIndex` 在任务构造期把逻辑记录的全部非表头 `source_refs` 显式写入 `target_ranges`，
表头保持 binding-only。必须用 `logical_row_id` 而非裸 `row_index`（合并单元格可跨多逻辑行）。
记录超预算时不得任意拆分后宣称验证完成，应记 `context_budget_exceeded`。
**这正是轨道 A 中已有实现的能力**——B2 = 分叉复制，代价与长期维护成本都高于 A。

**B3｜有界重提议处理错误 evidence_id**（`35b4f5d593ad` 那条）
`citation_repair.py` 已是正确形态（"never silently rewrite a proposed source anchor"）。
配合 B0 修好域之后即可生效。

**B4｜空白不敏感定位投影（R3）** — 仅在不采纳 B1 时才需要。
限同一 EvidenceUnit 内、唯一命中、仅空白差异、可逆偏移映射；
provenance 必须保存含原始空白的真实 excerpt；**不得**顺带做 NFKC / 大小写 / 标点归一化。

### 明确否决的做法

| 做法 | 否决理由 |
|---|---|
| 放宽 `ir.resolve(anchor) == span.text` 精确重放 | 摧毁证据可重放不变式，一切下游签名/审计失效 |
| 省略桥接 `A...B` → `[i, j+len(B))` 直接作为肯定证据 | 扩展区间可吞入否定、条件、另一主体或另一介质。至多作"系统扩展上下文"并重新独立语义验证，标 `quote_fidelity=expanded_from_ellipsis`，默认不进 positive |
| 无条件 evidence_id 重挂 | `source_quote_outside_scope` 是**权限错误**，绝不能用另一个位置洗白。仅当原错误是 `source_excerpt_mismatch`、模型未给坐标、原文精确匹配、授权域内唯一、且同一冻结逻辑行时才可考虑；跨记录一律拒绝 |
| proposal 内"剩一条 span 即通过" | 会丢掉主体、表头、否定或条件 span 后仍形成 binding 而误入 `positive_eligible`。且 sibling proposal 已由外层循环隔离（`PartialTaskFailure`），部分成功能力本就存在。门槛应是"至少一个完整证据包"，不是"至少一个 anchor" |
| 按引文长度阈值强制 context | 长度既非必要也非充分条件——短设备码可能唯一，长句也可能重复。应按**实际命中数**触发服务端 occurrence-ref 消歧 |

---

## 5. 建议执行顺序

1. **B0**（反馈域 bug）— 独立、低风险、无论走哪条轨道都需要。
2. **轨道 A 步骤 1-2**（解阻两次运行并验证新内核确能产出关系）。
3. 分叉点：
   - 步骤 2 通过 → 走轨道 A 步骤 3-4。
   - 步骤 2 不通过 → 走 **B1 → B2**，并把 B2 实现为对 `RecordIndex` 的复用而非复制。

## 6. 初始待验证项与当前结论

- `OntologyGuidedExecutor` 的模板前置实测未通过：后继运行及有界诊断没有正确绑定产品，
  详见 §7.1；没有切换面板投影。
- `preview_relationships` 直接消费 ontology_guided 投影的兼容性仍未验收。因前置条件未通过，
  本次没有实施该切换，也不从投影文件存在推断面板可用。
- B1 的旧响应模型与原子协议 decode 兼容性已通过定向回归及 §7.5 的真实模板验收。

## 7. 本次执行记录（2026-09-09）

- 用户补充验收：模板 `8542466b-d6e6-4052-ae7e-05ca9c99a1c3` 的一级关系必须正确
  连接实体并识别实体属性，至少核验 `describes → DrugProduct`（含合法子类）、
  `hasSynthesisRoute → SynthesisRoute` 及源文档中其他适用一级分支；非空图谱不算完成。
- **B0 已实现**：锚定错误携带 `field_path`；纠错按事实/绑定字段分别搜索。关系 recall
  的 `assertion_spans`、实体 mention、属性 value 使用 fact 域；条件、独立绑定及属性
  recall 的 `assertion_spans` 使用 binding 域。历史事件缺字段路径时保守处理；旧版未执行
  纠错预约重新生成合法提示，不增加重试额度，不重置预算。
- B0 定向回归实际结果：`test_citation_feedback_domains.py`、
  `test_template_relation_recovery.py`、`test_extraction_tasks.py` 合计 44 passed；
  本次四个相关源码/测试文件 Ruff 通过。
- **核验后的前提修正**：`CitationProtocol` 虽存在于共享目录，但当前
  `LocalModelRecognitionAdapter._request` 直接使用 `ModelResponse` / `VerificationResponse`，
  `Quote.text` 仍必填；不能把协议文件存在当作新适配器已接入原子协议。
- 两次旧运行的冻结指纹均与当前配置不匹配。`2f6940fa` 的检查点有 1795 个
  `RecognitionModelFailure` 任务结果（未形成实体关系），且查询时已在 `deleting` 状态；
  本次没有改写其指纹或撤销外部删除状态。`a484f2bf` 原暂停发生在首个 discovery 请求，
  尚无识别检查点；现按授权保留历史并取消，以新运行接替。
- 后继运行为 `58fcc303-c20b-4a47-9390-aad12c83b701`，复用相同源文件、本体及已冻结
  原文解析/元数据；没有预种候选或迁移识别检查点。通过现有租约和 worker 继续执行，
  正在验证，尚未判定轨道 A 通过。

### 7.1 条件分叉与后续修复

- 新运行形成了首批原文记录结果，但有效一级关系仍为空。有界原文诊断中，
  新适配器将产品介绍段的 `属于肿瘤药产品`、`目前项目处于I期临床阶段` 冻结为对象名称，
  独立核验虽支持部分关系，仍未把实体正确绑定到 HRS-5592，不能通过用户补充的金标准。
  此诊断是局部协议检查，不是整文召回率评测。原计划的另一诊断取用了 unit 56 的步骤
  heading，该类 heading 不在普通数据记录集合内；unit 52 的 `3.1.1合成路线图` 实际是
  paragraph，属于记录集合，不能笼统说路线标题被排除。
- 因此前置验收未通过，按约定执行 B1 → B2，模板面板继续消费旧域的正式结果，
  没有把分析制品伪装为已复核业务事实。后继运行通过正常控制暂停，保留图谱、费用和断点。
- B0 追加发现：线上短编号传输会过滤 `citation_feedback` / `source_quote_rules`。
  已保留这些字段并转换提示中的证据编号；新增实际短编号纠错回归通过。
- B1：正式 runner 接入共享 `CitationProtocol`，协议身份升级为
  `atomic-citations-v5-proposal-isolation`，上下文升级为 v7。整来源可只给 ID；
  非唯一子串、越权和跨格拼接继续拒绝。召回按完整提议隔离失败，验证不允许丢弃坏 span
  后接受剩余证明。协议进入缓存/运行身份，旧断点不能冒充新协议运行。
- B2：复用冻结 `RecordIndex` 生成完整逻辑行目标，合并单元格保留多个逻辑行身份；
  表头保持 binding-only。超预算记录明确记 `context_budget_exceeded`，不拆格后宣称完成。
  保留直接命名章节的实体识别机会，并让已验证一级分支的属性先于全文实体扫描得到检查。
- 属性字段组扩展仅作有界检索；名称字段匹配不证明身份，每个新增记录的属性必须通过
  原主体与当前记录双端的独立 `verify_reference`。分支投影补充属性处理进度，前端不把
  关系已识别当作属性全部完成。
- 尚需完成真实模板复测与上线后验收；以上工程改动和局部诊断不等同本模板金标准已达成。

### 7.2 已部署后的首轮真实复测

- 后端定向测试 152 passed，覆盖原子引用、合并逻辑行、超预算拒绝、有效兄弟提议保留、
  字段组双端归属、一级属性提前识别、旧抽取回归和共享内核边界；前端图谱测试 11 passed，
  TypeScript、定向 ESLint 与本次相关后端文件 Ruff 通过。
- 确认没有其他活动分析/标注运行及模型请求后，正常重启后端以加载代码。
  `/api/health` 为 200，实际数据库 revision 仍为 `0035_ranking_budget_control`。
  前端是挂载源码的开发服务，本次没有执行生产构建。
- 已通过原有 `_enqueue_annotation` / SQL 执行租约在原作业创建新运行
  `328877cbc64d404b972d237c12057e02`，冻结模板现有优先路径，以 48 个任务的安全边界进行
  首轮验证；没有预种金标候选。原检查点和缓存先备份到
  `data/uploads/template-atomic-recovery-20260909/previous/`，新回执同目录保存。
- 本段只记录实际启动与工程结果；DrugProduct、SynthesisRoute 等实体及属性的真实原文
  核对仍在进行，不能据此宣称已经达到用户的模板金标准。

### 7.3 正式复测暴露的后续问题

- `328877cbc64d404b972d237c12057e02` 经正常控制在 36 个任务、75 次模型调用后暂停，
  保留检查点。`describes → ClinicalTrialDrug/HRS-5592` 及注册分类、规格、项目名称通过；
  52 个不同原文 anchor 全部重放成功。路线、清洗分支仍未通过，该运行未达到模板金标准。
- 字段组归属请求只保留了重复名称，没有完整表单结构。补入“项目名称”完整字段仍不足以
  让模型判断跨段属性；现在复用 RecordIndex，完整字段组作为 binding-only 上下文，保留
  其他主体及限定字段，独立验证必须引用原主体、表单主体角色和当前属性双端原文。
  有界真实诊断 `owner-probe-415ec55325344fe4a2c5c23e61451740` 已识别并验证分子量
  `537.18` 归属 HRS-5592。诊断制品未直接写入正式候选。
- 路线不能仅靠孤立标题通过。对明确命名整体对象的短标题，按合法类型及其直接关系的
  类型标签检索同一父章节下的相关章节标题和首个完整记录；它们仅是 binding context。
  召回及独立类型验证仍须区分整体与单步，越权用背景标题提出实体继续拒绝。
- 清洗类型验证曾错误地要求设备全局编号，并以“属于其子类”为由拒绝声明父类。
  明确局部实例类型、继承和全局身份的不同契约，不从缺少唯一编号推出类型不成立。
  有界真实调用已通过 CleaningProcess 独立类型验证；正式关系/属性仍以新运行结果验收。
- 属性优先识别不再要求原文逐字出现属性 label；无标签命中时，每个谓词仍获得一个完整
  主体记录的检查机会。字段标题不能替代具体值；本体声明的原文依据属性可引用实际依据。
- 新协议为 `atomic-citations-v6-field-ownership`、上下文为
  `model-context-v10-structural-records`、识别版本为 `generic-semantic-v9`、标注缓存为 31。
  新身份防止旧候选的技术判定冒充此次结果，旧审核和制品继续保留。
- 浏览器核对发现默认源文档曾选成 HRS-1597；已优先采用模板默认 sourceJobId。另发现
  候选列表混入历史运行，而分支状态来自当前运行。新增只读 `view=latest_run`，面板使用
  当前快照中的候选 ID，并读取最新审核版本；默认完整历史查询及审计记录保留。
- 属性状态增加 `not_applicable`：当前本体的 CleaningProcess 没有声明数据属性，不能
  让该分支永久显示“属性排队”，也不为满足验收擅自增加属性定义。
- 相关工程回归实际运行 164 passed；前端图谱 11 passed，TypeScript 与相关源码 Ruff
  通过。定向 ESLint 无 error，template-slot-editor 存在原有 `meta.status` 依赖 warning。
  后续补充的正式运行和浏览器验收尚待记录；工程通过不代表金标准完成。

### 7.4 原文证明和条件复核补充

- 原子协议已定位的名称、身份键和值，不再被召回 `assertion_spans` 二次裁剪。原来同段中
  名称在前、类型证明在后会误报 `scope_violation`；修复后仍按原字段授权域精确回放。
- 可完整回放的证明改用仅含 `evidence_id` 的 `FactProof` / `BindingProof`，避免模型再次
  抄写时插入省略号。权限枚举与对应 Span 相同，非连续授权或无法由单个授权区间覆盖的
  来源仍需精确引用；显式错误 text 不会被静默丢弃。跨章节上下文不再提供单章节映射选项。
- 正式运行 `b3a93493f0854c6c853e9ee8fd60251b` 在 12 个任务、26 次模型调用后正常暂停。
  `describes → DrugProduct/HRS-5592`、注册分类、注册状态，以及
  `hasSynthesisRoute → SynthesisRoute/3.1.1合成路线图` 和 `processBasis` 通过；
  38 个不同 anchor 全部回放成功。浏览器等待正文加载后，产品与整体路线点击原文定位通过，
  无页面/API 错误，也未触发写操作。最初浏览器失败是检查在正文装载完成前超时，
  后端证据与预览的结构指纹相同，不是另一份源文档或证据 ID 被改挂。
- 此轮仍**未达完整金标准**：清洗 recall 把背景来源放入条件列表，独立验证返回肯定且
  无条件，旧代码却以 `verified_conditions or recalled_conditions` 保留误列条件。
  不能仅凭验证条件列表为空删除原条件；新增逐项 `condition_reviews`，要求每个原条件
  都有独立的索引、是否为真实条件及理由。缺项、重复或冲突拒绝；真实条件必须保留，
  明确判为非条件的来源及理由保存在绑定记录中。候选不再因未复核的背景“条件”长期卡住。
- 分支新增 `relation_checked`：已检查但仅有否定、条件、假设或未决结果，不能退回
  “等待关系验证”，也不能宣称存在有效肯定关系或全文无匹配。
- 当前协议 `atomic-citations-v8-condition-review`、识别版本 `generic-semantic-v10`、
  标注缓存 32；再次正式运行使用新身份，旧运行及断点保留。产品属性优先列表仅含本体
  `projectName`、`molecularFormula`、`molecularWeight`，用于用户要求的属性与归属验收；
  期待值、实体候选和参考原文没有预种到识别输入。
- 新条件复核当前有 14 个反例/正例回归。定向后端组共 253 项，首轮 251 passed、
  2 项协议版本断言待更新；更新后对应表格协议组 7 passed。前端图谱 11 passed，
  TypeScript、相关源码 Ruff 与定向 ESLint 通过。正式模板最终验收继续记录如下。
- `188031a5d76c4009b34c3da3610b8b44` 在第一个关系验证请求遇到 400：本机 llama.cpp
  不支持未带首尾锚点的 `pattern`。已用两个不含文档的微型 schema 请求复现：未锚定正则
  被拒绝，`maxItems=0` 正常；与[官方限制](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
  一致。非空白理由改由服务端等价验证，不改原文、语义输入或已通过实体。
  正常失败重试复用原检查点，并在派发前断言冻结 input_id 相同；累计调用与失败记录保留。
  补入空白理由反例后，条件/原子引用相关组实际 93 passed。
- 模型请求兼容性修复后的恢复执行标识为 `fe2577d08726436aab625e791ea6572a`，复用
  语义输入 `8ca960ba82727e4f17c33db6ab551af90e8d18e7def62aba96bd39c9067baea6`。
  随后核对发现验收脚本的三个产品属性用了错误的 `drug-development` 命名空间，
  因而没有实际优先执行。已纠正为权威本体的 `drug` 命名空间，并在派发前校验这些 IRI
  确属 DrugProduct 属性菜单。该运行正常暂停保留，不计作最终属性优先级验收。

### 7.5 模板核心一级关系验收通过

后续用户已要求正式迁移到新内核，实施契约见
[kernel-panel-migration.md](kernel-panel-migration.md)。本节仍保留为旧 B 路径的历史验收记录，
不能作为新内核已通过的证据。

2026-09-09 19:01 UTC，正式运行 `200789fc3ff6471b9b5a36156d5b0665` 正常暂停，
保存后的当前面板快照通过只读验收。采用 **B0 + B1 + B2**：旧 `GenericExtractionRunner`
接入共享 `CitationProtocol` 并复用 `RecordIndex`；**面板没有切换到 OntologyGuidedExecutor**。
精简结果见 [template-first-level-validation.json](template-first-level-validation.json)。

| 一级关系 | 正确对象 | 本次独立验证并显示的属性 |
|---|---|---|
| `describes` | `DrugProduct / HRS-5592` | 注册分类 `化1类新药`；项目名称 `HRS-5592`；分子式 `C26H25F2N7O2S`；分子量 `537.18` |
| `hasSynthesisRoute` | `SynthesisRoute / 3.1.1合成路线图`，整体路线 | `processBasis = 3.1.1合成路线图`，使用本体允许的源文章节依据 |
| `hasCleaningMethod` | `CleaningProcess`，反应釜纯化水清洗过程 | 当前本体未声明该类的数据属性，状态为 `not_applicable` |

验收源作业为 `885c6aec-9b09-49ec-a368-926100e93a02`，原文 SHA256 为
`2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436`，语义输入为
`0c1039a995141e834e5b92659b0f25d1a5b48582ce5a1e37c68ac0db33223384`。
优先级仅使用本体谓词；期待值和只读验收器没有进入模型输入，也没有把诊断候选写入正式结果。
没有把中间体分子量或 `N/A` CAS 挂到产品，没有把单步骤冒充整体路线；把标题作为
`processDescription` 的提议被拒绝，面板保留其“校验未通过”状态。

保存后核对的是 `view=latest_run` 对应的当前候选仓库版本：12 个实体、6 条关系、6 个属性
候选（含被拒绝项）。52 个不同原文 anchor 全部精确回放，来源 excerpt 和原文一致，
关系端点及依赖修订均为当前版本。三个核心分支均为 `identified`，产品显示 4 个属性任务和
4 个肯定属性候选，路线显示 2 个属性任务和 1 个肯定属性候选；所有分支继续明确记录覆盖未完成。

真实浏览器访问指定模板，默认选中 HRS-5592 源作业，确认读取当前运行视图及暂停状态。
3 个实体、3 条关系、5 个属性共 **11 次查看原文**均定位到正确证据 ID 和实际片段；
无 JavaScript 错误、API 错误或写操作。最终图谱组件的 ESLint、TypeScript 检查通过；
前述引用/条件相关回归 93 passed、表格协议组 7 passed、分支状态组 16 passed、
前端图谱 11 passed 是本次不同命令的结果，不累加成一个全量测试总数。

运行公开进度为处理 24 项、完成 21 项、失败 2 项，累计 53 次模型调用；SQL 请求台账为
52 次完成、1 次因正常暂停取消。暂停保留的失败分别为降解途径的极性冲突、共线评估数值
解析不支持，以及当时在途的生产风险关系取消；前两项没有进入有效肯定结果。
清洗残留类型也存在未通过校验的提议，其他未处理分支仍排队。这里通过的是上述核心一级
关系与属性验收，**不是整文覆盖、所有一级关系、PDE 或业务事实发布验收**。运行仍为
`paused` / `incomplete` 且可恢复，累计预算和断点保留，没有发布业务事实。

完整制品保存在后端数据卷的
`/app/data/uploads/template-atomic-recovery-20260909/gold-run-722e1617d840421f9dc29069c54ea146/`：
`receipt.json`、`requests.jsonl`、`final-checkpoint.json`、`final-projection.json`、
`acceptance.json`、`template-gold-panel.json`、11 项定位截图及制品/源码 SHA256 清单。
原文和完整工艺片段留在该运行目录，仓库仅保存精简验收结果。
两个验收脚本也已归档：容器内执行 `python validate_template_gold.py`（环境
`PYTHONPATH=/app`），主机执行 `node check-template-gold-panel.cjs --gold`；前者检查当前
面板状态，后者需要本机已有 Playwright、Chrome 和服务。后续恢复产生新结果后应另存验收制品，
不能覆盖本次冻结结果。最终活动运行/请求检查均为空，数据库 revision 仍为
`0035_ranking_budget_control`；本次没有数据库迁移或生产前端构建。
