"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getTemplateRecognitionEngine, updateTemplateRecognitionEngine,
  type AstTemplateDTO, type TemplateRecognitionEngineUpdate,
} from "@/lib/api";
import { useIdentity } from "@/lib/use-identity";
import { Button } from "@/components/ui/button";

export function TemplateRecognitionEngine({ templateId, rootClassIri }: {
  templateId: string; rootClassIri: string | null;
}) {
  const { identity: { username, role } } = useIdentity();
  return <EngineSelector key={JSON.stringify([templateId, rootClassIri, username, role])}
    templateId={templateId} rootClassIri={rootClassIri} username={username} role={role} />;
}

function EngineSelector({ templateId, rootClassIri, username, role }: {
  templateId: string; rootClassIri: string | null; username: string; role: string;
}) {
  const client = useQueryClient();
  const key = ["template-recognition-engine", username, role, templateId, rootClassIri];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getTemplateRecognitionEngine(templateId, signal),
  });
  const [draft, setDraft] = useState<TemplateRecognitionEngineUpdate | null>(null);
  const mutation = useMutation({
    mutationFn: (request: TemplateRecognitionEngineUpdate) => updateTemplateRecognitionEngine(templateId, request),
    onSuccess: async (data) => {
      const templateKey = ["ast-template", templateId];
      // Discard reads started before the save so they cannot restore the old mode.
      await Promise.all([
        client.cancelQueries({ queryKey: key, exact: true }),
        client.cancelQueries({ queryKey: templateKey, exact: true }),
      ]);
      setDraft(null);
      client.setQueryData(key, data);
      client.setQueryData(templateKey, (old: AstTemplateDTO | undefined) => old ? {
        ...old, recognition_mode: data.recognition_mode, finder_profile_id: data.finder_profile_id,
      } : old);
      // The PATCH already confirms persistence. Refresh related views separately;
      // a slow/failed read must not keep the save pending or re-download the template.
      void client.invalidateQueries({ queryKey: ["recognition-context"] });
      void client.invalidateQueries({ queryKey: ["template-finder", username, role, templateId], refetchType: "none" });
    },
  });
  const config = query.data;
  const mode = draft?.recognition_mode ?? config?.recognition_mode ?? "ontology_guided";
  const profile = draft ? draft.finder_profile_id : config?.finder_profile_id;
  const editable = role === "senior_analyst" && !!config && !query.isError && !mutation.isPending;
  const error = mutation.error ?? query.error;

  function choose(nextMode: TemplateRecognitionEngineUpdate["recognition_mode"], nextProfile: string | null) {
    if (!config) return;
    mutation.reset();
    setDraft({
      recognition_mode: nextMode, finder_profile_id: nextProfile,
      expected_recognition_mode: draft?.expected_recognition_mode ?? config.recognition_mode,
      expected_finder_profile_id: draft ? draft.expected_finder_profile_id : config.finder_profile_id,
    });
  }

  return <div className="space-y-3 text-sm">
    <div className="space-y-4">
      <label className="grid gap-2">
        关系图谱识别引擎
        <select aria-label="关系图谱识别引擎" className="w-full min-w-0 rounded border bg-background px-3 py-2"
          value={mode} disabled={!editable}
          onChange={(event) => {
            const next = event.target.value as TemplateRecognitionEngineUpdate["recognition_mode"];
            choose(next, next === "finder_legacy" ? config?.finder_profiles[0]?.id ?? null : null);
          }}>
          <option value="ontology_guided">文档结构解析＋本体指引</option>
          <option value="finder_legacy" disabled={!config?.finder_profiles.length}>本体指引1.0</option>
        </select>
      </label>
      {mode === "finder_legacy" && <label className="grid gap-2">
        本体指引1.0配置
        <select aria-label="本体指引1.0配置" className="w-full min-w-0 rounded border bg-background px-3 py-2"
          value={profile ?? ""} disabled={!editable}
          onChange={(event) => choose("finder_legacy", event.target.value)}>
          {config?.finder_profiles.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>}
      {role === "senior_analyst" && <div className="flex justify-end"><Button size="sm"
        disabled={!editable || !draft || (mode === "finder_legacy" && !profile)}
        onClick={() => { if (draft) mutation.mutate(draft); }}>
        {mutation.isPending ? "正在保存…" : "保存引擎设置"}
      </Button></div>}
    </div>
    <p className="text-xs text-muted-foreground">
      仅应用于当前模板修订，报告中心沿用此设置。保存后请在源文档中手动开始识别；已有任务和结果保留。
    </p>
    {config && !config.finder_profiles.length && <p className="text-xs text-muted-foreground">
      当前文档类型暂无可用的本体指引1.0配置。
    </p>}
    {query.isPending && <p role="status">正在加载引擎设置…</p>}
    {mutation.isSuccess && !draft && <p role="status">引擎设置已保存。</p>}
    {error && <div role="alert" className="flex flex-wrap items-center gap-2 text-destructive">
      <span>{error instanceof Error ? error.message : "引擎设置操作失败"}</span>
      <Button size="sm" variant="outline" onClick={() => {
        setDraft(null); mutation.reset(); void query.refetch();
      }}>刷新设置</Button>
    </div>}
  </div>;
}
