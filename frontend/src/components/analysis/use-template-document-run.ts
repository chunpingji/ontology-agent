"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  controlDocumentAnalysisRun, createTemplateDocumentRun, getDocumentAnalysisGraph,
  getDocumentAnalysisRankingSummary, getDocumentAnalysisSource, getDocumentAnalysisSourceSelection,
  getIdentity, getTemplateDocumentRun, shouldSubscribeDocumentAnalysisEvents,
  createReportDocumentRun, getReportDocumentRun, getDocumentAnalysisMetadata,
  subscribeDocumentAnalysisEvents, VersionConflictError, type DocumentAnalysisControlAction,
  type DocumentAnalysisRun, type DocumentAnalysisSourceArtifact,
  type DocumentAnalysisMetadataArtifact,
  type DocumentAnalysisSourceSelectionArtifact, type DocumentGraphProjection,
} from "@/lib/api";

/** Serial, coalesced reads while connected; bounded backoff only while disconnected. */
export function createRunRefreshScheduler(refresh: () => Promise<unknown>) {
  let stopped = false;
  let connected = false;
  let fetching = false;
  let dirty = false;
  let delay = 2500;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = (milliseconds: number) => {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = undefined; void flush(); }, milliseconds);
  };
  const flush = async () => {
    if (stopped) return;
    if (fetching) { dirty = true; return; }
    fetching = true;
    dirty = false;
    try { await refresh(); } catch { /* Query state owns the readable error. */ }
    finally {
      fetching = false;
      if (!stopped && dirty) schedule(500);
      else if (!stopped && !connected) {
        delay = Math.min(delay * 2, 30000);
        schedule(delay);
      }
    }
  };
  schedule(delay);
  return {
    event() {
      if (stopped) return;
      const alreadyQueued = dirty;
      dirty = true;
      if (!fetching && !alreadyQueued) schedule(500);
    },
    connection(value: boolean) {
      if (stopped || value === connected) return;
      connected = value;
      clearTimeout(timer);
      timer = undefined;
      delay = 2500;
      if (connected) {
        // A reconnect may have crossed an artifact commit without a live frame.
        dirty = true;
        if (!fetching) schedule(500);
      } else if (!fetching) schedule(delay);
    },
    close() { stopped = true; clearTimeout(timer); },
  };
}

const subscribeVisibility = (notify: () => void) => {
  document.addEventListener("visibilitychange", notify);
  return () => document.removeEventListener("visibilitychange", notify);
};
const readVisibility = () => document.visibilityState !== "hidden";

export function sourceDocumentIdentity(source: DocumentAnalysisSourceSelectionArtifact): string {
  return JSON.stringify([source.recognition_run_id, source.analysis_id,
    source.document_hash, source.structure_hash]);
}

export function matchingSourceSelection(
  source: DocumentAnalysisSourceArtifact | undefined,
  selection: DocumentAnalysisSourceSelectionArtifact | undefined,
  selectionRef: string | undefined,
): DocumentAnalysisSourceSelectionArtifact | undefined {
  return source && selection && selection.selection?.selection_ref === selectionRef
    && sourceDocumentIdentity(source) === sourceDocumentIdentity(selection) ? selection : undefined;
}

/** Run ownership and immutable content are isolated by document and caller. GET never starts work. */
function useSourceDocumentRun(
  templateId?: string, jobId?: string | null, panelVisible = true, documentIri?: string,
) {
  const client = useQueryClient();
  const { username, role } = getIdentity();
  const pageVisible = useSyncExternalStore(subscribeVisibility, readVisibility, () => true);
  const visible = panelVisible && pageVisible;
  const [projection, setProjection] = useState<DocumentGraphProjection>("effective_affirmed");
  const [selection, setSelection] = useState<{ runId: string; ref: string } | null>(null);
  const requestKey = useRef<{ source: string; key: string } | null>(null);
  const sourceKey = JSON.stringify([templateId, jobId, documentIri, username, role]);
  const queryKey = useMemo(() => documentIri
    ? ["report-document-run", documentIri, username, role]
    : ["template-document-run", templateId, jobId, username, role],
  [templateId, jobId, documentIri, username, role]);
  const latest = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const result = documentIri ? await getReportDocumentRun(documentIri, signal)
        : await getTemplateDocumentRun(templateId!, jobId!, signal);
      const current = client.getQueryData<{ run: DocumentAnalysisRun | null }>(queryKey);
      if (current?.run && result.run?.recognition_run_id === current.run.recognition_run_id
        && (result.run.run_revision < current.run.run_revision
          || result.run.event_head < current.run.event_head
          || result.run.artifact_revision < current.run.artifact_revision)) return current;
      return result;
    },
    enabled: visible && (!!documentIri || (!!templateId && !!jobId)),
    refetchInterval: false,
    refetchOnWindowFocus: false,
  });
  const run = latest.data?.run ?? null;
  const runId = run?.recognition_run_id;
  const active = shouldSubscribeDocumentAnalysisEvents(run?.status);
  const artifactsKey = useMemo(() => ["template-document-artifact", username, role, runId],
    [username, role, runId]);
  useEffect(() => () => { void client.cancelQueries({ queryKey, exact: true }); },
    [client, queryKey, visible]);
  useEffect(() => () => { void client.cancelQueries({ queryKey: artifactsKey }); },
    [client, artifactsKey, visible]);
  useEffect(() => {
    if (!visible || !runId || !active) return;
    const scheduler = createRunRefreshScheduler(() => client.refetchQueries(
      { queryKey, exact: true }, { cancelRefetch: false },
    ));
    let eventHead = run?.event_head ?? -1;
    let close = () => {};
    try {
      close = subscribeDocumentAnalysisEvents(runId, (event) => {
        if (event.recognition_run_id !== runId || event.event_head <= eventHead) return;
        eventHead = event.event_head;
        scheduler.event();
      }, (connected) => scheduler.connection(connected));
    } catch {
      // Unsupported/unavailable EventSource still has the read-only fallback.
    }
    return () => { close(); scheduler.close(); };
    // A progress update must not tear down a healthy subscription.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, client, queryKey, runId, visible]);

  // On return to the panel, synchronize the run before reading any cached version's endpoint.
  const readable = visible && !!runId && !latest.isFetching && latest.isSuccess
    && !["deleted", "expired", "deleting"].includes(run?.status ?? "");
  const graphVersion = run?.identities.graph_snapshot_id;
  const graph = useQuery({
    queryKey: [...artifactsKey, "graph", graphVersion, projection],
    queryFn: async ({ signal }) => {
      const result = await getDocumentAnalysisGraph(runId!, projection, signal);
      if (result.recognition_run_id !== runId
        || (graphVersion && result.graph_snapshot?.snapshot_id !== graphVersion)) {
        void client.invalidateQueries({ queryKey, exact: true });
        throw new Error("图谱版本已更新，正在同步最新结果");
      }
      return result;
    },
    enabled: readable && !!graphVersion,
    placeholderData: (previous, previousQuery) => previous && previous.recognition_run_id === runId
      && previous.projection === projection && previousQuery?.queryKey[1] === username
      && previousQuery?.queryKey[2] === role ? previous : undefined,
    staleTime: Infinity,
    refetchInterval: false,
    retry: false,
  });
  const rankingSummaryId = run?.identities.ranking_summary_id ?? "";
  const rankingBudgetEnabled = run?.ranking_budget_enabled ?? true;
  const ranking = useQuery({
    queryKey: [...artifactsKey, "ranking", run?.identities.ranking_summary_id ?? run?.artifact_revision,
      rankingBudgetEnabled],
    queryFn: async ({ signal }) => {
      try {
        return await getDocumentAnalysisRankingSummary(runId!, signal, {
          summaryId: rankingSummaryId, budgetEnabled: rankingBudgetEnabled,
        });
      } catch (error) {
        if (error instanceof VersionConflictError) {
          void client.invalidateQueries({ queryKey, exact: true });
        }
        throw error;
      }
    },
    enabled: readable,
    staleTime: Infinity,
    refetchInterval: false,
    retry: false,
  });
  const structureVersion = run?.identities.structure_snapshot_id ?? run?.identities.analysis_id;
  const sourceReady = readable && ["ready", "partial"].includes(run?.artifacts.structure ?? "");
  const source = useQuery<DocumentAnalysisSourceArtifact & { metadata?: DocumentAnalysisMetadataArtifact }>({
    queryKey: [...artifactsKey, "source", structureVersion,
      ...(documentIri ? [run?.identities.metadata_snapshot_id] : [])],
    queryFn: async ({ signal }) => {
      // Report metadata contains both the frozen reader content and chapter tree.
      // Do not fetch a second full source payload merely to validate selection anchors.
      let result: DocumentAnalysisSourceArtifact & { metadata?: DocumentAnalysisMetadataArtifact };
      if (documentIri) {
        const metadata = await getDocumentAnalysisMetadata(runId!, signal);
        if (!metadata.analysis || !metadata.content || !metadata.filename) {
          throw new Error("运行正文尚未可用，请刷新重试");
        }
        result = {
          contract_version: metadata.contract_version,
          recognition_run_id: metadata.recognition_run_id,
          ...metadata.analysis,
          filename: metadata.filename, content: metadata.content, selection: null, anchors: [],
          metadata,
        };
      } else result = await getDocumentAnalysisSource(runId!, undefined, signal);
      if (result.recognition_run_id !== runId
        || (run?.identities.analysis_id && result.analysis_id !== run.identities.analysis_id)) {
        throw new Error("原文版本与当前运行不一致");
      }
      return result;
    },
    enabled: sourceReady,
    staleTime: Infinity,
    refetchInterval: false,
    retry: false,
  });
  const selectionRef = selection?.runId === runId ? selection?.ref : undefined;
  const sourceSelection = useQuery({
    queryKey: [...artifactsKey, "selection", structureVersion, selectionRef],
    queryFn: ({ signal }) => getDocumentAnalysisSourceSelection(runId!, selectionRef!, signal),
    enabled: sourceReady && !!selectionRef,
    staleTime: Infinity,
    refetchInterval: false,
    retry: false,
  });
  const selectedSource = matchingSourceSelection(source.data, sourceSelection.data, selectionRef);
  const selectionMismatch = sourceSelection.data && source.data && !selectedSource
    ? new Error("原文定位与当前文档版本不一致") : null;

  const create = useMutation({
    mutationFn: (input: {
      templateId?: string; jobId?: string | null; documentIri?: string; requestKey: string;
    }) => input.documentIri
      ? createReportDocumentRun(input.documentIri, input.requestKey)
      : createTemplateDocumentRun(input.templateId!, input.jobId!, input.requestKey),
    onSuccess: (_data, input) => {
      if (requestKey.current?.key === input.requestKey) requestKey.current = null;
    },
    onSettled: (_data, _error, input) => client.invalidateQueries({
      queryKey: input.documentIri ? ["report-document-run", input.documentIri]
        : ["template-document-run", input.templateId, input.jobId],
    }),
  });
  const control = useMutation({
    mutationFn: (input: { runId: string; action: DocumentAnalysisControlAction; revision: number }) =>
      controlDocumentAnalysisRun(input.runId, input.action, input.revision, crypto.randomUUID(),
        documentIri ? "报告文档关系图谱面板操作" : "模板关系图谱面板操作"),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: ["template-document-run"] }),
      client.invalidateQueries({ queryKey: ["report-document-run"] }),
    ]),
  });
  const creating = create.isPending && create.variables?.templateId === templateId
    && create.variables?.jobId === jobId && create.variables?.documentIri === documentIri;
  const controlling = control.isPending && control.variables?.runId === runId;
  const mutationError = create.variables?.templateId === templateId && create.variables?.jobId === jobId
    && create.variables?.documentIri === documentIri
    ? create.error : null;
  const error = latest.error || graph.error || ranking.error || mutationError
    || (control.variables?.runId === runId ? control.error : null);
  const artifactsAvailable = !["deleted", "expired", "deleting"].includes(run?.status ?? "");
  return {
    run, graph: artifactsAvailable ? graph.data : undefined, ranking: ranking.data,
    metadata: artifactsAvailable ? source.data?.metadata : undefined,
    metadataError: source.error,
    source: artifactsAvailable ? source.data : undefined,
    sourceSelection: artifactsAvailable ? selectedSource : undefined,
    sourceIdentity: source.data ? sourceDocumentIdentity(source.data) : undefined,
    sourceError: source.error || sourceSelection.error || selectionMismatch,
    sourceLoading: source.isFetching || sourceSelection.isFetching,
    selectionRef, loading: latest.isLoading,
    running: active || creating, busy: creating || controlling, error,
    canCreate: role === "senior_analyst",
    projection, setProjection,
    select: (ref: string) => { if (runId) setSelection({ runId, ref }); },
    start: () => {
      if ((documentIri || (templateId && jobId)) && !active && !creating) {
        if (requestKey.current?.source !== sourceKey) {
          requestKey.current = { source: sourceKey, key: crypto.randomUUID() };
        }
        create.mutate({ templateId, jobId, documentIri, requestKey: requestKey.current.key });
      }
    },
    control: (action: DocumentAnalysisControlAction) => {
      if (run && !controlling) control.mutate({ runId: run.recognition_run_id,
        action, revision: run.run_revision });
    },
    refresh: () => {
      if (!visible) return;
      void latest.refetch();
      void client.invalidateQueries({ queryKey: artifactsKey });
    },
  };
}

export function useTemplateDocumentRun(
  templateId?: string, jobId?: string | null, panelVisible = true,
) {
  return useSourceDocumentRun(templateId, jobId, panelVisible);
}

export function useReportDocumentRun(documentIri: string) {
  return useSourceDocumentRun(undefined, undefined, true, documentIri);
}
