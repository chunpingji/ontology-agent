import type { FinderDocument, FinderGraph } from "./api";

/** Never pair a late graph with another execution's original document. */
export function finderArtifactsMatch(graph?: FinderGraph, source?: FinderDocument): boolean {
  return !!graph && !!source && !!graph.execution_id &&
    (["template_id", "source_job_id", "execution_id", "document_hash", "parser_version",
      "structure_hash", "analysis_id"] as const).every((key) => graph[key] === source[key]);
}
