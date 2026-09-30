type PositionedNode = { id: string; x: number; y: number };
type Link = { source: string; target: string };

/** Project a force layout onto graph-distance rings around the document root. */
export function rootedCircleLayout(nodes: PositionedNode[], edges: Link[], rootId?: string) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const root = rootId && nodeIds.has(rootId) ? nodes.find((node) => node.id === rootId) : undefined;
  const neighbors = new Map(nodes.map((node) => [node.id, new Set<string>()]));
  for (const edge of edges) {
    neighbors.get(edge.source)?.add(edge.target);
    neighbors.get(edge.target)?.add(edge.source);
  }
  const depth = new Map<string, number>();
  if (root) {
    const queue = [root.id];
    depth.set(root.id, 0);
    for (let index = 0; index < queue.length; index++) {
      const id = queue[index];
      for (const neighbor of neighbors.get(id) ?? []) {
        if (depth.has(neighbor)) continue;
        depth.set(neighbor, depth.get(id)! + 1);
        queue.push(neighbor);
      }
    }
  }
  const outerDepth = Math.max(0, ...depth.values()) + 1;
  const rings = new Map<number, PositionedNode[]>();
  for (const node of nodes) {
    if (node.id === root?.id) continue;
    const level = depth.get(node.id) ?? outerDepth;
    rings.set(level, [...rings.get(level) ?? [], node]);
  }
  const origin = root ?? {
    x: nodes.reduce((sum, node) => sum + node.x, 0) / (nodes.length || 1),
    y: nodes.reduce((sum, node) => sum + node.y, 0) / (nodes.length || 1),
  };
  const positions = new Map<string, { x: number; y: number }>();
  if (root) positions.set(root.id, { x: 0, y: 0 });
  let radius = 0;
  for (const [, ring] of [...rings].sort(([a], [b]) => a - b)) {
    ring.sort((a, b) => Math.atan2(a.y - origin.y, a.x - origin.x)
      - Math.atan2(b.y - origin.y, b.x - origin.x) || a.id.localeCompare(b.id));
    radius = Math.max(radius + 8, ring.length * 8 / (2 * Math.PI));
    const start = Math.atan2(ring[0].y - origin.y, ring[0].x - origin.x);
    ring.forEach((node, index) => {
      const angle = start + 2 * Math.PI * index / ring.length;
      positions.set(node.id, { x: radius * Math.cos(angle), y: radius * Math.sin(angle) });
    });
  }
  return positions;
}
