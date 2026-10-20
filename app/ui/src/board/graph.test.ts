import assert from "node:assert/strict";
import { applyConnection, placeNode } from "./graph.ts";

const timeline = { x: 0, y: 0, width: 340, height: 280 };
const feed = { x: 380, y: 0, width: 268, height: 280 };
const size = { width: 268, height: 180 };

const free = placeNode([timeline, feed], { x: 800, y: 40 }, size);
assert.deepEqual(free, { x: 800, y: 40 });

const nudged = placeNode([timeline, feed], { x: 40, y: 40 }, size);
assert.ok(nudged.x >= timeline.x + timeline.width || nudged.y >= timeline.y + timeline.height, JSON.stringify(nudged));
assert.ok(nudged.x !== 40 || nudged.y !== 40);

const stacked = placeNode([timeline], { x: 80, y: 80 }, size);
assert.ok(stacked.y >= timeline.height || stacked.x >= timeline.width, JSON.stringify(stacked));

const ctx = {
  hasNode: (id: string) => id === "timeline" || id === "breakdown" || id === "feed",
  port: (id: string, direction: "in" | "out", handle?: string | null) => {
    if (handle === "ad" && ((direction === "out" && (id === "timeline" || id === "feed")) || (direction === "in" && id === "breakdown"))) return "ad";
    if (handle === "page") return "page";
    return null;
  },
  label: (id: string) => (id === "breakdown" ? "Breakdown" : id),
};

const wires = [{ id: "e-feed", source: "feed", target: "breakdown", sourceHandle: "ad", targetHandle: "ad" }];
const replaced = applyConnection(wires, { source: "timeline", target: "breakdown", sourceHandle: "ad", targetHandle: "ad" }, ctx);
assert.equal(replaced.ok, true);
assert.equal(replaced.edges.length, 1);
assert.equal(replaced.edges[0].source, "timeline");
assert.match(replaced.message, /Replaced the wire into Breakdown/);

const fresh = applyConnection([], { source: "timeline", target: "breakdown", sourceHandle: "ad", targetHandle: "ad" }, ctx);
assert.equal(fresh.ok, true);
assert.equal(fresh.edges.length, 1);
assert.equal(fresh.message, "");

const mismatch = applyConnection([], { source: "timeline", target: "breakdown", sourceHandle: "ad", targetHandle: "page" }, ctx);
assert.equal(mismatch.ok, false);
assert.equal(mismatch.edges.length, 0);
assert.match(mismatch.message, /Cannot connect/);

const otherTab = applyConnection([], { source: "timeline", target: "missing", sourceHandle: "ad", targetHandle: "ad" }, ctx);
assert.equal(otherTab.ok, false);
assert.match(otherTab.message, /other tab/);

console.log("graph tests ok");
