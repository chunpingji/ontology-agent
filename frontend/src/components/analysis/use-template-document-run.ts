"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  controlDocumentAnalysisRun, createTemplateDocumentRun, getDocumentAnalysisGraph,
  getDocumentAnalysisSource, getIdentity, getTemplateDocumentRun, shouldSubscribeDocumentAnalysisEvents,
  subscribeDocumentAnalysisEvents, type DocumentAnalysisControlAction,
  type DocumentGraphProjection,
} from "@/lib/api";

/** Run ownership and source replay are keyed together; GET never starts recognition. */
export function useTemplateDocumentRun(templateId?: string, jobId?: string | null) {
  const client = useQueryClient();
  const identity = getIdentity();
  const [projection, setProjection] = useState<DocumentGraphProjection>("effective_affirmed");
  const [selection, setSelection] = useState<{ runId: string; ref: string } | null>(null);
  const queryKey = ["template-document-run", templateId, jobId, identity.username];
  const latest = useQuery({
    queryKey,
    queryFn: ({ signal }) => getTemplateDocumentRun(templateId!, jobId!, signal),
    enabled: !!templateId && !!jobId,
    refetchInterval: (query) => shouldSubscribeDocumentAnalysisEvents(query.state.data?.run?.status)
      ? 2500 : 10000,
  });
  const run = latest.data?.run ?? null;
  const runId = run?.recognition_run_id;
  const active = shouldSubscribeDocumentAnalysisEvents(run?.status);
  useEffect(() => {
    if (!runId || !active) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const close = subscribeDocumentAnalysisEvents(runId, (event) => {
      if (event.recognition_run_id !== runId || timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        void client.invalidateQueries({ queryKey: ["template-document-run", templateId, jobId] });
      }, 500);
    });
    return () => { close(); clearTimeout(timer); };
  }, [active, client, jobId, runId, templateId]);

  const graph = useQuery({
    queryKey: ["template-document-graph", runId, projection],
    queryFn: ({ signal }) => getDocumentAnalysisGraph(runId!, projection, signal),
    enabled: !!runId,
    refetchInterval: active ? 2500 : false,
  });
  // Pausing/finishing must fetch the last committed graph as well.
  useEffect(() => {
    if (runId) void client.invalidateQueries({ queryKey: ["template-document-graph", runId] });
  }, [client, runId, run?.artifact_revision, run?.status]);
  const selectionRef = selection && selection.runId === runId ? selection.ref : undefined;
  const source = useQuery({
    queryKey: ["template-document-source", runId, selectionRef ?? null],
    queryFn: ({ signal }) => getDocumentAnalysisSource(runId!, selectionRef, signal),
    enabled: !!runId && ["ready", "partial"].includes(run?.artifacts.structure ?? ""),
    retry: false,
  });

  const create = useMutation({
    mutationFn: (input: { templateId: string; jobId: string; requestKey: string }) =>
      createTemplateDocumentRun(input.templateId, input.jobId, input.requestKey),
    onSettled: (_data, _error, input) => client.invalidateQueries({
      queryKey: ["template-document-run", input.templateId, input.jobId],
    }),
  });
  const control = useMutation({
    mutationFn: (input: { runId: string; action: DocumentAnalysisControlAction; revision: number }) =>
      controlDocumentAnalysisRun(input.runId, input.action, input.revision, crypto.randomUUID(),
        "模板关系图谱面板操作"),
    onSettled: () => client.invalidateQueries({ queryKey: ["template-document-run"] }),
  });
  const creating = create.isPending && create.variables?.templateId === templateId
    && create.variables?.jobId === jobId;
  const controlling = control.isPending && control.variables?.runId === runId;
  const mutationError = create.variables?.templateId === templateId && create.variables?.jobId === jobId
    ? create.error : null;
  const error = latest.error || graph.error || mutationError
    || (control.variables?.runId === runId ? control.error : null);
  return {
    run, graph: graph.data, source: source.data, sourceError: source.error,
    sourceLoading: source.isFetching, selectionRef, loading: latest.isLoading,
    running: active || creating, busy: creating || controlling, error,
    canCreate: identity.role === "senior_analyst",
    projection, setProjection,
    select: (ref: string) => { if (runId) setSelection({ runId, ref }); },
    start: () => {
      if (templateId && jobId && !active && !creating) create.mutate({
        templateId, jobId, requestKey: crypto.randomUUID(),
      });
    },
    control: (action: DocumentAnalysisControlAction) => {
      if (run && !controlling) control.mutate({ runId: run.recognition_run_id,
        action, revision: run.run_revision });
    },
    refresh: () => {
      void latest.refetch();
      if (runId) void graph.refetch();
    },
  };
}
