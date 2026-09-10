import type { ExtractionJob } from "./api";

// Mirrors the server's Word workflow boundary. A source-only preview does not
// identify the workflow: template sources can also lack recognition results.
export function extractionCapabilities(job?: Pick<ExtractionJob, "source_type" | "source_mode">) {
  if (!job?.source_type) return { preview: false, recognition: false };
  const word = job.source_type.trim().toLowerCase() === "word";
  const templateSource = job.source_mode === "template_default";
  return {
    preview: !word || templateSource || job.source_mode === "doc_repo_preview",
    recognition: !word || templateSource,
  };
}
