"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { generateSectionPrompt, previewSectionNarrative, type SectionNarrativePreview } from "@/lib/api";
import { groupsIn, isUploadDocumentContainer, type TemplateV2, type SectionNarrative } from "@/lib/reporting-v2";

const fieldClass = "w-full min-w-0 rounded border bg-background px-3 py-2 text-sm";
const policy = "urn:report:prompt:custom-draft:1";

export function SectionNarrativeEditor({ sectionId, template, templateId, sourceJobId, recognitionMode, sampleText, onChange }: {
  sectionId: string; template: TemplateV2; templateId?: string; sourceJobId?: string | null;
  recognitionMode?: "finder_legacy" | "ontology_guided"; sampleText?: string | null;
  onChange: (change: (next: TemplateV2) => void) => void;
}) {
  const section = template.sections.find((s) => s.section_id === sectionId)!;
  const sectionTitle = isUploadDocumentContainer(section) ? "文档正文" : section.title;
  const inputIds = [...new Set([...groupsIn(section.groups).flatMap((g) => g.units.flatMap((u) => u.inputs.map((i) => i.input_ref))), ...(section.narrative?.input_refs.map((r) => r.input_id) || [])])];
  const config: SectionNarrative = section.narrative ?? { enabled: false, instructions: "", policy_ref: policy,
    input_refs: inputIds.map((input_id) => ({ input_id })), required_refs: [], claim_refs: [] };
  const labels = config.input_refs.map((ref) => template.definitions.inputs[ref.input_id]?.label || ref.input_id);
  const [suggestion, setSuggestion] = useState("");
  const [preview, setPreview] = useState<{ result: SectionNarrativePreview; config: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const promptEdits = useRef(0);
  const configKey = JSON.stringify([config, template, sourceJobId, recognitionMode]);
  useEffect(() => () => { request.current?.abort(); }, [templateId, sectionId, sourceJobId, recognitionMode]);
  function change(update: (value: SectionNarrative) => void) {
    onChange((next) => {
      const target = next.sections.find((s) => s.section_id === sectionId);
      if (!target) return;
      target.narrative ??= structuredClone(config);
      update(target.narrative);
    });
  }
  function updatePrompt(instructions: string) {
    promptEdits.current += 1;
    change((value) => { value.instructions = instructions; });
  }
  async function generatePrompt() {
    if (busy) return;
    const controller = new AbortController(); request.current = controller;
    const baselineEdits = promptEdits.current;
    setBusy("prompt"); setError(""); setSuggestion("");
    try {
      const result = await generateSectionPrompt({
        section_title: sectionTitle, slot_labels: labels,
        sample_text: (sampleText ?? "").slice(0, 4000), instructions: config.instructions,
      }, controller.signal);
      if (!controller.signal.aborted) {
        if (promptEdits.current === baselineEdits) updatePrompt(result.prompt);
        else setSuggestion(result.prompt);
      }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Prompt 生成失败"); }
    finally { if (request.current === controller) setBusy(""); }
  }
  async function generatePreview() {
    if (busy || !templateId || !sourceJobId || !config.instructions.trim()) return;
    const controller = new AbortController(); request.current = controller;
    setBusy("preview"); setError("");
    const baselineKey = configKey;
    try {
      const result = await previewSectionNarrative({
        job_id: sourceJobId, template_id: templateId, section_id: sectionId,
        prompt: config.instructions, draft_schema: template,
      }, controller.signal);
      if (!controller.signal.aborted) setPreview({ result, config: baselineKey });
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "行文预览失败"); }
    finally { if (request.current === controller) setBusy(""); }
  }
  return <section aria-label={`Section 行文 · ${sectionTitle || "未命名章节"}`} className="my-3 space-y-3 rounded border bg-muted/30 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">Section · AI 行文</strong>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={config.enabled} onChange={(e) => { const enabled = e.target.checked; change((v) => { v.enabled = enabled; }); }} />在报告中生成本节正文</label>
    </div>
    <p className="text-xs text-muted-foreground">从模板样例生成行文 Prompt，可直接编辑并预览本节正文。预览使用已关联原件的识别结果，缺失字段保留为待补充。</p>
    <Button size="sm" variant="outline" disabled={!!busy} onClick={generatePrompt}>{busy === "prompt" ? "正在生成 Prompt…" : "从样本生成"}</Button>
    {suggestion && <div className="space-y-2 rounded border bg-background p-2"><p className="text-xs text-muted-foreground">已保留请求期间的手工编辑，可选择采用本次建议。</p><p className="whitespace-pre-wrap text-sm">{suggestion}</p><Button size="sm" onClick={() => { updatePrompt(suggestion); setSuggestion(""); }}>采用生成的 Prompt</Button></div>}
    <label className="block space-y-1 text-sm"><span>Section 行文 Prompt</span><textarea aria-label="Section 行文 Prompt" rows={5} className={fieldClass} value={config.instructions} onChange={(e) => updatePrompt(e.target.value)} placeholder="描述本节写作要求，使用 {{字段名}} 引用变量；可直接编辑" /></label>
    {labels.length > 0 && <p className="text-xs text-muted-foreground">可用变量：{[...new Set(labels)].map((label) => `{{${label}}}`).join("、")}</p>}
    <details><summary className="cursor-pointer text-xs">本节字段范围（{config.input_refs.length}）</summary>
      {inputIds.map((id) => <label className="my-1 flex items-center gap-2 text-xs" key={id}><input type="checkbox" checked={config.input_refs.some((r) => r.input_id === id)} onChange={(e) => {
        const checked = e.target.checked; change((v) => {
          v.input_refs = checked ? [...v.input_refs, { input_id: id }] : v.input_refs.filter((r) => r.input_id !== id);
          v.required_refs = v.required_refs.filter((r) => v.input_refs.some((input) => input.input_id === r.input_id));
        });
      }} />{template.definitions.inputs[id]?.label || id}</label>)}
      <p className="text-xs text-muted-foreground">默认使用本节字段；已有部分数据也可预览，样例不会用于填补事实。</p>
    </details>
    <Button size="sm" disabled={!!busy || !templateId || !sourceJobId || !config.instructions.trim()} onClick={generatePreview}>{busy === "preview" ? "正在生成本节正文…" : "AI 行文预览"}</Button>
    <p className="text-xs text-muted-foreground">预览可使用未保存的 Prompt。用于完整报告时，勾选生成本节正文并保存新修订。</p>
    {!sourceJobId && <p className="text-xs text-muted-foreground">关联真实源文档并完成识别后可预览行文。</p>}
    {error && <p role="alert" className="whitespace-pre-wrap text-sm text-destructive">{error}</p>}
    {preview && <div className="space-y-2 rounded border bg-background p-3">
      <div className="flex items-center justify-between"><p role="status" className="text-xs text-muted-foreground">{preview.config !== configKey ? "配置已变更，下方为上次生成结果。" : "本节 AI 行文预览（待核对草稿）"}</p><Button size="sm" variant="ghost" onClick={() => setPreview(null)}>关闭预览</Button></div>
      {preview.result.source?.source_filename && <p className="text-xs text-muted-foreground">来源：{preview.result.source.source_filename}</p>}
      {preview.result.warnings?.map((warning) => <p key={warning} className="text-xs text-muted-foreground">{warning}</p>)}
      <article aria-label="本节行文正文" className="whitespace-pre-wrap break-words text-sm leading-relaxed">{preview.result.narrative}</article>
    </div>}
  </section>;
}
