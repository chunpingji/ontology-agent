import type { CSSProperties } from "react";
import type { BatchDemoLayout, BatchDemoOutputNode } from "@/lib/api";

function layoutOf(node: BatchDemoOutputNode) {
  return node.provenance_refs?.find((ref) => ref.kind === "template_layout") as BatchDemoLayout | undefined;
}

function textStyle(layout: Partial<BatchDemoLayout>): CSSProperties {
  return {
    fontFamily: `"${layout.latin_font_family ?? "Times New Roman"}", "${layout.font_family ?? "宋体"}", "SimSun", serif`,
    fontSize: `${layout.font_size_pt ?? 10.5}pt`,
    fontWeight: layout.bold ? "bold" : "normal", fontStyle: layout.italic ? "italic" : "normal",
    textDecoration: layout.underline ? "underline" : undefined,
    textAlign: layout.align,
    verticalAlign: layout.script === "superscript" ? "super" : layout.script === "subscript" ? "sub" : undefined,
  };
}

function CellText({ node, layout }: { node: BatchDemoOutputNode; layout: BatchDemoLayout }) {
  if (!layout.paragraphs) return <>{node.text}</>;
  return <>{layout.paragraphs.map((paragraph, index) => <p key={index} style={{
    ...textStyle(paragraph.style), lineHeight: `${paragraph.style.line_height_pt ?? 15.6}pt`,
    marginTop: `${paragraph.space_before_pt}pt`, marginBottom: `${paragraph.space_after_pt}pt`,
  }}>{paragraph.runs.length ? paragraph.runs.map((run, i) => <span key={i} style={textStyle(run.style)}>{run.text}</span>) : <br />}</p>)}</>;
}

export function BatchReportPreview({ node }: { node: BatchDemoOutputNode }) {
  const children = node.children.map((child) => <BatchReportPreview key={child.node_id} node={child} />);
  const layout = layoutOf(node);
  if (layout) {
    const font: CSSProperties = {
      ...textStyle(layout),
      marginTop: `${layout.space_before_pt ?? 6}pt`, marginBottom: `${layout.space_after_pt ?? 6}pt`,
      textDecoration: layout.underline ? "underline" : undefined,
      textAlign: layout.align, fontWeight: layout.bold ? "bold" : "normal",
    };
    if (node.kind === "document") return <div className="max-w-full overflow-x-auto" data-batch-layout={layout.renderer_version ?? "sample-layout-v1"}>{children}</div>;
    if (node.kind === "section") return <section className="mx-auto mb-6 bg-white text-black shadow-sm" style={{
      width: `${layout.width_pt}pt`, minHeight: `${layout.height_pt}pt`, boxSizing: "border-box",
      padding: `${layout.top_pt}pt ${layout.right_pt}pt ${layout.bottom_pt}pt ${layout.left_pt}pt`,
    }}>{children}</section>;
    if (node.kind === "table") return <table data-form={layout.role} className="my-4 border-collapse" style={{
      tableLayout: "fixed", width: `${layout.width_pt}pt`,
      marginLeft: layout.align === "center" ? `calc((100% - ${layout.width_pt}pt) / 2)` : undefined,
    }}><colgroup>{layout.widths?.map((width, index) => <col key={index} style={{ width: `${width / 20}pt` }} />)}</colgroup><tbody>{children}</tbody></table>;
    if (node.kind === "row") return <tr style={{ height: layout.height_pt ? `${layout.height_pt}pt` : undefined }}>{children}</tr>;
    if (node.kind === "cell" && layout.vmerge === "continue") return null;
    if (node.kind === "cell") return <td colSpan={layout.span ?? 1} rowSpan={layout.row_span ?? 1} style={{ ...font, borderTop: layout.border_top, borderRight: layout.border_right, borderBottom: layout.border_bottom, borderLeft: layout.border_left, padding: "4pt", verticalAlign: layout.vertical_align === "center" ? "middle" : (layout.vertical_align ?? "top"), overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}><CellText node={node} layout={layout} />{children}</td>;
    if (layout.role === "spacer") return <div aria-hidden="true" style={{ height: `${layout.line_height_pt}pt` }} />;
    if (layout.role === "title") return <h2 style={font}>{node.text}</h2>;
    if (layout.role === "heading") return <h3 style={font}>{node.text}</h3>;
    return <p style={{ ...font, whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{node.text}{children}</p>;
  }
  // Historical reports keep their original, format-independent preview.
  if (node.kind === "table") return <div className="my-4 overflow-x-auto"><table className="w-full border-collapse text-xs"><tbody>{children}</tbody></table></div>;
  if (node.kind === "row") return <tr className={node.header ? "bg-muted font-semibold" : ""}>{children}</tr>;
  if (node.kind === "cell") return <td className="min-w-20 whitespace-pre-wrap border p-2 align-top">{node.text}{children}</td>;
  if (node.kind === "section") return <section className="mb-8"><h2 className="mb-4 border-b pb-2 text-xl font-semibold">{node.text}</h2>{children}</section>;
  if (node.kind === "group") return <section className="mb-6"><h3 className="text-base font-semibold">{node.text}</h3>{children}</section>;
  if (node.kind === "document") return <>{children}</>;
  return <p className="my-2 whitespace-pre-wrap text-sm leading-6">{node.text}{children}</p>;
}
