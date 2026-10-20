// Placement and wires for the board. Kept free of React so the rules can be tested on their own.

export type Box = { x: number; y: number; width: number; height: number };
export type Wire = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
};

const GAP = 28;

function overlaps(x: number, y: number, size: { width: number; height: number }, occupied: Box[]) {
  return occupied.some((n) =>
    x < n.x + n.width + GAP && x + size.width + GAP > n.x &&
    y < n.y + n.height + GAP && y + size.height + GAP > n.y);
}

/** Drop point when it is free; otherwise the nearest open cell on the same tab. */
export function placeNode(occupied: Box[], desired: { x: number; y: number }, size: { width: number; height: number }) {
  const x0 = Math.round(desired.x);
  const y0 = Math.round(desired.y);
  if (!overlaps(x0, y0, size, occupied)) return { x: x0, y: y0 };
  const stepY = Math.max(size.height, 160) + GAP;
  const stepX = size.width + GAP;
  for (let row = 0; row < 16; row++) {
    for (let col = 0; col < 8; col++) {
      if (row === 0 && col === 0) continue;
      const x = x0 + col * stepX;
      const y = y0 + row * stepY;
      if (!overlaps(x, y, size, occupied)) return { x, y };
    }
  }
  return { x: x0 + GAP, y: y0 + size.height + GAP };
}

export function applyConnection(
  edges: Wire[],
  c: { source?: string | null; target?: string | null; sourceHandle?: string | null; targetHandle?: string | null },
  ctx: {
    hasNode: (id: string) => boolean;
    port: (id: string, direction: "in" | "out", handle?: string | null) => string | null;
    label: (id: string) => string;
  },
): { edges: Wire[]; ok: boolean; message: string } {
  if (!c.source || !c.target || !c.sourceHandle || !c.targetHandle) {
    return { edges, ok: false, message: "Drop the wire on a coloured port." };
  }
  if (!ctx.hasNode(c.source) || !ctx.hasNode(c.target)) {
    return { edges, ok: false, message: "That node is on the other tab. Open it and connect the port there." };
  }
  const outType = ctx.port(c.source, "out", c.sourceHandle);
  const inType = ctx.port(c.target, "in", c.targetHandle);
  if (!outType || !inType || outType !== inType) {
    return { edges, ok: false, message: `Cannot connect ${outType || "that port"} to ${inType || "that port"}.` };
  }
  const occupied = edges.filter((e) => e.target === c.target && e.targetHandle === c.targetHandle);
  const already = occupied.find((e) => e.source === c.source && e.sourceHandle === c.sourceHandle);
  if (already && occupied.length === 1) return { edges, ok: true, message: "" };
  const next = edges.filter((e) => !(e.target === c.target && e.targetHandle === c.targetHandle));
  next.push({
    id: `e-${c.source}-${c.sourceHandle}-${c.target}-${c.targetHandle}`,
    source: c.source,
    target: c.target,
    sourceHandle: c.sourceHandle,
    targetHandle: c.targetHandle,
  });
  const message = occupied.length
    ? `Replaced the wire into ${ctx.label(c.target)} (${c.targetHandle}).`
    : "";
  return { edges: next, ok: true, message };
}
