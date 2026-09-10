"use client";

import { useState } from "react";
import { hotkeysCoreFeature, selectionFeature, syncDataLoaderFeature,
  type TreeInstance, type Updater } from "@headless-tree/core";
import { useTree } from "@headless-tree/react";

export type DocumentTreeNode = { name: string; children: string[]; defaultExpanded?: boolean };
export type DocumentTreeData<T extends DocumentTreeNode> = { rootId: string; nodes: Map<string, T> };

const apply = <T,>(update: Updater<T>, previous: T): T =>
  typeof update === "function" ? (update as (value: T) => T)(previous) : update;

/** A synchronous snapshot: new nodes receive defaults, existing nodes retain user choices.
 * Callers key the owning component by document/user/run/projection identity. */
export function useDocumentTree<T extends DocumentTreeNode>(data: DocumentTreeData<T>,
  onActivate?: (node: T, tree: TreeInstance<T>) => void) {
  const [view, setView] = useState(() => ({
    data,
    expandedItems: [...data.nodes].filter(([, node]) => node.defaultExpanded).map(([id]) => id),
    selectedItems: [] as string[], focusedItem: null as string | null,
  }));
  // Update the data and state together before rendering the new snapshot. Passing
  // a new controlled expandedItems array makes Headless Tree rebuild its item
  // metadata, including when only children change. No stale removed item is read.
  if (view.data !== data) {
    const expandedItems = [...view.expandedItems.filter((id) => data.nodes.has(id)),
      ...[...data.nodes].filter(([id, node]) => !view.data.nodes.has(id) && node.defaultExpanded)
        .map(([id]) => id)];
    const expanded = new Set(expandedItems);
    const visible = new Set<string>();
    const parents = new Map<string, string>();
    for (const [id, node] of data.nodes) for (const child of node.children) parents.set(child, id);
    const pending = [...(data.nodes.get(data.rootId)?.children ?? [])];
    while (pending.length) {
      const id = pending.pop()!;
      if (visible.has(id)) continue;
      visible.add(id);
      if (expanded.has(id)) pending.push(...(data.nodes.get(id)?.children ?? []));
    }
    // A refreshed forest may reparent a focused entity under a closed branch.
    // Keep a visible keyboard entry even when the entity itself still exists.
    let focusedItem = view.focusedItem;
    while (focusedItem && !visible.has(focusedItem)) focusedItem = parents.get(focusedItem) ?? null;
    setView({ data,
      expandedItems,
      selectedItems: view.selectedItems.filter((id) => data.nodes.has(id)),
      focusedItem,
    });
  }
  return useTree<T>({
    rootItemId: view.data.rootId,
    state: { expandedItems: view.expandedItems, selectedItems: view.selectedItems, focusedItem: view.focusedItem },
    setExpandedItems: (value) => setView((previous) => ({ ...previous,
      expandedItems: [...new Set(apply(value, previous.expandedItems))] })),
    setSelectedItems: (value) => setView((previous) => ({ ...previous,
      selectedItems: apply(value, previous.selectedItems) })),
    setFocusedItem: (value) => setView((previous) => ({ ...previous,
      focusedItem: apply(value, previous.focusedItem) })),
    getItemName: (item) => item.getItemData().name,
    isItemFolder: (item) => item.getItemData().children.length > 0,
    dataLoader: {
      getItem: (id) => view.data.nodes.get(id)!,
      getChildren: (id) => view.data.nodes.get(id)?.children ?? [],
    },
    onPrimaryAction: (item) => onActivate?.(item.getItemData(), item.getTree()),
    hotkeys: {
      customActivate: { hotkey: "Enter", preventDefault: true, handler: (_, tree) => {
        const item = tree.getFocusedItem();
        if (!item) return;
        tree.setSelectedItems([item.getId()]);
        item.primaryAction();
      } },
      customSelect: { hotkey: "Space", preventDefault: true, handler: (_, tree) => {
        const item = tree.getFocusedItem();
        if (item) tree.setSelectedItems([item.getId()]);
      } },
      // 1.6.x matches overrides before merging presets; keep the hotkey string
      // even for disabled multi-selection actions.
      selectAll: { hotkey: "Control+KeyA", isEnabled: () => false },
      selectUpwards: { hotkey: "Shift+ArrowUp", isEnabled: () => false },
      selectDownwards: { hotkey: "Shift+ArrowDown", isEnabled: () => false },
      toggleSelectedItem: { hotkey: "Control+Space", isEnabled: () => false },
    },
    features: [syncDataLoaderFeature, selectionFeature, hotkeysCoreFeature],
  });
}
