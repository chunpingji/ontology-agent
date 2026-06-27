import { Mark, mergeAttributes } from "@tiptap/core";

export const ENTITY_PALETTE = [
  { bg: "#DBEAFE", border: "#3B82F6" }, // blue
  { bg: "#D1FAE5", border: "#10B981" }, // emerald
  { bg: "#FED7AA", border: "#F97316" }, // orange
  { bg: "#E9D5FF", border: "#A855F7" }, // purple
  { bg: "#FECACA", border: "#EF4444" }, // red
  { bg: "#CCFBF1", border: "#14B8A6" }, // teal
  { bg: "#FEF08A", border: "#CA8A04" }, // yellow
  { bg: "#FBCFE8", border: "#EC4899" }, // pink
  { bg: "#C7D2FE", border: "#6366F1" }, // indigo
  { bg: "#FDE68A", border: "#D97706" }, // amber
] as const;

export function entityColorIndex(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % ENTITY_PALETTE.length;
}

export const EntityAnnotation = Mark.create({
  name: "entity-annotation",

  addAttributes() {
    return {
      label: { default: null },
      className: { default: null },
      score: { default: 0 },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-entity-label]" }];
  },

  renderHTML({ HTMLAttributes }) {
    const label = HTMLAttributes.label ?? "";
    const cls = HTMLAttributes.className ?? label;
    const score = Number(HTMLAttributes.score ?? 0);
    const pct = Math.round(score * 100);
    const color = ENTITY_PALETTE[entityColorIndex(cls)];

    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-entity-label": label,
        "data-entity-class": cls,
        "data-entity-score": String(pct),
        class: "entity-annotation",
        style: [
          `background: ${color.bg}`,
          `border-bottom: 2px solid ${color.border}`,
          "border-radius: 3px",
          "padding: 1px 4px",
          "cursor: default",
        ].join("; "),
      }),
      0,
    ];
  },
});
