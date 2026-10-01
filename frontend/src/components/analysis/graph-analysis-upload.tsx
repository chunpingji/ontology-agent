"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Loader2, Play, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createDocumentAnalysisRun,
  getAllClassesWithSignal,
  type OntologyClassFlat,
} from "@/lib/api";

function classLabel(item: OntologyClassFlat): string {
  return item.label || item.name || item.iri;
}

export function GraphAnalysisUpload({ starting, onStartingChange, onCreated }: {
  starting: boolean;
  onStartingChange: (starting: boolean) => void;
  onCreated: (runId: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [rootClassIri, setRootClassIri] = useState("");
  const [classSearch, setClassSearch] = useState("");
  const [catalog, setCatalog] = useState<{
    items: OntologyClassFlat[]; loading: boolean; error: string | null;
  }>({ items: [], loading: true, error: null });
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const requestKey = useRef<string | null>(null);
  const creationController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getAllClassesWithSignal(controller.signal).then((items) => {
      if (!controller.signal.aborted) setCatalog({
        items: [...items].sort((a, b) => classLabel(a).localeCompare(classLabel(b), "zh-Hans-CN")),
        loading: false, error: null,
      });
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setCatalog({
        items: [], loading: false,
        error: failure instanceof Error ? failure.message : "本体类型加载失败。",
      });
    });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => () => creationController.current?.abort(), []);

  const search = classSearch.trim().toLocaleLowerCase();
  const classOptions = catalog.items.filter((item) => item.iri === rootClassIri
    || [classLabel(item), item.name, item.iri].some((value) => value.toLocaleLowerCase().includes(search)));

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.currentTarget.files?.[0] ?? null;
    requestKey.current = null;
    setError(null);
    if (selected && !/\.(doc|docx)$/i.test(selected.name)) {
      event.currentTarget.value = "";
      setFile(null);
      setError("仅支持 .doc 或 .docx 文件。");
      return;
    }
    setFile(selected);
  };

  const start = async () => {
    if (!file || !rootClassIri || starting || creationController.current) return;
    const controller = new AbortController();
    creationController.current = controller;
    requestKey.current ??= globalThis.crypto?.randomUUID?.()
      ?? `graph-upload:${Date.now()}-${Math.random().toString(16).slice(2)}`;
    onStartingChange(true);
    setError(null);
    try {
      const receipt = await createDocumentAnalysisRun(
        file, rootClassIri, requestKey.current, "generate_summary", controller.signal,
      );
      if (!controller.signal.aborted) {
        requestKey.current = null;
        onCreated(receipt.recognition_run_id);
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(
        failure instanceof Error ? failure.message : "上传失败，请重试。",
      );
    } finally {
      if (creationController.current === controller) {
        creationController.current = null;
        onStartingChange(false);
      }
    }
  };

  return <div className="space-y-4">
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
      <div className="min-w-0 space-y-2">
        <label htmlFor="graph-analysis-file" className="text-sm font-medium">Word 文件</label>
        <Input id="graph-analysis-file" type="file"
          accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          disabled={starting} onChange={chooseFile} />
        <p className="text-xs text-muted-foreground">支持 .doc / .docx，选择文件后点击“开始分析”上传。</p>
      </div>
      <div className="min-w-0 space-y-2">
        <label htmlFor="graph-analysis-root-class" className="text-sm font-medium">本体根类型（必选）</label>
        <Input aria-label="搜索本体根类型" placeholder="搜索类型标签、名称或完整 IRI"
          value={classSearch} disabled={starting} onChange={(event) => setClassSearch(event.target.value)} />
        <select id="graph-analysis-root-class" value={rootClassIri}
          disabled={starting || catalog.loading || Boolean(catalog.error)}
          onChange={(event) => {
            setRootClassIri(event.target.value);
            requestKey.current = null;
            setError(null);
          }} className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm">
          <option value="">{catalog.loading ? "正在读取本体类型…" : "请选择根类型"}</option>
          {classOptions.map((item) => <option key={item.iri} value={item.iri}>{classLabel(item)} · {item.iri}</option>)}
        </select>
      </div>
      <Button disabled={!file || !rootClassIri || starting || catalog.loading || Boolean(catalog.error)}
        onClick={() => void start()}>
        {starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
        {starting ? "正在上传并创建分析" : "开始分析"}
      </Button>
    </div>
    {catalog.error && <div className="flex flex-wrap items-center gap-2">
      <p role="alert" className="break-words text-sm text-destructive">本体类型加载失败：{catalog.error}</p>
      <Button size="sm" variant="outline" onClick={() => {
        setCatalog({ items: [], loading: true, error: null });
        setRetry((value) => value + 1);
      }}><RefreshCw className="size-3.5" />重新读取本体类型</Button>
    </div>}
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
  </div>;
}
