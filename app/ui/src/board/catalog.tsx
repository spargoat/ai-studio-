// Node catalogue for the board. Port ids and types mirror studio/board.py.
import type { LucideIcon } from "lucide-react";
import { AudioLines, ChartNoAxesCombined, Clapperboard, FileText, Microscope, Route, Rss, ScanSearch, ScrollText } from "lucide-react";

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
  ad_feed: { type: "ad_feed", side: "intel", headline: "Ad Feed", icon: Rss, width: 228,
    inputs: [], outputs: [{ id: "ad", type: "ad" }] },
  ad_breakdown: { type: "ad_breakdown", side: "intel", headline: "Breakdown", icon: ScanSearch, width: 248,
    inputs: [{ id: "ad", type: "ad" }], outputs: [{ id: "ad", type: "ad" }] },
  funnel_route: { type: "funnel_route", side: "intel", headline: "Funnel Map", icon: Route, width: 420,
    inputs: [{ id: "ad", type: "ad" }], outputs: [{ id: "page", type: "page" }] },
  teardown: { type: "teardown", side: "intel", headline: "Teardown", icon: Microscope, width: 248,
    inputs: [{ id: "ad", type: "ad" }, { id: "page", type: "page" }], outputs: [{ id: "brief", type: "brief" }] },
  market_timeline: { type: "market_timeline", side: "intel", headline: "Market Timeline", icon: ChartNoAxesCombined, width: 432,
    inputs: [], outputs: [{ id: "ad", type: "ad" }] },
  brief: { type: "brief", side: "create", headline: "Brief", icon: FileText, width: 220,
    inputs: [{ id: "brief", type: "brief" }], outputs: [{ id: "brief", type: "brief" }] },
  script: { type: "script", side: "create", headline: "Script", icon: ScrollText, width: 220, needsKey: "llm",
    inputs: [{ id: "brief", type: "brief" }], outputs: [{ id: "script", type: "script" }, { id: "hooks", type: "hooks" }] },
  voice: { type: "voice", side: "create", headline: "Voiceover", icon: AudioLines, width: 220, needsKey: "elevenlabs",
    inputs: [{ id: "script", type: "script" }], outputs: [{ id: "audio", type: "audio" }] },
  video: { type: "video", side: "create", headline: "Video", icon: Clapperboard, width: 220, needsKey: "fal",
    inputs: [{ id: "audio", type: "audio" }, { id: "still", type: "still" }], outputs: [{ id: "video", type: "video" }] },
};

export const PALETTE: { side: Side; label: string; types: string[] }[] = [
  { side: "intel", label: "Intel · charts", types: ["market_timeline", "funnel_route", "ad_feed", "ad_breakdown", "teardown"] },
  { side: "create", label: "Create · pipeline", types: ["brief", "script", "voice", "video"] },
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
  audio: "#3ecf8e", video: "#ff7a8a", still: "#e5a54b", page: "#5ab0ff",
};
