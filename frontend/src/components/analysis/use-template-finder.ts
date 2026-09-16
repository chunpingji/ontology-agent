"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getRecognitionContext, getTemplateFinder, getTemplateFinderGraph,
  getTemplateFinderSource, startTemplateFinder, type EvidenceAnchor,
  getFinderPdeDecision, saveFinderPdeDecision, VersionConflictError,
  type PdeDecisionChoice, type SubRelationship,
} from "@/lib/api";
import { finderArtifactsMatch } from "@/lib/template-finder";
import { useIdentity } from "@/lib/use-identity";

export function useRecognitionContext(documentIri?: string | null, templateId?: string | null) {
  const { identity: { username, role } } = useIdentity();
  return useQuery({
    queryKey: ["recognition-context", username, role, documentIri, templateId ?? null],
    queryFn: ({ signal }) => getRecognitionContext(documentIri!, templateId, signal),
    enabled: !!documentIri,
  });
}

export function useTemplateFinder(templateId?: string | null, sourceJobId?: string | null,
  enabled = true, includeArtifacts = true) {
  const { identity: { username, role } } = useIdentity();
  const client = useQueryClient();
  const key = ["template-finder", username, role, templateId, sourceJobId, "finder_legacy"];
  const statusKey = [...key, "status"];
  const scope = JSON.stringify(key);
  const active = enabled && !!templateId && !!sourceJobId;
  const status = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => getTemplateFinder(templateId!, sourceJobId!, signal),
    enabled: active,
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.status ?? "") ? 1500 : false,
  });
  const execution = status.data?.has_result ? status.data.execution_id : null;
  async function readArtifact<T>(read: () => Promise<T>): Promise<T> {
    try { return await read(); } catch (error) {
      // Another tab can replace the current execution between status and artifact reads.
      // Refresh the status first; its new execution selects new artifact query keys.
      if (error instanceof VersionConflictError) {
        await client.invalidateQueries({ queryKey: statusKey, exact: true });
      }
      throw error;
    }
  }
  const source = useQuery({
    queryKey: [...key, "source", execution, status.data?.execution_id, status.data?.input?.source_hash],
    queryFn: ({ signal }) => readArtifact(() => getTemplateFinderSource(templateId!, sourceJobId!, execution, signal)),
    enabled: active && includeArtifacts && status.isSuccess,
    retry: (count, error) => !(error instanceof VersionConflictError) && count < 3,
  });
  const graph = useQuery({
    queryKey: [...key, "graph", execution],
    queryFn: ({ signal }) => readArtifact(() => getTemplateFinderGraph(templateId!, sourceJobId!, execution!, signal)),
    enabled: active && includeArtifacts && !!execution,
    retry: (count, error) => !(error instanceof VersionConflictError) && count < 3,
  });
  const mutation = useMutation({
    mutationFn: (request: { scope: string; key: typeof key; templateId: string; sourceJobId: string;
      request_key: string; expected_execution_id: string | null }) =>
      startTemplateFinder(request.templateId, request.sourceJobId, {
        request_key: request.request_key, expected_execution_id: request.expected_execution_id,
      }),
    onMutate: (request) => client.cancelQueries({ queryKey: request.key }),
    onSuccess: (result, request) => client.setQueryData([...request.key, "status"], result),
    // Invalidating all children here refetches the old execution's source/graph/PDE
    // before the status response arrives, producing spurious RESULT_UNAVAILABLE 409s.
    onSettled: (_data, _error, request) => client.invalidateQueries({ queryKey: [...request.key, "status"], exact: true }),
  });
  const [selection, setSelection] = useState<{ key: string; anchor: EvidenceAnchor } | null>(null);
  const readerKey = JSON.stringify([scope, execution, source.data?.analysis_id]);
  const paired = finderArtifactsMatch(graph.data, source.data);
  const pending = mutation.isPending && mutation.variables?.scope === scope;
  const running = ["queued", "running"].includes(status.data?.status ?? "") || pending;
  const error = status.error ?? (includeArtifacts ? source.error ?? graph.error : null) ??
    (mutation.variables?.scope === scope ? mutation.error : null);
  return {
    status: status.data, source: source.data, graph: paired ? graph.data : undefined,
    loading: status.isLoading || source.isLoading, error, running,
    canStart: active && role === "senior_analyst" && status.isSuccess && !running,
    readerKey, anchor: selection?.key === readerKey ? selection.anchor : null,
    select: (anchor: EvidenceAnchor | null) => setSelection(anchor ? { key: readerKey, anchor: { ...anchor } } : null),
    start: () => {
      if (!active || !status.isSuccess || running) return;
      setSelection(null);
      mutation.mutate({ scope, key, templateId: templateId!, sourceJobId: sourceJobId!, request_key: crypto.randomUUID(),
        expected_execution_id: status.data.execution_id });
    },
    refresh: async () => {
      await client.invalidateQueries({ queryKey: statusKey, exact: true });
      await client.invalidateQueries({ queryKey: key, predicate: (query) => {
        const current = client.getQueryData<typeof status.data>(statusKey);
        return query.queryKey[7] === (current?.has_result ? current.execution_id : null);
      } });
    },
  };
}

export type TemplateFinderModel = ReturnType<typeof useTemplateFinder>;

function hasPdeConflict(nodes: SubRelationship[]): boolean {
  return nodes.some((node) => !!node.conflict || hasPdeConflict(node.sub_relationships ?? []));
}

export function useFinderPdeDecision(templateId: string, sourceJobId: string,
  model: TemplateFinderModel) {
  const { identity: { username, role } } = useIdentity();
  const client = useQueryClient();
  const execution = model.graph?.execution_id;
  const finderKey = ["template-finder", username, role, templateId, sourceJobId, "finder_legacy"];
  const key = [...finderKey, "pde-decision", execution ?? null];
  const scope = JSON.stringify(key);
  const hasConflict = hasPdeConflict(model.graph?.relationships ?? []);
  const active = !!execution && hasConflict && !model.running && !model.status?.stale;
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getFinderPdeDecision(templateId, sourceJobId, execution!, signal),
    enabled: active,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (request: { key: typeof key; finderKey: typeof finderKey; scope: string;
      templateId: string; sourceJobId: string; execution: string;
      chosen: PdeDecisionChoice; expectedVersion: number }) =>
      saveFinderPdeDecision(request.templateId, request.sourceJobId, request.execution, {
        chosen: request.chosen, expected_version: request.expectedVersion,
      }),
    onMutate: (request) => client.cancelQueries({ queryKey: request.key, exact: true }),
    onSuccess: (decision, request) => client.setQueryData(request.key, decision),
    onError: (_error, request) => client.invalidateQueries({ queryKey: request.finderKey }),
  });
  const saving = mutation.isPending && mutation.variables?.scope === scope;
  const mutationError = mutation.variables?.scope === scope ? mutation.error : null;
  return {
    decision: active ? query.data ?? null : null,
    pending: saving || !active || !query.isSuccess || !!query.error,
    error: query.error ?? (mutationError instanceof VersionConflictError
      ? new Error("识别结果或审核已被更新，已刷新当前状态，请核对后重试。") : mutationError),
    onDecide: role === "senior_analyst" ? (chosen: PdeDecisionChoice) => {
      if (!active || !execution || !query.data || query.error || saving) return;
      mutation.mutate({ key, finderKey, scope, templateId, sourceJobId, execution, chosen,
        expectedVersion: query.data.version });
    } : undefined,
  };
}
