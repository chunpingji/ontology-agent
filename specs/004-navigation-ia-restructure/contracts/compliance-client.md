# Client Contract: 审批中心复用的合规端点

审批中心**不新增后端能力**。本契约登记其在 `frontend/src/lib/api.ts` 绑定的端点。三项**已存在**,两项**待新增绑定**(指向 003 已交付的后端端点,见 `backend/app/api/compliance.py`)。所有端点经 nginx 同源 `/api` 前缀;身份经 `X-User`/`X-Role` 头注入。

## 既有绑定(直接复用,无需改动)

| 客户端函数 | 方法 / 路径 | 角色 | 响应要点 |
|------------|-------------|:--:|----------|
| `verifyAudit()` | `GET /api/compliance/audit/verify` | senior_analyst \| qa | `{ ok, verified_count?, head_seq?, broken_at_seq? }` |
| `getPendingSignatures()` | `GET /api/compliance/signatures/pending` | qa | `{ conclusions: PendingConclusion[] }` |
| `signConclusion(req)` | `POST /api/compliance/signatures` | qa | `{ signature_id, conclusion_id, effective, signed_at }`;201 |

## 待新增绑定(指向既有后端端点)

### `rejectConclusion(req)` → `POST /api/compliance/reject`

后端:`compliance.reject_conclusion`(`require_role(qa)`)。

**Request**
```ts
interface RejectRequest {
  conclusion_id: string;  // UUID
  username: string;
  password: string;       // Part 11 重认证
  reason: string;
}
```
**Response 201**
```ts
interface RejectResponse {
  conclusion_id: string;
  lifecycle_state: string;  // "rejected"
  voided_actions: number;   // 被作废的非终态动作数
}
```
**错误**:`401` 重认证失败;`409` 非待签态(终态不可再拒绝);`404` 结论不存在。

### `getComplianceAudit(params?)` → `GET /api/compliance/audit`

后端:`compliance.list_audit`(`require_role(senior_analyst, qa)`),append-only 只读。

**Query(可选)**:`actor` / `action` / `entity_iri`
**Response 200**
```ts
interface ComplianceAuditEntry {
  seq: number | null;
  action: string;
  actor: string | null;
  entity_iri: string | null;
  prev_hash: string | null;
  entry_hash: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
}
interface ComplianceAuditListResponse { entries: ComplianceAuditEntry[] }
```

> 注意:`lib/api.ts` 既有的 `getAudit()` 指向的是 `/api/ontology/audit`(本体审计),与此处合规审计链 `/api/compliance/audit` 不同,需分别绑定,勿混用。

## 复用断言(FR-014 / Constitution IV)

- 上述端点的 **request/response 形状、状态码、角色门禁均不改动**;本特性仅在前端新增 2 个 `fetch` 封装。
- 后端 `compliance.py` 与其 pytest **0 改动**;审批中心是既有端点的组合编排。
