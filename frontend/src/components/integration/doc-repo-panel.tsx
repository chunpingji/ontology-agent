"use client";

import { useCallback, useEffect, useState } from "react";
import {
  DEVELOPMENT_PHASES,
  DOCUMENT_NS,
  connectorDocIris,
  createDocRepoConnector,
  createExtractionConfig,
  deleteConnector,
  docRepoMode,
  docTypeLabel,
  enqueueDocumentExtraction,
  getClassHierarchy,
  getModules,
  listDocRepoConnectors,
  listDocuments,
  listExtractedFrom,
  listExtractionConfigs,
  phaseLabel,
  startExtractionJob,
  syncConnector,
  testConnector,
  webhookConnector,
  type Connector,
  type DocRepoAccessMode,
  type EntityShadow,
  type ExtractionConfig,
  type Module,
  type TreeNode,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

type ModeMeta = { value: DocRepoAccessMode; title: string; short: string; hint: string };

const MODES: ModeMeta[] = [
  { value: "inline", title: "确定性测试", short: "inline", hint: "从内联变更读取——演示 / 契约测试用。" },
  { value: "upload", title: "过渡导入", short: "upload", hint: "真实系统就位前，经上传登记文档「记录层」元数据。" },
  { value: "http", title: "生产接入", short: "http", hint: "经 base_url + env 注入凭据拉取真实 EDMS / eTMF。" },
];

/** 7 类研发文档（与 DOC_TYPE_LABELS 同源）。 */
const DOC_TYPE_OPTIONS: Array<[string, string]> = [
  ["RegulatoryDocument", "法规文档"],
  ["INDDossier", "IND 申报资料"],
  ["TechTransferReport", "技术转移报告"],
  ["ProcessValidationReport", "工艺验证报告"],
  ["StabilityReport", "稳定性报告"],
  ["NDA_BLADossier", "NDA/BLA 申报资料"],
  ["PVReport", "药物警戒报告"],
];

const APPROVAL_STATES = ["draft", "approved", "superseded", "withdrawn"];

/** inline 示例变更：技术转移报告 v2 / 临床Ⅰ期 / 已批准（与后端确定性夹具同形）。 */
const SAMPLE_INLINE_CHANGE: Record<string, unknown> = {
  entity_id: "doc-TTR-001",
  entity_type: "TechTransferReport",
  version: 2,
  label: "XX 项目技术转移报告",
  fields: {
    hasDevelopmentPhase: `${DOCUMENT_NS}Phase_ClinicalI`,
    documentVersion: "2",
    approvalStatus: "approved",
    sourceSystem: "EDMS-A",
    contentHash: "sha256:1f3b9cda2e",
    externalRef: "edms://doc/TTR-001/v2",
  },
};

function statusVariant(status: string | null | undefined): "success" | "warning" | "secondary" {
  if (status === "approved") return "success";
  if (status === "superseded" || status === "withdrawn") return "warning";
  return "secondary";
}

/** 由文件名派生稳定的 doc_id（→ facts# 个体 IRI；同名再传＝同一记录的生命周期更新）。 */
function sanitizeId(filename: string): string {
  return (
    filename
      .replace(/\.[^.]+$/, "")
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "doc"
  );
}

/** 浏览器内计算文件内容指纹（SHA-256，ALCOA+ 完整性）；文件正文不离开本地、不入库。 */
async function sha256Hex(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** 按研发阶段（受控词表定序）分组文档；无阶段者归入末尾「未标注阶段」。 */
function groupByPhase(docs: EntityShadow[]): Array<{ key: string; label: string; docs: EntityShadow[] }> {
  const byIri = new Map<string, EntityShadow[]>();
  for (const d of docs) {
    const ph = (d.properties_json?.hasDevelopmentPhase as string) || "";
    byIri.set(ph, [...(byIri.get(ph) || []), d]);
  }
  const groups: Array<{ key: string; label: string; docs: EntityShadow[] }> = [];
  for (const p of DEVELOPMENT_PHASES) {
    const ds = byIri.get(p.iri);
    if (ds?.length) groups.push({ key: p.iri, label: `${p.notation}. ${p.label}`, docs: ds });
  }
  const rest: EntityShadow[] = [];
  for (const [iri, ds] of byIri) {
    if (!DEVELOPMENT_PHASES.some((p) => p.iri === iri)) rest.push(...ds);
  }
  if (rest.length) groups.push({ key: "_none", label: "未标注阶段", docs: rest });
  return groups;
}

/** 类层级树 → 扁平选项（缩进体现层级），供目标类下拉选择。 */
function flattenClasses(tree: TreeNode[]): Array<{ iri: string; label: string }> {
  const out: Array<{ iri: string; label: string }> = [];
  const walk = (nodes: TreeNode[], depth: number) => {
    for (const n of nodes) {
      out.push({ iri: n.iri, label: `${"　".repeat(depth)}${n.label || n.name}` });
      if (n.children?.length) walk(n.children, depth + 1);
    }
  };
  walk(tree, 0);
  return out;
}

/**
 * 文档内容抽取发起器（007 US2）。对一份已物化的文档个体**人工发起**内容抽取：
 * 选择/新建 doc_repo 抽取配置（目标类）→ 入队并 start → 候选进入既有对齐复核队列，
 * 分析师确认后入事实层并携 extractedFrom 回链。复用既有 /extraction 端点与复核 UI（宪章 V）。
 */
function DocExtractLauncher({ doc }: { doc: EntityShadow }) {
  const [configs, setConfigs] = useState<ExtractionConfig[]>([]);
  const [configId, setConfigId] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [modules, setModules] = useState<Module[]>([]);
  const [classOpts, setClassOpts] = useState<Array<{ iri: string; label: string }>>([]);
  const [ncName, setNcName] = useState("");
  const [ncModule, setNcModule] = useState("");
  const [ncClass, setNcClass] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; status: string } | null>(null);

  useEffect(() => {
    void listExtractionConfigs()
      .then((cs) => {
        const docCfgs = cs.filter((c) => c.source_type === "doc_repo");
        setConfigs(docCfgs);
        if (docCfgs.length) setConfigId(docCfgs[0].id);
        else setShowNew(true);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const openNew = () => {
    setShowNew(true);
    if (modules.length === 0) void getModules().then(setModules).catch((e) => setErr(String(e)));
  };

  const onModuleChange = (m: string) => {
    setNcModule(m);
    setNcClass("");
    setClassOpts([]);
    void getClassHierarchy(m)
      .then((tree) => setClassOpts(flattenClasses(tree)))
      .catch((e) => setErr(String(e)));
  };

  const onCreateConfig = async () => {
    if (!ncName.trim() || !ncClass) {
      setErr("请填写配置名称并选择目标类。");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const cfg = await createExtractionConfig({
        name: ncName.trim(),
        target_class_iri: ncClass,
        source_type: "doc_repo",
      });
      setConfigs((prev) => [cfg, ...prev]);
      setConfigId(cfg.id);
      setShowNew(false);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onLaunch = async () => {
    if (!configId) {
      setErr("请先选择或新建抽取配置。");
      return;
    }
    setBusy(true);
    setErr(null);
    setJob(null);
    try {
      const contentRef = String(doc.properties_json?.externalRef ?? doc.iri);
      // 人工发起＝入队 + start：候选进入对齐复核队列，不自动断言为权威事实（Q1/复核门禁）。
      const enq = await enqueueDocumentExtraction({
        doc_ref: doc.iri,
        content_ref: contentRef,
        config_id: configId,
      });
      const started = await startExtractionJob(enq.id);
      setJob({ id: started.id, status: started.status });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 space-y-2 rounded border border-dashed p-2">
      <p className="text-xs font-medium">发起内容抽取（能力二 · US2）</p>
      <p className="text-xs text-muted-foreground">
        从本文档抽取业务实体（药物 / 备样 / 质量标准等）为<strong>待复核候选</strong>，
        经分析师确认后方入事实层，并携溯源回链本文档。复核门禁不被削弱（Q1）。
        正文经文档外部引用按需读取（平台不存全文，Q2）；http 接入的文档可回取正文，
        手动上传的文档若无可回取正文源则不产出候选。
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={configId} onValueChange={setConfigId} disabled={configs.length === 0}>
          <SelectTrigger className="h-8 w-56 text-xs">
            <SelectValue placeholder={configs.length ? "选择抽取配置…" : "暂无 doc_repo 配置"} />
          </SelectTrigger>
          <SelectContent>
            {configs.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}（{docTypeLabel(c.target_class_iri)}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" className="h-8" onClick={openNew} disabled={busy}>
          + 新建配置
        </Button>
        <Button size="sm" className="h-8" onClick={onLaunch} disabled={busy || !configId}>
          {busy ? "处理中…" : "发起抽取"}
        </Button>
      </div>

      {showNew && (
        <div className="space-y-2 rounded border border-dashed p-2">
          <p className="text-xs font-medium">新建 doc_repo 抽取配置</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">配置名称</Label>
              <Input
                className="h-8 w-44 text-xs"
                value={ncName}
                onChange={(e) => setNcName(e.target.value)}
                placeholder="如：技术转移报告-药物抽取"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">模块</Label>
              <Select value={ncModule} onValueChange={onModuleChange}>
                <SelectTrigger className="h-8 w-36 text-xs">
                  <SelectValue placeholder="选择模块…" />
                </SelectTrigger>
                <SelectContent>
                  {modules.map((m) => (
                    <SelectItem key={m.key} value={m.key}>
                      {m.label || m.key}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">目标类</Label>
              <Select value={ncClass} onValueChange={setNcClass} disabled={classOpts.length === 0}>
                <SelectTrigger className="h-8 w-52 text-xs">
                  <SelectValue placeholder={ncModule ? "选择目标类…" : "先选模块"} />
                </SelectTrigger>
                <SelectContent>
                  {classOpts.map((c) => (
                    <SelectItem key={c.iri} value={c.iri}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button size="sm" className="h-8" onClick={onCreateConfig} disabled={busy}>
              创建并选用
            </Button>
          </div>
        </div>
      )}

      {err && <p className="text-xs text-destructive">{err}</p>}
      {job && (
        <p className="text-xs text-muted-foreground">
          已发起抽取作业
          <span className="mx-1 font-mono">{job.id.slice(0, 8)}</span>（{job.status}）。候选将进入对齐复核队列 ——
          <a className="ml-1 text-primary underline" href="/entities/extraction">
            前往复核 →
          </a>
        </p>
      )}
    </div>
  );
}

/**
 * 研发文档事实源（007）面板。缺省呈现三类接入模式卡片（inline/upload/http）；
 * 点击卡片打开右侧抽屉，展示该模式连接器 + 按研发阶段划分的文档溯源；
 * 文档上传仅在 upload 卡片抽屉内提供。复用既有 connector CRUD 与 /api/entities（宪章 V）。
 */
export function DocRepoPanel() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [docs, setDocs] = useState<EntityShadow[]>([]);
  const [modeIris, setModeIris] = useState<Record<DocRepoAccessMode, Set<string>>>({
    inline: new Set(),
    upload: new Set(),
    http: new Set(),
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // --- 抽屉 + 溯源展开 ---
  const [openMode, setOpenMode] = useState<DocRepoAccessMode | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [derived, setDerived] = useState<Record<string, EntityShadow[]>>({});

  // --- 新建对话框 ---
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("研发文档事实源");
  const [accessMode, setAccessMode] = useState<DocRepoAccessMode>("http");
  const [baseUrl, setBaseUrl] = useState("https://edms.internal/api/changes");
  const [tokenRef, setTokenRef] = useState("EDMS_TOKEN");
  const [seedSample, setSeedSample] = useState(true);

  // --- upload 过渡导入（文件 → 记录层元数据信封） ---
  const [upDocType, setUpDocType] = useState("TechTransferReport");
  const [upPhase, setUpPhase] = useState(`${DOCUMENT_NS}Phase_ClinicalI`);
  const [upVersion, setUpVersion] = useState("1");
  const [upStatus, setUpStatus] = useState("approved");
  const [upSource, setUpSource] = useState("manual-upload");
  const [staged, setStaged] = useState<Record<string, unknown>[]>([]);
  const [importing, setImporting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [conns, docRes] = await Promise.all([listDocRepoConnectors(), listDocuments()]);
      setError(null);
      setConnectors(conns);
      setDocs(docRes.items);
      // 文档→模式归属：经各连接器 run 留痕只读重建（EntityShadow 不存连接器字段）。
      const map: Record<DocRepoAccessMode, Set<string>> = {
        inline: new Set(),
        upload: new Set(),
        http: new Set(),
      };
      await Promise.all(
        conns.map(async (c) => {
          const mode = docRepoMode(c);
          try {
            (await connectorDocIris(c.id)).forEach((iri) => map[mode].add(iri));
          } catch {
            /* 单连接器留痕读取失败不影响整体归属 */
          }
        }),
      );
      setModeIris(map);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  // 挂载即加载。经 microtask 间接调用，setState 落在 promise 回调内（避免 effect 内同步置态）。
  useEffect(() => {
    void Promise.resolve().then(refresh);
  }, [refresh]);

  const modeConnectors = (m: DocRepoAccessMode) => connectors.filter((c) => docRepoMode(c) === m);
  const modeDocs = (m: DocRepoAccessMode) => docs.filter((d) => modeIris[m].has(d.iri));

  const onCreate = async () => {
    setError(null);
    try {
      await createDocRepoConnector({
        name,
        accessMode,
        baseUrl: accessMode === "http" ? baseUrl : undefined,
        tokenRef: accessMode === "http" ? tokenRef : undefined,
        inlineChanges:
          accessMode === "inline" && seedSample ? [SAMPLE_INLINE_CHANGE] : undefined,
      });
      setCreateOpen(false);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const onTest = async (id: string) => {
    setBusy(id);
    try {
      const r = await testConnector(id);
      alert(r.ok ? `连接正常 (${r.latency_ms ?? "—"}ms)` : `连接失败：${r.error}`);
    } finally {
      setBusy(null);
    }
  };

  const onSync = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      await syncConnector(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("确认删除该 doc_repo 连接器？")) return;
    await deleteConnector(id);
    refresh();
  };

  /** 选中文件 → 计算 SHA-256、按当前元数据构建 upload 信封并暂存（正文不上传）。 */
  const onStageFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      const hex = await sha256Hex(file);
      const envelope: Record<string, unknown> = {
        doc_id: sanitizeId(file.name),
        doc_type: upDocType,
        version: Number(upVersion) || 1,
        title: file.name,
        metadata: {
          hasDevelopmentPhase: upPhase,
          documentVersion: upVersion || "1",
          approvalStatus: upStatus,
          sourceSystem: upSource || "manual-upload",
          contentHash: `sha256:${hex}`,
          externalRef: `upload://${file.name}`,
        },
      };
      setStaged((prev) => [...prev, envelope]);
    } catch (e) {
      setError(String(e));
    }
  };

  /**
   * 把暂存文档**累积**进**同一个** upload 连接器（无该模式连接器时才惰性新建一次），
   * 经 webhook 增量推送已归一化骨架（追加 inline_changes 并即时同步）——不再每次上传新建连接器。
   * 传输层 `version` 取单调递增的批次时间戳：连接器的水位游标据此推进，重复同步幂等
   * （物化按 entity_id 去重；同名文档再传＝该记录的生命周期更新）。文档版本另记于 fields.documentVersion。
   */
  const onImportUpload = async () => {
    if (staged.length === 0) return;
    setImporting(true);
    setError(null);
    try {
      const existing = modeConnectors("upload");
      const conn =
        existing[0] ??
        (await createDocRepoConnector({ name: "研发文档上传源", accessMode: "upload" }));
      const batchVersion = Date.now(); // 单调递增传输水位（同批共用 → 物化按 entity_id 各自生效）。
      const changes = staged.map((s) => ({
        entity_id: s.doc_id,
        entity_type: s.doc_type,
        version: batchVersion,
        label: s.title,
        fields: { ...((s.metadata ?? {}) as Record<string, unknown>) },
      }));
      await webhookConnector(conn.id, changes);
      setStaged([]);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setImporting(false);
    }
  };

  const onToggleProvenance = async (doc: EntityShadow) => {
    if (expanded === doc.iri) {
      setExpanded(null);
      return;
    }
    setExpanded(doc.iri);
    if (!derived[doc.iri]) {
      const ents = await listExtractedFrom(doc.iri);
      setDerived((prev) => ({ ...prev, [doc.iri]: ents }));
    }
  };

  const drawerMeta = MODES.find((m) => m.value === openMode) ?? null;

  return (
    <div className="space-y-4">
      {/* === 标题 + 右上角新建 === */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">研发文档事实源</h2>
          <p className="text-sm text-muted-foreground">
            三类接入模式按卡片呈现；点击卡片查看其按研发阶段划分的文档溯源。
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => {
            setError(null);
            setCreateOpen(true);
          }}
        >
          + 新建
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {/* === 三类接入模式卡片 === */}
      <div className="grid gap-3 sm:grid-cols-3">
        {MODES.map((m) => {
          const cs = modeConnectors(m.value);
          const ds = modeDocs(m.value);
          const errs = cs.filter((c) => c.last_status && c.last_status !== "success").length;
          return (
            <Card
              key={m.value}
              role="button"
              tabIndex={0}
              onClick={() => setOpenMode(m.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpenMode(m.value);
                }
              }}
              className="cursor-pointer p-4 transition-colors hover:border-primary/50 hover:bg-muted/40"
            >
              <CardContent className="space-y-2 p-0">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{m.title}</h3>
                  <Badge variant="outline" className="text-xs">
                    {m.short}
                  </Badge>
                </div>
                <p className="min-h-[2.5rem] text-xs text-muted-foreground">{m.hint}</p>
                <div className="flex items-center gap-2 text-sm">
                  <span>
                    <span className="font-semibold">{cs.length}</span> 连接器
                  </span>
                  <span className="text-muted-foreground">·</span>
                  <span>
                    <span className="font-semibold">{ds.length}</span> 文档
                  </span>
                  {errs > 0 && (
                    <Badge variant="warning" className="ml-auto">
                      {errs} 异常
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-primary">查看文档溯源 →</p>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* === 新建连接器对话框 === */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建研发文档事实源（doc_repo）</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">名称</Label>
              <Input className="text-sm" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">接入模式</Label>
              <Select value={accessMode} onValueChange={(v) => setAccessMode(v as DocRepoAccessMode)}>
                <SelectTrigger className="text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.title}（{m.short}）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {accessMode === "http" && (
              <div className="space-y-3 rounded-md border border-dashed p-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">base_url（EDMS/eTMF 端点）</Label>
                  <Input
                    className="text-sm"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">凭据环境变量名（token_ref）</Label>
                  <Input
                    className="text-sm"
                    value={tokenRef}
                    onChange={(e) => setTokenRef(e.target.value)}
                    placeholder="EDMS_TOKEN"
                  />
                </div>
                <p className="text-xs text-warning-foreground">
                  ⚠ 仅填写凭据的<strong>环境变量名</strong>；明文 token/密钥经 env
                  注入，绝不入库或提交（FR-010）。
                </p>
              </div>
            )}

            {accessMode === "inline" && (
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={seedSample}
                  onChange={(e) => setSeedSample(e.target.checked)}
                />
                填入示例变更（技术转移报告 v2 / 临床Ⅰ期 / 已批准）
              </label>
            )}

            {accessMode === "upload" && (
              <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
                upload 连接器创建后，在「过渡导入」卡片抽屉中选择文件上传文档（仅登记记录层元数据，Q5）。
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button size="sm" onClick={onCreate}>
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* === 模式抽屉：连接器 + （upload 上传）+ 按阶段文档 === */}
      <Sheet open={openMode !== null} onOpenChange={(o) => !o && setOpenMode(null)}>
        <SheetContent className="overflow-y-auto">
          {openMode && drawerMeta && (
            <>
              <SheetHeader>
                <SheetTitle>
                  {drawerMeta.title}（{drawerMeta.short}）
                </SheetTitle>
                <SheetDescription>{drawerMeta.hint}</SheetDescription>
              </SheetHeader>

              {/* 连接器 */}
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">连接器（{modeConnectors(openMode).length}）</h4>
                {modeConnectors(openMode).length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    暂无该模式连接器
                    {openMode === "upload" ? "——可在下方上传文档直接创建。" : "——点击右上角「+ 新建」。"}
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {modeConnectors(openMode).map((c) => (
                      <li
                        key={c.id}
                        className="flex items-center justify-between gap-2 rounded border px-2 py-1.5"
                      >
                        <div className="min-w-0">
                          <span className="text-sm font-medium">{c.name}</span>
                          {c.last_status && (
                            <Badge
                              variant={c.last_status === "success" ? "success" : "warning"}
                              className="ml-2"
                            >
                              {c.last_status}
                            </Badge>
                          )}
                          {c.last_error && (
                            <span className="ml-2 text-xs text-destructive">{c.last_error}</span>
                          )}
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy === c.id}
                            onClick={() => onTest(c.id)}
                          >
                            探活
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy === c.id}
                            onClick={() => onSync(c.id)}
                          >
                            同步
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="border-destructive/40 text-destructive"
                            onClick={() => onDelete(c.id)}
                          >
                            删除
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* upload 过渡导入 */}
              {openMode === "upload" && (
                <section className="space-y-3 rounded-md border border-dashed p-3">
                  <h4 className="text-sm font-semibold">上传导入</h4>
                  <p className="text-xs text-muted-foreground">
                    先设定下方元数据，再选择文档文件即登记其<strong>记录层</strong>元数据（文件名 +
                    浏览器内计算的 SHA-256 指纹）。所有上传<strong>累积进同一个上传事实源</strong>
                    （不再每次新建连接器）。平台不存储文件正文（仅元数据 + 外部引用，Q5）；
                    正文如需结构化抽取，经能力二人工发起（US2）。
                  </p>
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">文档类型</Label>
                      <Select value={upDocType} onValueChange={setUpDocType}>
                        <SelectTrigger className="w-44 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {DOC_TYPE_OPTIONS.map(([k, v]) => (
                            <SelectItem key={k} value={k}>
                              {v}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">研发阶段</Label>
                      <Select value={upPhase} onValueChange={setUpPhase}>
                        <SelectTrigger className="w-40 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {DEVELOPMENT_PHASES.map((p) => (
                            <SelectItem key={p.iri} value={p.iri}>
                              {p.notation}. {p.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">版本</Label>
                      <Input
                        className="w-20 text-sm"
                        value={upVersion}
                        onChange={(e) => setUpVersion(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">审批状态</Label>
                      <Select value={upStatus} onValueChange={setUpStatus}>
                        <SelectTrigger className="w-28 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {APPROVAL_STATES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {s}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">来源系统</Label>
                      <Input
                        className="w-32 text-sm"
                        value={upSource}
                        onChange={(e) => setUpSource(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">
                      选择文档文件（按上面元数据登记，可多次添加）
                    </Label>
                    <Input
                      type="file"
                      className="w-full text-sm"
                      onChange={(e) => {
                        void onStageFile(e.target.files?.[0]);
                        e.target.value = "";
                      }}
                    />
                  </div>
                  {staged.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs font-medium">待导入（{staged.length}）：</p>
                      <ul className="space-y-0.5 text-xs">
                        {staged.map((s, i) => {
                          const meta = (s.metadata ?? {}) as Record<string, unknown>;
                          return (
                            <li key={i} className="flex flex-wrap items-center gap-2">
                              <span className="font-medium">{String(s.title)}</span>
                              <Badge variant="outline">{docTypeLabel(String(s.doc_type))}</Badge>
                              <Badge variant="outline">
                                {phaseLabel(String(meta.hasDevelopmentPhase ?? ""))}
                              </Badge>
                              <span className="font-mono text-muted-foreground">
                                {String(meta.contentHash ?? "")}
                              </span>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-auto px-1 py-0 text-xs text-destructive"
                                onClick={() => setStaged((prev) => prev.filter((_, j) => j !== i))}
                              >
                                移除
                              </Button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                  <Button
                    size="sm"
                    disabled={staged.length === 0 || importing}
                    onClick={onImportUpload}
                  >
                    {importing
                      ? "导入中…"
                      : `导入到上传源（${staged.length}）${
                          modeConnectors("upload").length === 0 ? "——首次将自动创建" : ""
                        }`}
                  </Button>
                </section>
              )}

              {/* 文档（按研发阶段） */}
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">
                  文档（{modeDocs(openMode).length}，按研发阶段）
                </h4>
                {groupByPhase(modeDocs(openMode)).length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    暂无文档——创建连接器并「同步」后在此呈现。
                  </p>
                ) : (
                  groupByPhase(modeDocs(openMode)).map((g) => (
                    <div key={g.key} className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">
                        {g.label}（{g.docs.length}）
                      </p>
                      <ul className="space-y-1">
                        {g.docs.map((d) => {
                          const p = d.properties_json || {};
                          return (
                            <li key={d.iri} className="rounded border px-2 py-1.5">
                              <div className="flex flex-wrap items-center gap-2 text-sm">
                                <span className="font-medium">
                                  {d.label_zh || d.iri.split(/[#/]/).pop()}
                                </span>
                                <Badge variant="outline">{docTypeLabel(d.class_iri)}</Badge>
                                <Badge variant={statusVariant(p.approvalStatus as string)}>
                                  {(p.approvalStatus as string) || "—"}
                                </Badge>
                                <span className="text-xs text-muted-foreground">
                                  v{(p.documentVersion as string) || "—"}
                                </span>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="ml-auto h-auto px-2 py-0.5 text-xs"
                                  onClick={() => onToggleProvenance(d)}
                                >
                                  {expanded === d.iri ? "收起" : "溯源"}
                                </Button>
                              </div>
                              <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground">
                                <span>来源：{(p.sourceSystem as string) || "—"}</span>
                                <span className="font-mono">{(p.contentHash as string) || "—"}</span>
                              </div>
                              {expanded === d.iri && (
                                <div className="mt-1 border-t pt-1">
                                  <p className="mb-1 text-xs text-muted-foreground">
                                    抽取自本文档的派生实体（extractedFrom 回链）：
                                  </p>
                                  {(derived[d.iri] || []).length === 0 ? (
                                    <p className="text-xs text-muted-foreground">
                                      暂无派生实体——可在下方发起内容抽取（US2）。
                                    </p>
                                  ) : (
                                    <ul className="space-y-0.5 text-xs">
                                      {(derived[d.iri] || []).map((e) => (
                                        <li key={e.iri}>
                                          · {e.label_zh || e.iri.split(/[#/]/).pop()}
                                          <span className="ml-1 text-muted-foreground">
                                            {docTypeLabel(e.class_iri)}
                                          </span>
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                  <DocExtractLauncher doc={d} />
                                </div>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  ))
                )}
              </section>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
