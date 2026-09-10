"use client";

// Adapted from ReUI's Radix Tree (MIT, Copyright (c) 2025 Keenthemes Inc).
// Source and full license: ./tree.LICENSE.md. Styles target this project's Tailwind 3.
import { createContext, useContext, type CSSProperties, type HTMLAttributes } from "react";
import type { ItemInstance, TreeInstance } from "@headless-tree/core";
import { Slot } from "@radix-ui/react-slot";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const TreeIndent = createContext(16);

export function Tree<T>({ tree, indent = 16, className, children, ...props }: {
  tree: TreeInstance<T>; indent?: number;
} & HTMLAttributes<HTMLDivElement>) {
  return <TreeIndent.Provider value={indent}>
    <div {...tree.getContainerProps()} {...props} data-slot="tree"
      className={cn("flex min-w-0 flex-col gap-0.5", className)}
      onKeyDownCapture={(event) => {
        // Headless Tree listens on the native container. Row actions must keep
        // their own Enter/Space/arrow behavior, including keyboard evidence links.
        if ((event.target as HTMLElement).closest("button, a, input, textarea, select")) {
          event.stopPropagation();
        }
        props.onKeyDownCapture?.(event);
      }}>{children}</div>
  </TreeIndent.Provider>;
}

export function TreeItem<T>({ item, asChild = false, className, style, children, ...props }: {
  item: ItemInstance<T>; asChild?: boolean;
} & HTMLAttributes<HTMLDivElement>) {
  const indent = useContext(TreeIndent);
  const Comp = asChild ? Slot : "div";
  return <Comp {...item.getProps()} {...props} data-slot="tree-item"
    data-selected={item.isSelected()} data-focus={item.isFocused()}
    data-folder={item.isFolder()}
    style={{ "--tree-padding": `${item.getItemMeta().level * indent}px`, ...style } as CSSProperties}
    className={cn("min-w-0 rounded-md pl-[var(--tree-padding)] text-sm outline-none",
      "hover:bg-accent/50 data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
      "focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", className)}
    onClick={(event) => {
      if ((event.target as HTMLElement).closest("[data-tree-action]")) return;
      item.setFocused();
      item.getTree().setSelectedItems([item.getId()]);
      item.primaryAction();
      event.currentTarget.focus({ preventScroll: true });
      props.onClick?.(event);
    }}>
    {children}
  </Comp>;
}

export function TreeItemLabel<T>({ item, children, className, ...props }: {
  item: ItemInstance<T>;
} & HTMLAttributes<HTMLDivElement>) {
  return <div {...props} data-slot="tree-item-label"
    className={cn("flex min-w-0 items-start gap-1 rounded-md px-1 py-1.5", className)}>
    {item.isFolder() ? <button type="button" tabIndex={-1} data-tree-action="toggle"
      aria-label={`${item.isExpanded() ? "折叠" : "展开"}${item.getItemName()}`}
      className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      onClick={(event) => {
        event.stopPropagation();
        item.setFocused();
        if (item.isExpanded()) item.collapse(); else item.expand();
        item.getElement()?.focus({ preventScroll: true });
      }}>
      <ChevronRight aria-hidden="true" className={cn("size-3.5 transition-transform", item.isExpanded() && "rotate-90")} />
    </button> : <span aria-hidden="true" className="size-4 shrink-0" />}
    <div className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
      {children ?? item.getItemName()}
    </div>
  </div>;
}
