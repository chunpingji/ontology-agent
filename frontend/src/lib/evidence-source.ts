import type { DocumentEvidenceIR, EvidenceAnchor, TiptapContent } from "./api";

export function codepointToUtf16(text: string, offset: number): number {
  const points = Array.from(text);
  if (!Number.isInteger(offset) || offset < 0 || offset > points.length) {
    throw new Error("原文偏移越界");
  }
  return points.slice(0, offset).join("").length;
}

export function resolveEvidenceSource(content: TiptapContent, anchor: EvidenceAnchor) {
  const ir = content.analysis as DocumentEvidenceIR | undefined;
  if (!ir || ir.document_hash !== anchor.document_hash ||
      ir.parser_version !== anchor.parser_version || ir.structure_hash !== anchor.structure_hash) {
    throw new Error("来源已失效：样例或解析版本已变更，请重新分析");
  }
  const unit = ir.evidence_units.find((item) => item.evidence_id === anchor.evidence_id);
  if (!unit || unit.block_id !== anchor.block_id || unit.section_node_id !== anchor.section_node_id ||
      unit.paragraph_index !== anchor.paragraph_index || unit.fragment_index !== anchor.fragment_index ||
      JSON.stringify(unit.table_path) !== JSON.stringify(anchor.table_path) ||
      unit.row_index !== anchor.row_index || unit.column_index !== anchor.column_index ||
      unit.physical_page_number !== anchor.physical_page_number) {
    throw new Error("来源已失效：证据坐标不匹配");
  }
  if ((anchor.span_start == null) !== (anchor.span_end == null)) {
    throw new Error("原文偏移不完整");
  }
  const from = codepointToUtf16(unit.text, anchor.span_start ?? 0);
  const to = codepointToUtf16(unit.text, anchor.span_end ?? Array.from(unit.text).length);
  if (anchor.span_start != null && to <= from) throw new Error("原文偏移为空");
  return { unit, from, to };
}
