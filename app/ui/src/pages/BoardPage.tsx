import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, applyNodeChanges, applyEdgeChanges,
  useReactFlow, type Connection, type Edge, type Node, type NodeChange, type EdgeChange, type OnConnect, type FinalConnectionState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Play, Plus, Save } from "lucide-react";
import { api } from "../lib/api";
import { Button } from "../components/ui";
import { toast } from "../components/Toast";
import { CATALOG, PALETTE, PORT_COLOR, portType, type Side } from "../board/catalog";
import { applyConnection, placeNode, type Box } from "../board/graph";
import { BoardCtx, nodeTypes, type Artefact, type BoardData } from "../board/nodes";

type FlowNode = Node<BoardData, "board">;

type GraphNode = { id: string; type: string; position: { x: number; y: number }; enabled?: boolean; config?: Record<string, string> };
type GraphEdge = { id: string; source: string; target: string; sourceHandle?: string; targetHandle?: string };

function toFlow(nodes: GraphNode[]): FlowNode[] {
  return (nodes || []).filter((n) => CATALOG[n.type]).map((n) => ({
    id: n.id, type: "board", position: n.position || { x: 0, y: 0 },
    data: { nodeType: n.type, enabled: n.enabled !== false, config: n.config || {} },
  }));
}

function serialize(nodes: FlowNode[], edges: Edge[], variables: Record<string, string>) {
  return {
    variables,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.nodeType, position: n.position, enabled: n.data.enabled, config: n.data.config || {} })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle })),
  };
}

function EventPanel({ config, preview }: { config: Record<string, string>; preview?: Artefact | null }) {
  const fields = (preview?.evidence?.fields || {}) as {
    events?: { id: string; label?: string; date?: string; kind?: string; ad_id?: string }[];
    emitted_event?: { id?: string; label?: string; date?: string; kind?: string; ad_id?: string };
  };
  const event = (fields.events || []).find((ev) => ev.id === config.event_id) || fields.emitted_event;
  const ad = preview?.ad;
  const adId = event?.ad_id || config.ad_id;
  const matched = ad?.id && ad.id === adId ? ad : undefined;
  return (
    <div className="space-y-2">
      <p className="text-zinc-400">A marker selects that event&apos;s ad and passes it to Ad Feed, then to Breakdown, Funnel and Teardown.</p>
      {event ? (
        <dl className="space-y-1.5">
          <div><dt className="text-[10px] uppercase tracking-wide text-zinc-500">Event</dt><dd className="text-zinc-100">{event.label || event.id}</dd></div>
          {event.date && <div><dt className="text-[10px] uppercase tracking-wide text-zinc-500">Date</dt><dd className="text-zinc-100">{event.date}</dd></div>}
          {event.kind && <div><dt className="text-[10px] uppercase tracking-wide text-zinc-500">Kind</dt><dd className="text-zinc-100">{event.kind}</dd></div>}
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-zinc-500">Ad</dt>
            <dd className="text-zinc-100">{matched?.headline || "Selected ad"} <span className="text-zinc-500">{adId}</span></dd>
          </div>
        </dl>
      ) : <p className="text-zinc-400">Click a marker on the chart.</p>}
    </div>
  );
}

function stageView(stage: { runs?: { status: string; review_status?: string; actual_cost_usd?: number; est_cost_usd?: number }[] } | undefined, jobStatus?: string) {
  const run = stage?.runs?.length ? stage.runs[stage.runs.length - 1] : undefined;
  if (!run) {
    if (!jobStatus) return { status: "idle", actual: null as number | null, ran: false };
    return { status: jobStatus === "done" || jobStatus === "failed" ? "blocked" : "waiting", actual: null, ran: false };
  }
  let status = run.status;
  if (run.status === "succeeded" && run.review_status === "pending") status = "needs review";
  else if (run.status === "succeeded") status = "done";
  else if (run.status === "failed" || run.status === "blocked_budget") status = "failed";
  else if (run.status === "running") status = "running";
  return { status, actual: run.actual_cost_usd ?? 0, ran: true };
}

function Canvas() {
  const qc = useQueryClient();
  const { screenToFlowPosition, fitView } = useReactFlow();
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta") });
  const { data: list } = useQuery({ queryKey: ["boards"], queryFn: () => api<any[]>("/api/boards") });
  const [boardId, setBoardId] = useState("talking_head");
  const { data: board } = useQuery({ queryKey: ["board", boardId], queryFn: () => api<any>(`/api/boards/${boardId}`), enabled: !!boardId });
  const [nodes, setNodes] = useState<FlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [name, setName] = useState("Talking head");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Side>("intel");
  const [estimates, setEstimates] = useState<Record<string, number>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState("");

  useEffect(() => {
    if (!board) return;
    const stamp = boardId + (board.updated_at || "");
    if (loaded === stamp) return;
    setLoaded(stamp);
    setNodes(toFlow(board.graph?.nodes || []));
    setEdges((board.graph?.edges || []).map((e: GraphEdge) => ({ ...e })));
    setName(board.name || "Board");
    setVariables(board.graph?.variables || {});
    setEstimates(board.estimates || {});
    if (board.last_job_id) setJobId(board.last_job_id);
  }, [board, boardId, loaded]);

  const feed = nodes.find((n) => n.data.nodeType === "ad_feed");
  const timeline = nodes.find((n) => n.data.nodeType === "market_timeline");
  const previewQs = new URLSearchParams({
    advertiser: feed?.data.config.advertiser || "",
    status: feed?.data.config.status || "",
    date_from: feed?.data.config.date_from || "",
    date_to: feed?.data.config.date_to || "",
    longevity_min: feed?.data.config.longevity_min || "",
    ad_id: feed?.data.config.ad_id || "",
    event_id: timeline?.data.config.event_id || "",
  }).toString();
  const previewQuery = useQuery({
    queryKey: ["intel-preview", previewQs],
    queryFn: () => api<any>(`/api/intel/preview?${previewQs}`),
    placeholderData: keepPreviousData,
    enabled: nodes.length > 0,
  });
  // The board payload already carries the fixture, so a refetch cannot blank the nodes.
  const preview = previewQuery.data || board?.preview || null;
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
      if (n.data.nodeType === "ad_feed") {
        next.data = { ...n.data, config: { ...n.data.config, ad_id: adId } };
      }
      return next;
    }));
  }, []);

  const previewByType: Record<string, any> = {
    ad_feed: preview?.feed, ad_breakdown: preview?.breakdown, funnel_route: preview?.funnel,
    teardown: preview?.teardown, market_timeline: preview?.timeline,
  };
  const stages: Record<string, any> = {};
  for (const stage of job?.stages || []) stages[stage.id] = stage;

  const view = useMemo(() => {
    const visible = nodes.filter((n) => CATALOG[n.data.nodeType]?.side === tab);
    const ids = new Set(visible.map((n) => n.id));
    const stubs: Node[] = [];
    const viewEdges: Edge[] = [];
    const paint = (n: FlowNode): FlowNode => {
      const sv = stageView(stages[n.id], job?.job?.status);
      return { ...n, data: { ...n.data, preview: previewByType[n.data.nodeType] || null, status: sv.status, est: estimates[n.id] ?? 0, actual: sv.actual, ran: sv.ran } };
    };
    for (const edge of edges) {
      const srcIn = ids.has(edge.source);
      const dstIn = ids.has(edge.target);
      const color = PORT_COLOR[portType(nodes.find((n) => n.id === edge.source)?.data.nodeType || "", "out", edge.sourceHandle) || ""] || "#71717a";
      const styled = { ...edge, style: { stroke: color }, animated: false };
      if (srcIn && dstIn) viewEdges.push(styled);
      else if (srcIn || dstIn) {
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
    const t = setTimeout(() => fitView({ padding: 0.12, maxZoom: 1, duration: 200 }), 80);
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
      toast.error(toId ? "That node is on the other tab. Open it and connect the port there." : "Drop the wire on a coloured port.");
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
  const isValid = useCallback((c: Connection | Edge) => {
    return applyConnection(edges, c, wireCtx(nodes)).ok;
  }, [edges, nodes, wireCtx]);

  const boxesFor = (list: FlowNode[], side: Side): Box[] => list.filter((n) => CATALOG[n.data.nodeType]?.side === side).map((n) => ({
    x: n.position.x, y: n.position.y,
    width: n.measured?.width || CATALOG[n.data.nodeType].width,
    height: n.measured?.height || (CATALOG[n.data.nodeType].side === "intel" ? 280 : 150),
  }));
  const addNode = (type: string) => {
    const spec = CATALOG[type];
    if (!spec) return;
    if (spec.side !== tab) setTab(spec.side);
    const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
    const size = { width: spec.width, height: spec.side === "intel" ? 280 : 150 };
    setNodes((ns) => [...ns, {
      id, type: "board",
      position: placeNode(boxesFor(ns, spec.side), { x: 48, y: 48 }, size),
      data: { nodeType: type, enabled: true, config: {} },
    }]);
  };

  const save = useMutation({
    mutationFn: async () => api(`/api/boards/${boardId}`, { method: "PUT", body: { name, graph: serialize(nodes, edges, variables) } }),
    onSuccess: (row: any) => { setEstimates(row.estimates || {}); setLoaded(boardId + (row.updated_at || "")); qc.invalidateQueries({ queryKey: ["boards"] }); toast.success("Board saved"); },
    onError: (e: Error) => toast.error(e.message),
  });
  const create = useMutation({
    mutationFn: async () => api("/api/boards", { method: "POST", body: { name: name || "Untitled board", graph: serialize(nodes, edges, variables) } }),
    onSuccess: (row: any) => { setBoardId(row.id); qc.invalidateQueries({ queryKey: ["boards"] }); toast.success("Board copied"); },
    onError: (e: Error) => toast.error(e.message),
  });
  const run = useMutation({
    mutationFn: async () => {
      await api(`/api/boards/${boardId}`, { method: "PUT", body: { name, graph: serialize(nodes, edges, variables) } });
      const row = await api<{ id: string; mode: string; pump?: boolean }>(`/api/boards/${boardId}/run`, { body: { mode: "dry" } });
      setJobId(row.id);
      let status = "started";
      if (row.pump) {
        for (let i = 0; i < 24; i++) {
          const step = await api<{ status?: string; action?: string; ok?: boolean; error?: string }>(`/api/jobs/${row.id}/advance`, { method: "POST", body: {} });
          status = step.status || status;
          qc.invalidateQueries({ queryKey: ["job", row.id] });
          if (step.ok === false) throw new Error(step.error || "Dry run stopped");
          if (step.action === "idle" || step.action === "stopped") break;
          if (status === "awaiting_review" || status === "done" || status === "failed") break;
          if (status !== "created" && status !== "running" && status !== "partial") break;
        }
      }
      return { ...row, status };
    },
    onSuccess: (row) => { setJobId(row.id); qc.invalidateQueries({ queryKey: ["board", boardId] }); toast.success(`Dry run ${row.id} · ${row.status}`); },
    onError: (e: Error) => toast.error(e.message),
  });

  const selected = nodes.find((n) => n.selected);
  const fixture = !preview || preview.origin !== "live";

  return (
    <BoardCtx.Provider value={{ keys, toggle, follow, pickEvent }}>
      <div className="h-full flex flex-col bg-zinc-950 text-zinc-100">
        <header className="shrink-0 flex flex-wrap items-center gap-2 px-3 pl-12 md:pl-3 py-2 border-b border-zinc-800">
          <input aria-label="Board name" value={name} onChange={(e) => setName(e.target.value)}
            className="bg-transparent text-sm font-semibold text-white w-36 border-b border-transparent focus:border-zinc-600 outline-none" />
          <select aria-label="Load board" value={boardId} onChange={(e) => { setLoaded(""); setBoardId(e.target.value); }}
            className="bg-zinc-900 border border-zinc-700 rounded-md text-xs px-2 py-1">
            {(list || [{ id: "talking_head", name: "Talking head" }]).map((b: any) => <option key={b.id} value={b.id}>{b.name}{b.preset ? " · preset" : ""}</option>)}
          </select>
          <select aria-label="Persona" value={variables.persona || ""} onChange={(e) => setVariables((v) => ({ ...v, persona: e.target.value }))}
            className="bg-zinc-900 border border-zinc-700 rounded-md text-xs px-2 py-1">
            {(meta?.personas || []).map((p: any) => <option key={p.id} value={p.id}>{p.display}</option>)}
          </select>
          <span className={`text-[10px] font-semibold rounded-full px-2 py-0.5 ${fixture ? "text-amber-200 bg-amber-500/10 border border-amber-500/30" : "text-emerald-200 bg-emerald-500/10 border border-emerald-500/30"}`}>
            {fixture ? "Fixture · not live" : "Live tracker"}
          </span>
          <div className="ml-auto flex gap-1.5">
            <Button size="xs" variant="ghost" icon={<Plus size={12} />} onClick={() => create.mutate()} loading={create.isPending}>New</Button>
            <Button size="xs" variant="secondary" icon={<Save size={12} />} onClick={() => save.mutate()} loading={save.isPending}>Save</Button>
            <Button size="xs" icon={<Play size={12} />} onClick={() => run.mutate()} loading={run.isPending}>Dry run</Button>
          </div>
        </header>
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <aside className="shrink-0 md:w-52 border-b md:border-b-0 md:border-r border-zinc-800 bg-zinc-950">
            <div className="flex md:flex-col gap-2 p-2 overflow-x-auto md:overflow-y-auto md:h-full">
              {PALETTE.map((group) => (
                <div key={group.side} className="flex md:flex-col gap-2 shrink-0">
                  <div className={`hidden md:block text-[9px] font-bold tracking-widest px-1 ${group.side === "intel" ? "text-teal-400" : "text-violet-400"}`}>{group.label}</div>
                  {group.types.map((type) => {
                    const spec = CATALOG[type];
                    const Icon = spec.icon;
                    const intel = spec.side === "intel";
                    return (
                      <button key={type} type="button" draggable
                        onDragStart={(e) => { e.dataTransfer.setData("application/board-node", type); e.dataTransfer.effectAllowed = "move"; }}
                        onClick={() => addNode(type)}
                        data-palette={type}
                        className={`palette-tile shrink-0 w-40 md:w-full ${intel ? "palette-tile-intel" : "palette-tile-create"}`}>
                        <span className="palette-icon">
                          <Icon size={16} aria-hidden />
                        </span>
                        <span className="palette-label">{spec.headline}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </aside>
          <div className="flex-1 min-h-0 min-w-0 relative board-shell"
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
            onDrop={(e) => {
              e.preventDefault();
              const type = e.dataTransfer.getData("application/board-node");
              if (!CATALOG[type]) return;
              const spec = CATALOG[type];
              const size = { width: spec.width, height: spec.side === "intel" ? 280 : 150 };
              const dropped = screenToFlowPosition({ x: e.clientX, y: e.clientY });
              const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
              setNodes((ns) => [...ns, {
                id, type: "board",
                position: placeNode(boxesFor(ns, spec.side), dropped, size),
                data: { nodeType: type, enabled: true, config: {} },
              }]);
              setTab(spec.side);
            }}>
            <ReactFlow nodes={view.nodes} edges={view.edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={onConnect} onConnectEnd={onConnectEnd} isValidConnection={isValid} edgesReconnectable={false}
              connectionRadius={40} colorMode="dark" fitView minZoom={0.35} proOptions={{ hideAttribution: false }}>
              <Background gap={18} color="#27272a" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable className="!bg-zinc-900" maskColor="rgba(0,0,0,0.55)" />
            </ReactFlow>
            <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center">
              <div className="pointer-events-auto flex rounded-full border border-zinc-700 bg-zinc-900/95 p-1 shadow-xl" role="tablist" aria-label="Board lens">
                {(["intel", "create"] as Side[]).map((side) => (
                  <button key={side} type="button" role="tab" aria-selected={tab === side} onClick={() => setTab(side)}
                    className={`px-4 py-1.5 rounded-full text-xs font-semibold capitalize ${tab === side ? (side === "intel" ? "bg-teal-600 text-white" : "bg-violet-600 text-white") : "text-zinc-400"}`}>
                    {side === "intel" ? "Intel" : "Create"}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {selected && (
            <aside className="shrink-0 w-full md:w-64 border-t md:border-t-0 md:border-l border-zinc-800 p-3 overflow-y-auto max-h-48 md:max-h-none text-xs space-y-2">
              <h2 className="text-sm font-semibold text-white">{CATALOG[selected.data.nodeType]?.headline}</h2>
              {selected.data.nodeType === "ad_feed" && (
                <div className="space-y-1.5">
                  {(["advertiser", "status", "date_from", "date_to", "longevity_min", "ad_id"] as const).map((key) => (
                    <label key={key} className="block text-[10px] text-zinc-500">{key.replace(/_/g, " ")}
                      <input value={selected.data.config[key] || ""} aria-label={key}
                        onChange={(e) => setNodes((ns) => ns.map((n) => n.id === selected.id ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: e.target.value } } } : n))}
                        className="mt-0.5 w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100" />
                    </label>
                  ))}
                </div>
              )}
              {selected.data.nodeType === "market_timeline" && <EventPanel config={selected.data.config} preview={previewByType.market_timeline} />}
              {selected.data.nodeType !== "ad_feed" && selected.data.nodeType !== "market_timeline" && (
                <p className="text-zinc-400">Connections and keys live in Settings. This board does not ask for them.</p>
              )}
            </aside>
          )}
        </div>
      </div>
    </BoardCtx.Provider>
  );
}

export default function BoardPage() {
  return <ReactFlowProvider><Canvas /></ReactFlowProvider>;
}
