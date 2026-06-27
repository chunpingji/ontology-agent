"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ENTITY_PALETTE, entityColorIndex } from "./entity-mark";

interface CellAnnotation {
  start: number;
  end: number;
  text: string;
  label: string;
  className?: string;
  score: number;
}

interface AnnotatedCell {
  value: string;
  annotations: CellAnnotation[];
}

interface ExcelViewerProps {
  content: {
    headers: string[];
    rows: Record<string, AnnotatedCell>[];
  };
}

function AnnotatedText({ cell }: { cell: AnnotatedCell }) {
  if (!cell.annotations.length) {
    return <span>{cell.value}</span>;
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const ann of cell.annotations) {
    if (ann.start > cursor) {
      parts.push(
        <span key={`t-${cursor}`}>{cell.value.slice(cursor, ann.start)}</span>,
      );
    }
    const cls = ann.className ?? ann.label;
    const color = ENTITY_PALETTE[entityColorIndex(cls)];
    const pct = Math.round(ann.score * 100);
    parts.push(
      <span
        key={`a-${ann.start}`}
        title={`${ann.label} (${pct}%)`}
        data-entity-label={ann.label}
        className="entity-annotation inline-flex items-center rounded px-1 text-xs cursor-default"
        style={{
          background: color.bg,
          borderBottom: `2px solid ${color.border}`,
        }}
      >
        {ann.text}
        <span
          className="ml-1 text-[10px] opacity-70"
          style={{ color: color.border }}
        >
          {ann.label}
        </span>
      </span>,
    );
    cursor = ann.end;
  }
  if (cursor < cell.value.length) {
    parts.push(
      <span key={`t-${cursor}`}>{cell.value.slice(cursor)}</span>,
    );
  }
  return <>{parts}</>;
}

export function ExcelViewer({ content }: ExcelViewerProps) {
  const { headers, rows } = content;

  return (
    <div className="overflow-auto max-h-[70vh]">
      <Table>
        <TableHeader>
          <TableRow>
            {headers.map((h) => (
              <TableHead key={h}>{h}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, idx) => (
            <TableRow key={idx}>
              {headers.map((h) => {
                const cell = row[h] ?? { value: "", annotations: [] };
                return (
                  <TableCell key={h}>
                    <AnnotatedText cell={cell} />
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
