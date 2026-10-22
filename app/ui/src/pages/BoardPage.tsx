import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, applyNodeChanges, applyEdgeChanges,
  useReactFlow, type Connection, type Edge, type Node, type NodeChange, type EdgeChange, type OnConnect, type FinalConnectionState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hash, Play, Copy, Save, Plus, Loader2 } from "lucide-react";
import { api, fileUrl } from "../lib/api";
import { toast } from "../components/Toast";
import { CATALOG, PALETTE, PORT_COLOR, portType, type Side } from "../board/catalog";
import { applyConnection, placeNode, type Box } from "../board/graph";
import { buildKit, buildKitPipeline, kitById, KITS } from "../board/kits";
import { BoardCtx, nodeTypes, type Artefact, type BoardData, type RouteStep } from "../board/nodes";

type Lens = Side | "both";
type FlowNode = Node<BoardData, "board">;
type GraphNode = { id: string; type: string; position: { x: number; y: number }; enabled?: boolean; config?: Record<string, string> };
type GraphEdge = { id: string; source: string; target: string; sourceHandle?: string; targetHandle?: string; label?: string };
type FunnelMeta = { name: string; stage: string; owner: string; channel: string; sourceAdIds: string[] };
const EMPTY_FUNNEL: FunnelMeta = { name: "", stage: "mapping", owner: "", channel: "", sourceAdIds: [] };

function feedConfig(type: string, config: Record<string, string>) {
  const cfg = { ...(config || {}) };
  // The talking-head preset stored the fixture window. On the live tracker that
  // window (live + 30 days) is empty for accounts whose dropdown count is not.
  if (type === "ad_feed" && cfg.status === "live" && cfg.date_from === "2026-07-01" && cfg.date_to === "2026-10-18" && String(cfg.longevity_min || "") === "30") {
    cfg.status = "";
    cfg.date_from = "";
    cfg.date_to = "";
    cfg.longevity_min = "";
  }
  return cfg;
}

function presentSteps(steps: RouteStep[]): RouteStep[] {
  const seen = new Set<string>();
  const unique: RouteStep[] = [];
  for (const step of steps) {
    const label = /^\d{6,}$/.test(step.label || "") ? "Ad creative" : (step.label || "Step");
    const key = `${step.kind || "step"}|${label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push({ ...step, label });
  }
  if (unique.length <= 12) return unique;
  return [...unique.slice(0, 11), { n: 12, kind: "step", label: `${unique.length - 11} more steps collapsed`, state: "unknown" }];
}

function toFlow(nodes: GraphNode[]): FlowNode[] {
  let selectTimeline = true;
  const flow: FlowNode[] = (nodes || []).filter((n) => CATALOG[n.type]).map((n) => {
    const selected = selectTimeline && n.type === "market_timeline";
    if (selected) selectTimeline = false;
    return {
      id: n.id, type: "board" as const, position: n.position || { x: 0, y: 0 }, selected,
      data: { nodeType: n.type, enabled: n.enabled !== false, config: feedConfig(n.type, n.config || {}) },
    };
  });
  return flow;
}

function serialize(nodes: FlowNode[], edges: Edge[], variables: Record<string, string>, funnel: FunnelMeta) {
  return {
    variables, funnel,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.nodeType, position: n.position, enabled: n.data.enabled, config: n.data.config || {} })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle, label: e.label })),
  };
}

function savedLabel(iso?: string) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return `Saved ${iso}`;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "Saved just now";
  if (mins < 60) return `Saved ${mins} min ago`;
  return `Saved ${iso.slice(0, 16).replace("T", " ")}`;
}

function stageView(stage: { runs?: { status: string; review_status?: string; actual_cost_usd?: number; est_cost_usd?: number }[] } | undefined, jobStatus?: string) {
  const run = stage?.runs?.length ? stage.runs[stage.runs.length - 1] : undefined;
  if (!run) {
    if (jobStatus === "created" || jobStatus === "running" || jobStatus === "partial") return { status: "waiting", actual: null as number | null, ran: false };
    return { status: "idle", actual: null as number | null, ran: false };
  }
  let status = run.status;
  if (run.status === "succeeded" && run.review_status === "pending") status = "needs review";
  else if (run.status === "succeeded") status = "done";
  else if (run.status === "failed" || run.status === "blocked_budget") status = "failed";
  else if (run.status === "running") status = "running";
  return { status, actual: run.actual_cost_usd ?? 0, ran: true };
}

function nodeBox(n: FlowNode): Box {
  const spec = CATALOG[n.data.nodeType];
  return {
    x: n.position.x, y: n.position.y,
    width: n.measured?.width || spec.width,
    height: n.measured?.height || (n.data.nodeType === "market_timeline" ? 360 : spec.side === "intel" ? 250 : 180),
  };
}

function Canvas() {
  const qc = useQueryClient();
  const { screenToFlowPosition, fitView } = useReactFlow();
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta") });
  const { data: list } = useQuery({ queryKey: ["boards"], queryFn: () => api<any[]>("/api/boards") });
  const [boardId, setBoardId] = useState(() => window.localStorage.getItem("studio:board-id") || "talking_head");
  const { data: board } = useQuery({ queryKey: ["board", boardId], queryFn: () => api<any>(`/api/boards/${boardId}`), enabled: !!boardId });
  const [nodes, setNodes] = useState<FlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [name, setName] = useState("Talking head");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [funnel, setFunnel] = useState<FunnelMeta>(EMPTY_FUNNEL);
  const [tab, setTab] = useState<Lens>("both");
  const [estimates, setEstimates] = useState<Record<string, number>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState("");
  const [query, setQuery] = useState("");
  const [pane, setPane] = useState<"config" | "output" | "runs">("config");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => { window.localStorage.setItem("studio:board-id", boardId); }, [boardId]);
  useEffect(() => {
    if (list?.length && !list.some((row: { id: string }) => row.id === boardId)) setBoardId("talking_head");
  }, [list, boardId]);

  useEffect(() => {
    if (!board) return;
    const stamp = boardId + (board.updated_at || "");
    if (loaded === stamp) return;
    setLoaded(stamp);
    setNodes(toFlow(board.graph?.nodes || []));
    setEdges((board.graph?.edges || []).map((e: GraphEdge) => ({ ...e })));
    setName(board.name || "Board");
    setVariables(board.graph?.variables || {});
    setFunnel({ ...EMPTY_FUNNEL, ...(board.graph?.funnel || {}) });
    setEstimates(board.estimates || {});
    if (board.last_job_id) setJobId(board.last_job_id);
  }, [board, boardId, loaded]);

  const feed = nodes.find((n) => n.data.nodeType === "ad_feed");
  const timeline = nodes.find((n) => n.data.nodeType === "market_timeline");
  const previewQs = new URLSearchParams({
    advertiser: feed?.data.config.advertiser || "",
    page_id: feed?.data.config.page_id || "",
    status: feed?.data.config.status || "",
    date_from: feed?.data.config.date_from || "",
    date_to: feed?.data.config.date_to || "",
    longevity_min: feed?.data.config.longevity_min || "",
    limit: feed?.data.config.limit || "40",
    offset: feed?.data.config.offset || "0",
    ad_id: feed?.data.config.ad_id || "",
    event_id: timeline?.data.config.event_id || "",
  }).toString();
  const previewQuery = useQuery({
    queryKey: ["intel-preview", previewQs],
    queryFn: () => api<any>(`/api/intel/preview?${previewQs}`),
    placeholderData: keepPreviousData,
    enabled: nodes.length > 0,
  });
  const preview = previewQuery.data || board?.preview || null;
  const sources = useMemo(() => {
    const attached = nodes.filter((node) => node.data.nodeType === "ad_reference" && node.data.config.ad_id).map((node) => ({
      id: node.data.config.ad_id, title: node.data.config.title || node.data.config.ad_id,
      url: node.data.config.url || "", status: node.data.config.source_status || "unknown",
    }));
    const pickedId = feed?.data.config.ad_id;
    const waterfall = (preview?.feed?.evidence?.fields?.waterfall || []) as { id?: string; headline?: string; library_url?: string; landing_url?: string; status?: string }[];
    const picked = waterfall.find((ad) => ad.id === pickedId);
    if (pickedId && picked && !attached.some((source) => source.id === pickedId)) attached.unshift({
      id: pickedId, title: picked.headline || pickedId, url: picked.library_url || picked.landing_url || "",
      status: preview?.feed?.evidence?.origin === "live" ? picked.status || "unknown" : "fixture",
    });
    return attached;
  }, [nodes, feed?.data.config.ad_id, preview]);
  const accountsQuery = useQuery({ queryKey: ["intel-accounts"], queryFn: () => api<any>("/api/intel/accounts") });
  const directoryQuery = useQuery({ queryKey: ["intel-funnels"], queryFn: () => api<any>("/api/intel/funnels") });
  const { data: job } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<any>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = q.state.data?.job?.status;
      return !s || s === "created" || s === "running" || s === "partial" ? 2000 : false;
    },
  });

  const keys = useMemo(() => {
    const out: Record<string, boolean> = {};
    for (const row of meta?.connections || []) out[row.id] = !!row.set;
    return out;
  }, [meta]);
  const persona = (meta?.personas || []).find((p: { id: string; display?: string }) => p.id === (variables.persona || ""))?.display || variables.persona || "";
  const wired = useMemo(() => {
    const set = new Set<string>();
    for (const edge of edges) {
      if (edge.sourceHandle) set.add(`${edge.source}:out:${edge.sourceHandle}`);
      if (edge.targetHandle) set.add(`${edge.target}:in:${edge.targetHandle}`);
    }
    return set;
  }, [edges]);

  const toggle = useCallback((id: string) => {
    setNodes((ns) => ns.map((n) => n.id === id ? { ...n, data: { ...n.data, enabled: !n.data.enabled } } : n));
  }, []);
  const follow = useCallback((id: string) => {
    const node = nodes.find((n) => n.id === id);
    const spec = node && CATALOG[node.data.nodeType];
    if (!spec) return;
    setTab(spec.side);
    setTimeout(() => fitView({ nodes: [{ id }], padding: 0.4, duration: 200 }), 40);
  }, [nodes, fitView]);
  const pickEvent = useCallback((id: string, event: { id: string; ad_id?: string }) => {
    const adId = event.ad_id || "";
    setNodes((ns) => ns.map((n) => {
      if (n.id === id) {
        return { ...n, selected: true, data: { ...n.data, config: { ...n.data.config, event_id: event.id, ad_id: adId } } };
      }
      const next = { ...n, selected: false };
      if (n.data.nodeType === "ad_feed") next.data = { ...n.data, config: { ...n.data.config, ad_id: adId } };
      return next;
    }));
    setPane("config");
  }, []);
  const setConfig = useCallback((id: string, patch: Record<string, string>) => {
    setNodes((ns) => ns.map((n) => {
      if (n.id === id) return { ...n, data: { ...n.data, config: { ...n.data.config, ...patch } } };
      if (n.id === `${id}-brief` && ["offer", "source_ad_id", "source_url"].some((key) => key in patch)) {
        return { ...n, data: { ...n.data, config: { ...n.data.config,
          ...Object.fromEntries(["offer", "source_ad_id", "source_url"].filter((key) => key in patch).map((key) => [key, patch[key]])) } } };
      }
      return n;
    }));
  }, []);
  const addAds = useCallback((ads: { id?: string; headline?: string; advertiser?: string; status?: string; library_url?: string; landing_url?: string; as_of?: string }[]) => {
    const usable = ads.filter((ad) => ad.id);
    setNodes((ns) => {
      const next = [...ns];
      usable.forEach((ad, i) => {
        const id = `source-ad-${ad.id}`;
        if (next.some((n) => n.id === id)) return;
        next.push({ id, type: "board", position: placeNode(next.map(nodeBox), { x: 1600 + (i % 4) * 300, y: 100 + Math.floor(i / 4) * 310 }, { width: 260, height: 260 }),
          data: { nodeType: "ad_reference", enabled: true, config: { title: ad.headline || ad.id || "Source ad", url: ad.library_url || ad.landing_url || "", advertiser: ad.advertiser || "", source_status: ad.status || "unknown", as_of: ad.as_of || "", ad_id: ad.id || "", notes: "Observed source record. Add your own breakdown separately." } } });
      });
      return next;
    });
    setFunnel((prev) => ({ ...prev, sourceAdIds: [...new Set([...prev.sourceAdIds, ...usable.map((ad) => ad.id || "")])] }));
    setTab("both");
    toast.success(`${usable.length} source ads added to canvas`);
  }, []);
  const addKit = useCallback((kitId: string, desired?: { x: number; y: number }) => {
    const definition = kitById(kitId);
    if (!definition) return;
    const start = desired || { x: Math.max(0, ...nodes.map((n) => n.position.x + (CATALOG[n.data.nodeType]?.width || 260))) + 160, y: 0 };
    const at = placeNode(nodes.map(nodeBox), start, { width: 370, height: 620 });
    const prefix = `${kitId}-${Date.now().toString(36)}`;
    const kit = buildKit(kitId, prefix, at.x, at.y);
    const source = sources.find((row) => funnel.sourceAdIds.includes(row.id)) || sources[0];
    if (source) kit.nodes[0].config = { ...kit.nodes[0].config, source_ad_id: source.id,
      source_url: source.url, source_status: source.status };
    if (variables.offer) kit.nodes[0].config.offer = variables.offer;
    setNodes((ns) => [...ns, ...kit.nodes.map((n) => ({ id: n.id, type: "board" as const, position: n.position, data: { nodeType: n.type, enabled: n.enabled, config: n.config } }))]);
    setEdges((es) => [...es, ...kit.edges]);
    setFunnel((prev) => ({ ...prev, name: prev.name || definition.name, stage: "mapping" }));
    setName((prev) => prev === "Untitled board" ? definition.name : prev);
    setTab("both");
    setTimeout(() => fitView({ nodes: [{ id: `${prefix}-overview` }], padding: 0.5, maxZoom: 1, duration: 250 }), 40);
    toast.success(`${definition.name} added. Save the board to keep it.`);
  }, [nodes, fitView, funnel.sourceAdIds, variables.offer, sources]);
  const buildKitPath = useCallback((masterId: string) => {
    const master = nodes.find((n) => n.id === masterId && n.data.nodeType === "kit_master");
    if (!master || master.data.config.production_built === "true") return;
    const cfg = master.data.config;
    const path = buildKitPipeline(cfg.kit_id, masterId, master.position.x + 470, master.position.y, cfg.offer, cfg.source_ad_id, cfg.source_url);
    if (path.nodes.some((n) => nodes.some((existing) => existing.id === n.id))) return;
    setNodes((ns) => [...ns.map((n) => n.id === masterId ? { ...n, data: { ...n.data, config: { ...n.data.config, production_built: "true" } } } : n),
      ...path.nodes.map((n) => ({ id: n.id, type: "board" as const, position: n.position, data: { nodeType: n.type, enabled: n.enabled, config: n.config } }))]);
    setEdges((es) => [...es, ...path.edges]);
    toast.success("Production path built. Save the board to keep it. Dry run is $0.");
  }, [nodes]);
  const expandKitMap = useCallback((masterId: string) => {
    const master = nodes.find((n) => n.id === masterId && n.data.nodeType === "kit_master");
    if (!master || master.data.config.map_expanded === "true") return;
    let stages: [string, string][] = [];
    try { stages = JSON.parse(master.data.config.stages || "[]"); } catch { return; }
    const cards = stages.map(([title, guidance], i) => ({ id: `${masterId}-map-${i + 1}`, type: "board" as const,
      position: { x: master.position.x + (i % 3) * 310, y: master.position.y + 720 + Math.floor(i / 3) * 310 },
      data: { nodeType: "funnel_stage", enabled: true, config: { title: `${i + 1}. ${title}`, status: "planned", notes: guidance } } }));
    if (cards.some((card) => nodes.some((n) => n.id === card.id))) return;
    setNodes((ns) => [...ns.map((n) => n.id === masterId ? { ...n, data: { ...n.data, config: { ...n.data.config, map_expanded: "true" } } } : n), ...cards]);
    setEdges((es) => [...es, ...cards.slice(1).map((card, i) => ({ id: `${masterId}-map-edge-${i + 1}`,
      source: cards[i].id, sourceHandle: "flow", target: card.id, targetHandle: "flow" }))]);
    toast.success("Stage map opened. Save the board to keep it.");
  }, [nodes]);
  const funnelSteps = useCallback(() => {
    const fields = (preview?.funnel?.evidence?.fields || {}) as { steps?: { n?: number; kind?: string; label?: string; url?: string }[]; ad_id?: string };
    return { steps: fields.steps || [], adId: fields.ad_id || "", asOf: preview?.funnel?.evidence?.data_as_of || "" };
  }, [preview]);
  const mapFunnel = useCallback((funnelId: string, explicit?: RouteStep[]) => {
    const read = funnelSteps();
    const steps = presentSteps(explicit?.length ? explicit : read.steps);
    const adId = read.adId;
    const asOf = read.asOf;
    if (!steps.length) { toast.info("No public steps in this read. Pick a funnel on the block, or replicate to scaffold ours."); return; }
    setTab("both");
    setNodes((ns) => {
      const funnel = ns.find((n) => n.id === funnelId);
      if (!funnel) return ns;
      const next = [...ns];
      steps.forEach((step, i) => {
        const id = `step-${funnelId}-${step.n || i + 1}`;
        if (next.some((n) => n.id === id)) return;
        const spec = CATALOG.funnel_step;
        next.push({
          id, type: "board",
          position: placeNode(next.map(nodeBox), { x: funnel.position.x + 480 + i * (spec.width + 36), y: funnel.position.y }, { width: spec.width, height: 180 }),
          data: { nodeType: "funnel_step", enabled: true, config: {
            title: `${step.n || i + 1}. ${step.label || "Step"}`,
            n: String(step.n || i + 1), kind: step.kind || "", label: step.label || "", url: step.url || "", ad_id: adId, data_as_of: asOf,
          } },
        });
      });
      return next;
    });
    if (preview?.funnel?.evidence?.origin !== "live") setEdges((es) => {
      const add = [...es];
      steps.forEach((step, i) => {
        if (i === 0) return;
        const source = `step-${funnelId}-${steps[i - 1].n || i}`;
        const target = `step-${funnelId}-${step.n || i + 1}`;
        const id = `e-${source}-step-${target}-step`;
        if (!add.some((e) => e.id === id)) add.push({ id, source, target, sourceHandle: "step", targetHandle: "step" });
      });
      return add;
    });
    toast.success(preview?.funnel?.evidence?.origin === "live" ? "Tracker route cards mapped. Order and links need verification." : "Public funnel mapped on the canvas");
  }, [funnelSteps, preview]);
  const mapDirectoryFunnel = useCallback(async (funnelId: string, funnelName: string) => {
    setBusyId(`funnel:${funnelId}`);
    try {
      const route = await api<{ steps: RouteStep[]; note?: string; data_as_of?: string; funnel_name?: string }>(`/api/intel/funnels/${encodeURIComponent(funnelId)}/route`);
      const title = route.funnel_name || funnelName || "Funnel";
      const steps = presentSteps(route.steps || []);
      if (!steps.length) { toast.error(route.note || "No steps in this route read."); return; }
      const prefix = `route-${Date.now().toString(36)}`;
      const x = Math.max(0, ...nodes.map((n) => n.position.x + (CATALOG[n.data.nodeType]?.width || 260))) + 160;
      const noteId = `${prefix}-head`;
      setNodes((ns) => [
        ...ns,
        { id: noteId, type: "board", position: { x, y: 0 }, data: { nodeType: "note", enabled: true, config: { title, notes: "Tracker route read. Repeated cards are collapsed. Confirm sequence and links before using this as a process map." } } },
        ...steps.map((step, i) => ({ id: `${prefix}-${i}`, type: "board" as const,
          position: { x: x + (i % 4) * 250, y: 290 + Math.floor(i / 4) * 250 },
          data: { nodeType: "funnel_step", enabled: true, config: { title: `${step.n || i + 1}. ${step.label || "Step"}`, n: String(step.n || i + 1), kind: step.kind || "", label: step.label || "Step", url: step.url || "", state: step.state || "unknown", source: step.source || "", data_as_of: route.data_as_of || "" } } }))
      ]);
      setTab("both");
      setTimeout(() => fitView({ nodes: [{ id: noteId }], padding: 0.4, duration: 250 }), 40);
      toast.success(`${steps.length} route cards added`);
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusyId(""); }
  }, [nodes, fitView]);
  const replicate = useCallback((funnelId: string, explicit?: RouteStep[]) => {
    const read = funnelSteps();
    const steps = presentSteps(explicit?.length ? explicit : read.steps);
    const adId = read.adId;
    const asOf = read.asOf;
    setTab("both");
    const ad = steps.find((s) => s.kind === "ad") || steps[0];
    const click = steps.find((s) => s.kind === "click");
    const land = steps.find((s) => s.kind === "landing") || steps[steps.length - 1];
    const briefId = `rep-${funnelId}-brief`;
    const scriptId = `rep-${funnelId}-script`;
    const voiceId = `rep-${funnelId}-voice`;
    const videoId = `rep-${funnelId}-video`;
    const landingId = `rep-${funnelId}-landing`;
    setNodes((ns) => {
      const funnel = ns.find((n) => n.id === funnelId);
      if (!funnel) return ns;
      const next = [...ns];
      const ensure = (id: string, nodeType: string, at: { x: number; y: number }, config: Record<string, string>) => {
        if (next.some((n) => n.id === id)) return;
        const spec = CATALOG[nodeType];
        next.push({
          id, type: "board",
          position: placeNode(next.map(nodeBox), at, { width: spec.width, height: 220 }),
          data: { nodeType, enabled: true, config },
        });
      };
      steps.forEach((step, i) => ensure(`step-${funnelId}-${step.n || i + 1}`, "funnel_step",
        { x: funnel.position.x + 480 + i * 236, y: funnel.position.y },
        { title: `${step.n || i + 1}. ${step.label || "Step"}`, n: String(step.n || i + 1), kind: step.kind || "", label: step.label || "", url: step.url || "", ad_id: adId, data_as_of: asOf }));
      const y = funnel.position.y + 320;
      const x = funnel.position.x + 480;
      ensure(briefId, "brief", { x, y }, {
        persona: variables.persona || "", offer: variables.offer || "", angle: "", length: "30", platform: "instagram",
      });
      ensure(scriptId, "script", { x: x + 300, y }, { script: variables.script || "" });
      ensure(voiceId, "voice", { x: x + 600, y }, {});
      ensure(videoId, "video", { x: x + 900, y }, { format: "talking_head", aspect: "9:16" });
      ensure(landingId, "landing", { x: x + 300, y: y + 300 }, {});
      return next;
    });
    setEdges((es) => {
      const add = [...es];
      const link = (source: string, sourceHandle: string, target: string, targetHandle: string) => {
        const id = `e-${source}-${sourceHandle}-${target}-${targetHandle}`;
        if (!add.some((e) => e.target === target && e.targetHandle === targetHandle)) {
          add.push({ id, source, target, sourceHandle, targetHandle });
        }
      };
      if (steps.length && preview?.funnel?.evidence?.origin !== "live" && !explicit?.length) steps.forEach((step, i) => {
        if (i === 0) return;
        link(`step-${funnelId}-${steps[i - 1].n || i}`, "step", `step-${funnelId}-${step.n || i + 1}`, "step");
      });
      if (ad) link(`step-${funnelId}-${ad.n || 1}`, "step", briefId, "source");
      link(briefId, "brief", scriptId, "brief");
      if (click) link(`step-${funnelId}-${click.n}`, "step", scriptId, "source");
      link(scriptId, "script", voiceId, "script");
      link(voiceId, "audio", videoId, "audio");
      link(briefId, "brief", landingId, "brief");
      if (land) link(`step-${funnelId}-${land.n || steps.length}`, "step", landingId, "source");
      return add;
    });
    toast.success(steps.length ? "Our funnel is scaffolded under the public steps" : "Brief, Script, Voiceover, Video and a landing outline are on the board");
  }, [funnelSteps, variables, preview]);

  const previewByType: Record<string, Artefact | null> = {
    ad_feed: preview?.feed, ad_breakdown: preview?.breakdown, funnel_route: preview?.funnel,
    teardown: preview?.teardown, market_timeline: preview?.timeline,
  };
  const stages: Record<string, any> = {};
  for (const stage of job?.stages || []) stages[stage.id] = stage;

  const view = useMemo(() => {
    const visible = tab === "both" ? nodes : nodes.filter((n) => CATALOG[n.data.nodeType]?.side === tab);
    const ids = new Set(visible.map((n) => n.id));
    const stubs: Node[] = [];
    const viewEdges: Edge[] = [];
    const selectedId = nodes.find((n) => n.selected)?.id;
    const paint = (n: FlowNode): FlowNode => {
      const sv = stageView(stages[n.id], job?.job?.status);
      const runs = stages[n.id]?.runs || [];
      const path = [...runs].reverse().find((r) => r.output_path)?.output_path;
      return { ...n, data: { ...n.data, preview: previewByType[n.data.nodeType] || null, status: sv.status, est: estimates[n.id] ?? 0, actual: sv.actual, ran: sv.ran, file: path ? fileUrl(path) : "" } };
    };
    for (const edge of edges) {
      const srcIn = ids.has(edge.source);
      const dstIn = ids.has(edge.target);
      const color = PORT_COLOR[portType(nodes.find((n) => n.id === edge.source)?.data.nodeType || "", "out", edge.sourceHandle) || ""] || "#6b6b76";
      const hot = edge.source === selectedId || edge.target === selectedId;
      const styled: Edge = { ...edge, type: "default", style: { stroke: color, strokeWidth: hot ? 2.25 : 1.5, opacity: hot ? 1 : 0.55 }, animated: hot };
      if (srcIn && dstIn) viewEdges.push(styled);
      else if (tab !== "both" && (srcIn || dstIn)) {
        const otherId = srcIn ? edge.target : edge.source;
        const other = nodes.find((n) => n.id === otherId);
        const anchor = nodes.find((n) => n.id === (srcIn ? edge.source : edge.target));
        if (!other || !anchor) continue;
        const stubId = `stub-${edge.id}`;
        const anchorWidth = CATALOG[anchor.data.nodeType]?.width || 240;
        stubs.push({
          id: stubId, type: "stub", draggable: false, selectable: false,
          position: srcIn
            ? { x: anchor.position.x + anchorWidth + 36, y: anchor.position.y + 12 }
            : { x: anchor.position.x - 212, y: anchor.position.y + 12 },
          data: { nodeType: other.data.nodeType, followId: other.id, side: srcIn ? "target" : "source" },
        });
        viewEdges.push(srcIn
          ? { ...styled, id: stubId, target: stubId, targetHandle: undefined }
          : { ...styled, id: stubId, source: stubId, sourceHandle: undefined });
      }
    }
    return { nodes: [...visible.map(paint), ...stubs], edges: viewEdges };
  }, [nodes, edges, tab, preview, job, estimates]);

  useEffect(() => {
    if (!nodes.length) return;
    const overview = nodes.find((n) => n.id.endsWith("-overview"));
    const focus = overview ? nodes.filter((n) => n.id.startsWith(overview.id.slice(0, -"overview".length)) &&
      (n.id.endsWith("-overview") || n.id.endsWith("-source") || /-(?:section|stage)-[1-4]$/.test(n.id))) : [];
    const t = setTimeout(() => fitView(focus.length ? { nodes: focus.map((n) => ({ id: n.id })), padding: 0.13, maxZoom: 0.9, duration: 200 }
      : { padding: 0.18, maxZoom: 1, duration: 200 }), 80);
    return () => clearTimeout(t);
  }, [tab, loaded, fitView]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((ns) => applyNodeChanges(changes.filter((c) => !("id" in c) || !String(c.id).startsWith("stub-")), ns) as FlowNode[]);
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes.filter((c) => !("id" in c) || !String(c.id).startsWith("stub-")), es));
  }, []);
  const wireCtx = useCallback((list: FlowNode[]) => ({
    hasNode: (id: string) => list.some((n) => n.id === id),
    port: (id: string, direction: "in" | "out", handle?: string | null) => {
      const node = list.find((n) => n.id === id);
      return node ? portType(node.data.nodeType, direction, handle) : null;
    },
    label: (id: string) => {
      const node = list.find((n) => n.id === id);
      return (node && CATALOG[node.data.nodeType]?.headline) || "node";
    },
  }), []);
  const edgesRef = useRef(edges);
  edgesRef.current = edges;
  const onConnect: OnConnect = useCallback((c: Connection) => {
    const result = applyConnection(edgesRef.current, c, wireCtx(nodes));
    if (result.message) (result.ok ? toast.success : toast.error)(result.message);
    if (result.ok) setEdges(result.edges);
  }, [nodes, wireCtx]);
  const onConnectEnd = useCallback((_event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    if (state.isValid !== false) return;
    const toId = state.toHandle?.nodeId;
    if (!toId || !nodes.some((n) => n.id === toId)) {
      toast.error(toId ? "That block is hidden by the category filter. Switch to Both to connect it." : "Drop the wire on a coloured port.");
      return;
    }
    const fromId = state.fromHandle?.nodeId;
    const src = nodes.find((n) => n.id === fromId);
    const dst = nodes.find((n) => n.id === toId);
    if (!src || !dst) return;
    const outType = portType(src.data.nodeType, "out", state.fromHandle?.id);
    const inType = portType(dst.data.nodeType, "in", state.toHandle?.id);
    if (outType && inType && outType !== inType) toast.error(`Cannot connect ${outType} to ${inType}.`);
  }, [nodes]);
  const isValid = useCallback((c: Connection | Edge) => applyConnection(edges, c, wireCtx(nodes)).ok, [edges, nodes, wireCtx]);

  const addNode = (type: string) => {
    const spec = CATALOG[type];
    if (!spec) return;
    setTab((current) => (current === "both" || current === spec.side ? current : "both"));
    const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
    const size = { width: spec.width, height: spec.side === "intel" ? 250 : 220 };
    setNodes((ns) => [...ns, {
      id, type: "board",
      position: placeNode(ns.map(nodeBox), { x: 48, y: 48 }, size),
      data: { nodeType: type, enabled: true, config: {} },
    }]);
  };

  const save = useMutation({
    mutationFn: async () => api(`/api/boards/${boardId}`, { method: "PUT", body: { name, graph: serialize(nodes, edges, variables, funnel) } }),
    onSuccess: (row: any) => { qc.setQueryData(["board", boardId], row); setEstimates(row.estimates || {}); setLoaded(boardId + (row.updated_at || "")); qc.invalidateQueries({ queryKey: ["boards"] }); toast.success("Board saved"); },
    onError: (e: Error) => toast.error(e.message),
  });
  const create = useMutation({
    mutationFn: async (blank: boolean) => api("/api/boards", { method: "POST", body: blank
      ? { name: "Untitled board", graph: { nodes: [], edges: [], variables: {}, funnel: EMPTY_FUNNEL } }
      : { name: `${(name || "Untitled board").replace(/ copy$/, "")} copy`, graph: serialize(nodes, edges, variables, funnel) } }),
    onSuccess: (row: any, blank) => {
      qc.setQueryData(["boards"], (previous: any[] | undefined) => [...(previous || []).filter((item) => item.id !== row.id), row]);
      qc.setQueryData(["board", row.id], row);
      setBoardId(row.id);
      qc.invalidateQueries({ queryKey: ["boards"] });
      const message = blank ? `New board “${row.name}” is ready.` : `Copied this board to “${row.name}”.`;
      setNotice(message);
      toast.success(message);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const run = useMutation({
    mutationFn: async () => {
      const saved = await api<any>(`/api/boards/${boardId}`, { method: "PUT", body: { name, graph: serialize(nodes, edges, variables, funnel) } });
      qc.setQueryData(["board", boardId], saved);
      setLoaded(boardId + (saved.updated_at || ""));
      const row = await api<{ id: string; mode: string; pump?: boolean }>(`/api/boards/${boardId}/run`, { body: { mode: "dry" } });
      setJobId(row.id);
      let status = "started";
      const paint = async () => {
        const fresh = await api<any>(`/api/jobs/${row.id}`);
        qc.setQueryData(["job", row.id], fresh);
        return fresh?.job?.status as string | undefined;
      };
      for (let i = 0; i < 80; i++) {
        let action = "";
        if (row.pump) {
          const step = await api<{ status?: string; action?: string; stage?: string; ok?: boolean; error?: string }>(`/api/jobs/${row.id}/advance`, { method: "POST", body: {} });
          status = step.status || status;
          action = step.action || "";
          if (step.ok === false) throw new Error(step.error || "Dry run stopped");
        }
        status = await paint() || status;
        if (action === "idle" || action === "stopped") break;
        if (status === "done" || status === "failed" || status === "awaiting_review") break;
        if (!row.pump && status !== "created" && status !== "running" && status !== "partial") break;
        await new Promise((resolve) => setTimeout(resolve, 280));
      }
      return { ...row, status };
    },
    onSuccess: (row) => { setJobId(row.id); qc.invalidateQueries({ queryKey: ["board", boardId] }); toast.success(`Dry run ${row.id} · ${row.status}`); },
    onError: (e: Error) => toast.error(e.message),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); run.mutate(); return; }
      if (!typing && e.key === "/") { e.preventDefault(); searchRef.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const selected = nodes.find((n) => n.selected);
  const selectedSpec = selected ? CATALOG[selected.data.nodeType] : undefined;
  const selectedView = selected ? stageView(stages[selected.id], job?.job?.status) : null;
  const selectedPreview = selected ? previewByType[selected.data.nodeType] : null;
  const trackerOrigin = preview?.origin || board?.intel_origin || meta?.intel_origin;
  const fixture = trackerOrigin === "fixture";
  const cap = meta?.caps?.per_job;
  const needle = query.trim().toLowerCase();
  const lensIndex = tab === "intel" ? 0 : tab === "both" ? 1 : 2;
  const hasRunnableNodes = nodes.some((node) => !["note", "quick_link", "funnel_stage", "ad_reference", "kit_master"].includes(node.data.nodeType));

  const setFeed = (key: string, value: string) => {
    if (!selected) return;
    setNodes((ns) => ns.map((n) => {
      if (n.id !== selected.id) return n;
      const config = { ...n.data.config, [key]: value };
      if (key === "advertiser") config.page_id = "";
      return { ...n, data: { ...n.data, config } };
    }));
  };

  return (
    <BoardCtx.Provider value={{
      keys, toggle, follow, pickEvent, setConfig, mapFunnel, replicate, addAds, buildKitPath, expandKitMap,       sources, wired, persona,
      personaId: variables.persona || meta?.personas?.[0]?.id || "",
      personas: meta?.personas || [],
      accounts: accountsQuery.data?.accounts || [],
      funnels: (directoryQuery.data?.funnels || []).map((row: { id: string; name: string }) => ({ id: row.id, name: row.name })),
      dry: true,
      boardScript: variables.script || "",
    }}>
      <div className="v3 v3-board">
        <header className="v3-top">
          <div className="v3-crumb">Boards / <input aria-label="Board name" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <select aria-label="Load board" className="v3-select" value={boardId} onChange={(e) => { setLoaded(""); setBoardId(e.target.value); }}>
            {(list || [{ id: "talking_head", name: "Talking head" }]).map((b: any) => <option key={b.id} value={b.id}>{b.name}{b.preset ? " · preset" : ""}</option>)}
          </select>
          {trackerOrigin && <span className={`v3-badge ${fixture ? "ex" : "ok"}`}>{fixture ? "Fixture · not live" : "Live tracker"}</span>}
          <span className="v3-badge v3-mono">Dry run · $0.00{cap != null ? ` of $${Number(cap).toFixed(0)} cap` : ""}</span>
          <div className="v3-sp" />
          <select aria-label="Persona" className="v3-select" value={variables.persona || ""} onChange={(e) => setVariables((v) => ({ ...v, persona: e.target.value }))}>
            {(meta?.personas || []).map((p: any) => <option key={p.id} value={p.id}>{p.display}</option>)}
          </select>
          {board?.updated_at && <span className="v3-saved">{savedLabel(board.updated_at)}</span>}
          <button type="button" className="v3-btn" onClick={() => create.mutate(true)} disabled={create.isPending}><Plus size={13} />{create.isPending ? "Working…" : "New"}</button>
          <button type="button" className="v3-btn" onClick={() => create.mutate(false)} disabled={create.isPending}><Copy size={13} />{create.isPending ? "Copying…" : "Copy"}</button>
          <button type="button" className="v3-btn" onClick={() => save.mutate()} disabled={save.isPending}><Save size={13} />Save</button>
          <button type="button" className="v3-btn pri" onClick={() => run.mutate()} disabled={run.isPending || !hasRunnableNodes}
            title={hasRunnableNodes ? "Run executable nodes without provider spend" : "Add an executable node such as Brief, Script or Video to run"}><Play size={13} />Run (dry)</button>
        </header>
        {notice && <div className="v3-confirm" role="status">{notice}</div>}
        <div className="v3-funnel-bar">
          <label>Funnel <input aria-label="Funnel name" value={funnel.name} placeholder="Name this funnel" onChange={(e) => setFunnel((f) => ({ ...f, name: e.target.value }))} /></label>
          <label>Stage <select aria-label="Funnel stage" value={funnel.stage} onChange={(e) => setFunnel((f) => ({ ...f, stage: e.target.value }))}>
            {["mapping", "building", "review", "ready to publish"].map((stage) => <option key={stage} value={stage}>{stage}</option>)}
          </select></label>
          <label>Channel <input aria-label="Distribution channel" value={funnel.channel} placeholder="Meta, email, organic…" onChange={(e) => setFunnel((f) => ({ ...f, channel: e.target.value }))} /></label>
          <label>Owner <input aria-label="Funnel owner" value={funnel.owner} placeholder="Owner" onChange={(e) => setFunnel((f) => ({ ...f, owner: e.target.value }))} /></label>
          <span>{funnel.sourceAdIds.length} source ads</span>
        </div>
        <div className="v3-board-body">
          <div className="v3-canvas board-shell v3-shell"
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
            onDrop={(e) => {
              e.preventDefault();
              const kitId = e.dataTransfer.getData("application/board-kit");
              if (kitById(kitId)) {
                addKit(kitId, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
                return;
              }
              const type = e.dataTransfer.getData("application/board-node");
              if (!CATALOG[type]) return;
              const spec = CATALOG[type];
              const size = { width: spec.width, height: spec.side === "intel" ? 250 : 220 };
              const dropped = screenToFlowPosition({ x: e.clientX, y: e.clientY });
              const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
              setNodes((ns) => [...ns, {
                id, type: "board",
                position: placeNode(ns.map(nodeBox), dropped, size),
                data: { nodeType: type, enabled: true, config: {} },
              }]);
              setTab((current) => (current === "both" || current === spec.side ? current : "both"));
            }}>
            <aside className="v3-pal">
              <div className="v3-search-wrap">
                <input ref={searchRef} className="v3-search" placeholder="Add…" aria-label="Add node" value={query} onChange={(e) => setQuery(e.target.value)} />
                <span className="v3-kbd">/</span>
              </div>
              {(["Operating maps", "Funnel strategies", "Asset workflows"] as const).map((group) => {
                const kits = KITS.filter((kit) => kit.group === group && (!needle || `${kit.name} ${kit.description}`.toLowerCase().includes(needle)));
                if (!kits.length) return null;
                return <div key={group}>
                  <h6>{group}</h6>
                  <div className="v3-grid">
                    {kits.map((kit) => <button key={kit.id} type="button" draggable className="palette-tile palette-tile-create" data-palette={`${kit.id}-kit`}
                      title={`${kit.description} Source: ${kit.source}`}
                      onDragStart={(e) => { e.dataTransfer.setData("application/board-kit", kit.id); e.dataTransfer.effectAllowed = "move"; }}
                      onClick={async () => { setBusyId(`kit:${kit.id}`); try { addKit(kit.id); await new Promise((r) => setTimeout(r, 350)); } finally { setBusyId(""); } }}>
                      {busyId === `kit:${kit.id}` ? <Loader2 size={14} className="v3-spin" /> : null}
                      <span className="palette-label">{kit.name}</span></button>)}
                  </div>
                </div>;
              })}
              {PALETTE.map((group) => {
                const types = group.types.filter((type) => !needle || CATALOG[type].headline.toLowerCase().includes(needle));
                const soon = group.label === "Create · pipeline" && (!needle || "hook lab".includes(needle));
                if (!types.length && !soon) return null;
                return (
                  <div key={group.label}>
                    <h6>{group.label}</h6>
                    <div className="v3-grid">
                      {types.map((type) => {
                        const spec = CATALOG[type];
                        const Icon = spec.icon;
                        const intel = spec.side === "intel";
                        return (
                          <button key={type} type="button" draggable
                            onDragStart={(e) => { e.dataTransfer.setData("application/board-node", type); e.dataTransfer.effectAllowed = "move"; }}
                            onClick={() => addNode(type)}
                            data-palette={type}
                            className={`palette-tile ${intel ? "palette-tile-intel" : "palette-tile-create"}`}>
                            <span className="palette-icon"><Icon size={15} aria-hidden /></span>
                            <span className="palette-label">{spec.headline}</span>
                          </button>
                        );
                      })}
                      {soon && (
                        <button type="button" disabled className="palette-tile palette-tile-create soon" title="Not in this slice">
                          <span className="palette-icon"><Hash size={15} aria-hidden /></span>
                          <span className="palette-label">Hook Lab</span>
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
              {directoryQuery.data?.origin === "live" && <div>
                <h6>Competitor funnels</h6>
                <p className="v3-muted">{directoryQuery.data.coverage}</p>
                {(directoryQuery.data.funnels || []).filter((f: { name: string; advertisers?: string[] }) => !needle || `${f.name} ${(f.advertisers || []).join(" ")}`.toLowerCase().includes(needle)).map((f: { id: string; name: string; advertisers?: string[]; ads?: number | null }) => (
                  <button key={f.id} type="button" className="v3-dir-item"
                    onClick={() => mapDirectoryFunnel(f.id, f.name)} title="Map tracker route cards on this board" disabled={busyId === `funnel:${f.id}`}>
                    <b>{busyId === `funnel:${f.id}` ? "Loading funnel…" : f.name}</b><small>{(f.advertisers || []).join(", ") || "Advertiser not linked"}{f.ads != null ? ` · ${f.ads} assigned ads` : ""}</small>
                  </button>
                ))}
              </div>}
            </aside>
            <div className="v3-flow">
              <ReactFlow nodes={view.nodes} edges={view.edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                onConnect={onConnect} onConnectEnd={onConnectEnd} isValidConnection={isValid} edgesReconnectable={false}
                connectionRadius={40} colorMode="dark" fitView minZoom={0.15}
                connectionLineStyle={{ stroke: "#7c6cff", strokeWidth: 2 }}
                proOptions={{ hideAttribution: false }}>
                <Background gap={24} size={1.1} color="#2c2c33" />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable nodeColor={(n) => n.data?.nodeType === "ad_reference" ? "#5ab0ff" : "#7c6cff"} />
              </ReactFlow>
              <div className="v3-pill" role="tablist" aria-label="Board lens" data-lens={tab}>
                <i className="v3-pill-ind" style={{ left: 4 + lensIndex * 96 }} />
                {(["intel", "both", "create"] as Lens[]).map((side) => (
                  <button key={side} type="button" role="tab" aria-selected={tab === side} onClick={() => setTab(side)} className={tab === side ? "on" : ""}>
                    {side === "intel" && <i className="v3-dot" style={{ background: "#5ab0ff" }} />}
                    {side === "create" && <i className="v3-dot" style={{ background: "#7c6cff" }} />}
                    {side === "intel" ? "Intel" : side === "create" ? "Create" : "Both"}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <aside className="v3-inspector">
            {selected && selectedSpec ? (
              <>
                <div className="v3-ih">
                  <div className="v3-ih-r">
                    <span className={`v3-ic ${selectedSpec.side === "intel" ? "in" : "cr"}`}><selectedSpec.icon size={14} /></span>
                    <b>{selectedSpec.headline}</b>
                    <span className="v3-badge" style={{ marginLeft: "auto" }}>{selectedSpec.side === "intel" ? "Intel" : "Create"}</span>
                  </div>
                  <div className="v3-sub">
                    {selected.data.nodeType === "market_timeline" && "Chart node · reach and live ads. A marker emits that ad."}
                    {selected.data.nodeType === "ad_feed" && "Query the tracker. Filters stay on this node."}
                    {selected.data.nodeType === "kit_master" ? "Source, offer, outline and progress are saved in this kit." : selected.data.nodeType !== "market_timeline" && selected.data.nodeType !== "ad_feed" && "Keys stay in Settings. This board does not ask for them."}
                  </div>
                </div>
                <div className="v3-tabs">
                  {(["config", "output", "runs"] as const).map((id) => (
                    <button key={id} type="button" className={pane === id ? "on" : ""} onClick={() => setPane(id)}>{id[0].toUpperCase() + id.slice(1)}</button>
                  ))}
                </div>
                <div className="v3-ib">
                  {pane === "config" && selected.data.nodeType === "ad_feed" && (
                    <div className="v3-flds">
                      {(["advertiser", "status", "date_from", "date_to", "longevity_min", "limit", "offset", "ad_id"] as const).map((key) => (
                        <label key={key} className="v3-fld">{key.replace(/_/g, " ")}
                          {key === "status" ? <select className="v3-inp" value={selected.data.config.status || ""} aria-label="status" onChange={(e) => setFeed("status", e.target.value)}>
                            <option value="">All statuses</option>{["live", "stopped", "missing", "removed"].map((s) => <option key={s} value={s}>{s}</option>)}
                          </select> : <input className="v3-inp" value={selected.data.config[key] || ""} aria-label={key} onChange={(e) => setFeed(key, e.target.value)} />}
                        </label>
                      ))}
                    </div>
                  )}
                  {pane === "config" && selected.data.nodeType === "market_timeline" && (
                    <TimelineConfig config={selected.data.config} preview={previewByType.market_timeline} />
                  )}
                  {pane === "config" && selected.data.nodeType === "kit_master" && (
                    <p className="v3-copy">Use the kit card to attach a source, review the outline and open an editable production path when needed. Save the board to keep these choices.</p>
                  )}
                  {pane === "config" && selected.data.nodeType !== "kit_master" && selected.data.nodeType !== "ad_feed" && selected.data.nodeType !== "market_timeline" && (
                    <NodeInputs node={selected} nodes={nodes} edges={edges} />
                  )}
                  {pane === "output" && (
                    <>
                      <div className="v3-ev">
                        <div className="v3-ev-l">Evidence</div>
                        <p className="v3-copy">{selectedPreview?.evidence?.label || selectedPreview?.evidence?.source || "No read yet."}</p>
                      </div>
                      <div className="v3-inf">
                        <div className="v3-inf-l">Inference</div>
                        <p className="v3-copy">{selectedPreview?.inference?.note || "Nothing inferred yet."}</p>
                      </div>
                    </>
                  )}
                  {pane === "runs" && (
                    <div className="v3-kv"><span>Status</span><span>{selectedView?.status || "idle"}</span></div>
                  )}
                </div>
              </>
            ) : (
              <div className="v3-ib"><p className="v3-copy">Select a node.</p></div>
            )}
          </aside>
        </div>
      </div>
    </BoardCtx.Provider>
  );
}

function NodeInputs({ node, nodes, edges }: { node: FlowNode; nodes: FlowNode[]; edges: Edge[] }) {
  const spec = CATALOG[node.data.nodeType];
  const named: Record<string, [string, string][]> = {
    brief: [["persona", "Persona"], ["offer", "Offer"], ["angle", "Angle"], ["length", "Length"], ["platform", "Platform"]],
    script: [["script", "Script"], ["framework", "Framework"]],
    voice: [["voice", "Voice"]],
    video: [["format", "Format"], ["aspect", "Aspect"]],
    still: [["prompt", "Image prompt"], ["aspect_ratio", "Aspect"]],
    landing: [["offer", "Offer"], ["angle", "Angle"]],
    funnel_route: [["funnel_name", "Funnel"], ["ad_id", "Ad"]],
    funnel_step: [["label", "Label"], ["kind", "Kind"], ["url", "URL"], ["state", "State"]],
    ad_breakdown: [["ad_id", "Ad"]],
    teardown: [["ad_id", "Ad"]],
  };
  const fields = named[node.data.nodeType] || [];
  const incoming = edges.filter((edge) => edge.target === node.id);
  return (
    <div className="v3-flds">
      {spec.inputs.map((port) => {
        const edge = incoming.find((item) => item.targetHandle === port.id);
        const source = nodes.find((item) => item.id === edge?.source);
        return <div className="v3-kv" key={port.id}><span>{port.id}</span><span>{source ? CATALOG[source.data.nodeType]?.headline || source.id : "Not connected"}</span></div>;
      })}
      {fields.map(([key, label]) => (
        <label key={key} className="v3-fld">{label}
          <div className="v3-inp">{node.data.config[key] || "—"}</div>
        </label>
      ))}
      {!spec.inputs.length && !fields.length && <p className="v3-copy">This block has no inputs yet.</p>}
    </div>
  );
}

function TimelineConfig({ config, preview }: { config: Record<string, string>; preview?: Artefact | null }) {
  const fields = (preview?.evidence?.fields || {}) as {
    events?: { id: string; label?: string; date?: string; kind?: string; ad_id?: string }[];
    emitted_event?: { id?: string; label?: string; date?: string; kind?: string; ad_id?: string };
  };
  const event = (fields.events || []).find((ev) => ev.id === config.event_id) || fields.emitted_event;
  const ad = preview?.ad;
  const adId = event?.ad_id || config.ad_id;
  const matched = ad?.id && ad.id === adId ? ad : undefined;
  return (
    <div className="v3-fld">
      <label>Selected marker → output</label>
      {event ? (
        <div className="v3-inp v3-stack">
          <span>{event.label || event.id}</span>
          <span className="v3-muted">{event.date}{event.kind ? ` · ${event.kind}` : ""} · {matched?.headline || adId}</span>
        </div>
      ) : <p className="v3-copy">Click a marker on the chart.</p>}
      <p className="v3-copy">A marker selects that event&apos;s ad and passes it to Ad Feed, then to Breakdown, Funnel and Teardown.</p>
    </div>
  );
}

export default function BoardPage() {
  return <ReactFlowProvider><Canvas /></ReactFlowProvider>;
}
