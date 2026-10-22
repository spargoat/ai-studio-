// Node catalogue for the board. Port ids and types mirror studio/board.py.
import type { LucideIcon } from "lucide-react";
import { AudioLines, ChartNoAxesCombined, Clapperboard, FileText, Image as ImageIcon, Link2, Microscope, PanelTop, Route, Rss, ScanSearch, ScrollText, StickyNote, Waypoints, Workflow } from "lucide-react";

export type Side = "intel" | "create";
export type Port = { id: string; type: string };

export type NodeSpec = {
  type: string;
  side: Side;
  headline: string;
  icon: LucideIcon;
  inputs: Port[];
  outputs: Port[];
  needsKey?: "llm" | "elevenlabs" | "fal";
  width: number;
};

export const CATALOG: Record<string, NodeSpec> = {
  ad_feed: { type: "ad_feed", side: "intel", headline: "Ad Feed", icon: Rss, width: 280,
    inputs: [], outputs: [{ id: "ad", type: "ad" }] },
  ad_breakdown: { type: "ad_breakdown", side: "intel", headline: "Breakdown", icon: ScanSearch, width: 248,
    inputs: [{ id: "ad", type: "ad" }], outputs: [{ id: "ad", type: "ad" }] },
  funnel_route: { type: "funnel_route", side: "intel", headline: "Funnel Map", icon: Route, width: 420,
    inputs: [{ id: "ad", type: "ad" }], outputs: [{ id: "page", type: "page" }] },
  teardown: { type: "teardown", side: "intel", headline: "Teardown", icon: Microscope, width: 280,
    inputs: [{ id: "ad", type: "ad" }, { id: "page", type: "page" }], outputs: [{ id: "brief", type: "brief" }] },
  funnel_step: { type: "funnel_step", side: "intel", headline: "Funnel step", icon: Waypoints, width: 200,
    inputs: [{ id: "step", type: "step" }], outputs: [{ id: "step", type: "step" }, { id: "page", type: "page" }] },
  market_timeline: { type: "market_timeline", side: "intel", headline: "Market Timeline", icon: ChartNoAxesCombined, width: 432,
    inputs: [], outputs: [{ id: "ad", type: "ad" }] },
  brief: { type: "brief", side: "create", headline: "Brief", icon: FileText, width: 260,
    inputs: [{ id: "brief", type: "brief" }, { id: "source", type: "step" }], outputs: [{ id: "brief", type: "brief" }] },
  landing: { type: "landing", side: "create", headline: "Landing page", icon: PanelTop, width: 240,
    inputs: [{ id: "brief", type: "brief" }, { id: "source", type: "step" }], outputs: [{ id: "page", type: "page" }] },
  script: { type: "script", side: "create", headline: "Script", icon: ScrollText, width: 260, needsKey: "llm",
    inputs: [{ id: "brief", type: "brief" }, { id: "source", type: "step" }], outputs: [{ id: "script", type: "script" }, { id: "hooks", type: "hooks" }] },
  voice: { type: "voice", side: "create", headline: "Voiceover", icon: AudioLines, width: 240, needsKey: "elevenlabs",
    inputs: [{ id: "script", type: "script" }], outputs: [{ id: "audio", type: "audio" }] },
  still: { type: "still", side: "create", headline: "Image", icon: ImageIcon, width: 260, needsKey: "fal",
    inputs: [{ id: "brief", type: "brief" }], outputs: [{ id: "still", type: "still" }] },
  video: { type: "video", side: "create", headline: "Video", icon: Clapperboard, width: 240, needsKey: "fal",
    inputs: [{ id: "audio", type: "audio" }, { id: "still", type: "still" }], outputs: [{ id: "video", type: "video" }] },
  note: { type: "note", side: "create", headline: "Note", icon: StickyNote, width: 260, inputs: [], outputs: [] },
  quick_link: { type: "quick_link", side: "create", headline: "Quick link", icon: Link2, width: 260, inputs: [], outputs: [] },
  funnel_stage: { type: "funnel_stage", side: "create", headline: "Funnel stage", icon: Workflow, width: 260, inputs: [{ id: "flow", type: "flow" }], outputs: [{ id: "flow", type: "flow" }] },
  ad_reference: { type: "ad_reference", side: "intel", headline: "Source ad", icon: Rss, width: 260, inputs: [], outputs: [] },
  kit_master: { type: "kit_master", side: "create", headline: "Strategy kit", icon: Workflow, width: 370, inputs: [], outputs: [] },
};

export const PALETTE: { side: Side; label: string; types: string[] }[] = [
  { side: "intel", label: "Intel · charts", types: ["market_timeline", "funnel_route", "ad_feed", "ad_breakdown", "teardown"] },
  { side: "create", label: "Create · pipeline", types: ["brief", "landing", "script", "voice", "still", "video"] },
  { side: "create", label: "Canvas · map and access", types: ["funnel_stage", "note", "quick_link"] },
];

export const REFERENCE_LABEL = "pattern reference for ideation only, NOT copy-blueprint";
export const LONGEVITY_NOTE = "a signal, not proof of spend or results";

export function portType(nodeType: string, direction: "in" | "out", handle?: string | null) {
  const spec = CATALOG[nodeType];
  if (!spec || !handle) return null;
  const ports = direction === "out" ? spec.outputs : spec.inputs;
  return ports.find((p) => p.id === handle)?.type ?? null;
}

export const PORT_COLOR: Record<string, string> = {
  ad: "#5ab0ff", brief: "#7c6cff", script: "#c9a0ff", hooks: "#c9a0ff",
  audio: "#3ecf8e", video: "#ff7a8a", still: "#e5a54b", page: "#5ab0ff", step: "#e2a65a", flow: "#a98cff",
};
