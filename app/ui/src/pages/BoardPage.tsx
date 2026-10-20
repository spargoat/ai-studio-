import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, ViewportPortal, applyNodeChanges, applyEdgeChanges,
  useReactFlow, type Connection, type Edge, type Node, type NodeChange, type EdgeChange, type OnConnect, type FinalConnectionState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hash, Play, Copy, Save } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { CATALOG, PALETTE, PORT_COLOR, portType, type Side } from "../board/catalog";
import { applyConnection, placeNode, type Box } from "../board/graph";
import { BoardCtx, nodeTypes, type Artefact, type BoardData } from "../board/nodes";

type Lens = Side | "both";
type FlowNode = Node<BoardData, "board">;
type GraphNode = { id: string; type: string; position: { x: number; y: number }; enabled?: boolean; config?: Record<string, string> };
type GraphEdge = { id: string; source: string; target: string; sourceHandle?: string; targetHandle?: string };

function toFlow(nodes: GraphNode[]): FlowNode[] {
  let selectTimeline = true;
  const flow: FlowNode[] = (nodes || []).filter((n) => CATALOG[n.type]).map((n) => {
    const selected = selectTimeline && n.type === "market_timeline";
    if (selected) selectTimeline = false;
    return {
      id: n.id, type: "board" as const, position: n.position || { x: 0, y: 0 }, selected,
      data: { nodeType: n.type, enabled: n.enabled !== false, config: n.config || {} },
    };
  });
  return flow;
}

function serialize(nodes: FlowNode[], edges: Edge[], variables: Record<string, string>) {
  return {
    variables,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.nodeType, position: n.position, enabled: n.data.enabled, config: n.data.config || {} })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle })),
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

function nodeBox(n: FlowNode): Box {
  const spec = CATALOG[n.data.nodeType];
  return {
    x: n.position.x, y: n.position.y,
    width: n.measured?.width || spec.width,
    height: n.measured?.height || (n.data.nodeType === "market_timeline" ? 360 : spec.side === "intel" ? 250 : 180),
  };
}

function ZoneBoxes({ nodes, lens, fixture }: { nodes: FlowNode[]; lens: Lens; fixture: boolean }) {
  const sides: Side[] = lens === "both" ? ["intel", "create"] : [lens];
  return (
    <ViewportPortal>
      {sides.map((side) => {
        const list = nodes.filter((n) => CATALOG[n.data.nodeType]?.side === side);
        if (!list.length) return null;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const n of list) {
          const box = nodeBox(n);
          minX = Math.min(minX, box.x); minY = Math.min(minY, box.y);
          maxX = Math.max(maxX, box.x + box.width); maxY = Math.max(maxY, box.y + box.height);
        }
        const pad = 36;
        return (
          <div key={side} className="v3-zone" style={{ left: minX - pad, top: minY - 28, width: maxX - minX + pad * 2, height: maxY - minY + pad + 28 }}>
            <div className="v3-zl">
              <i style={{ background: side === "intel" ? "#5ab0ff" : "#7c6cff" }} />
              {side === "intel" ? (fixture ? "INTEL · FIXTURE" : "INTEL · LIVE ANALYTICS") : "CREATE · PRODUCTION PIPELINE"}
              <span>{side === "intel" ? (fixture ? "recorded sample · not a live read" : "read-only · ads-tracker") : "dry run"}</span>
            </div>
          </div>
        );
      })}
    </ViewportPortal>
  );
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
  const [tab, setTab] = useState<Lens>("both");
  const [estimates, setEstimates] = useState<Record<string, number>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState("");
  const [query, setQuery] = useState("");
  const [pane, setPane] = useState<"config" | "output" | "runs">("config");
  const searchRef = useRef<HTMLInputElement>(null);

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
      return { ...n, data: { ...n.data, preview: previewByType[n.data.nodeType] || null, status: sv.status, est: estimates[n.id] ?? 0, actual: sv.actual, ran: sv.ran } };
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
    const t = setTimeout(() => fitView({ padding: 0.18, maxZoom: 1, duration: 200 }), 80);
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
  const isValid = useCallback((c: Connection | Edge) => applyConnection(edges, c, wireCtx(nodes)).ok, [edges, nodes, wireCtx]);

  const occupied = (list: FlowNode[], side: Side) => (tab === "both" ? list : list.filter((n) => CATALOG[n.data.nodeType]?.side === side));
  const addNode = (type: string) => {
    const spec = CATALOG[type];
    if (!spec) return;
    if (tab !== "both" && spec.side !== tab) setTab(spec.side);
    const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
    const size = { width: spec.width, height: spec.side === "intel" ? 250 : 180 };
    setNodes((ns) => [...ns, {
      id, type: "board",
      position: placeNode(occupied(ns, spec.side).map(nodeBox), { x: 48, y: 48 }, size),
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
  const fixture = !preview || preview.origin !== "live";
  const cap = meta?.caps?.per_job;
  const needle = query.trim().toLowerCase();
  const lensIndex = tab === "intel" ? 0 : tab === "both" ? 1 : 2;

  const setFeed = (key: string, value: string) => {
    if (!selected) return;
    setNodes((ns) => ns.map((n) => n.id === selected.id ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n));
  };

  return (
    <BoardCtx.Provider value={{ keys, toggle, follow, pickEvent, wired, persona }}>
      <div className="v3 v3-board">
        <header className="v3-top">
          <div className="v3-crumb">Boards / <input aria-label="Board name" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <select aria-label="Load board" className="v3-select" value={boardId} onChange={(e) => { setLoaded(""); setBoardId(e.target.value); }}>
            {(list || [{ id: "talking_head", name: "Talking head" }]).map((b: any) => <option key={b.id} value={b.id}>{b.name}{b.preset ? " · preset" : ""}</option>)}
          </select>
          <span className={`v3-badge ${fixture ? "ex" : "ok"}`}>{fixture ? "Fixture · not live" : "Live tracker"}</span>
          <span className="v3-badge v3-mono">Dry run · $0.00{cap != null ? ` of $${Number(cap).toFixed(0)} cap` : ""}</span>
          <div className="v3-sp" />
          <select aria-label="Persona" className="v3-select" value={variables.persona || ""} onChange={(e) => setVariables((v) => ({ ...v, persona: e.target.value }))}>
            {(meta?.personas || []).map((p: any) => <option key={p.id} value={p.id}>{p.display}</option>)}
          </select>
          {board?.updated_at && <span className="v3-saved">{savedLabel(board.updated_at)}</span>}
          <button type="button" className="v3-btn" onClick={() => create.mutate()} disabled={create.isPending}><Copy size={13} />Copy</button>
          <button type="button" className="v3-btn" onClick={() => save.mutate()} disabled={save.isPending}><Save size={13} />Save</button>
          <button type="button" className="v3-btn pri" onClick={() => run.mutate()} disabled={run.isPending}><Play size={13} />Run (dry)</button>
        </header>
        <div className="v3-board-body">
          <div className="v3-canvas board-shell v3-shell"
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
            onDrop={(e) => {
              e.preventDefault();
              const type = e.dataTransfer.getData("application/board-node");
              if (!CATALOG[type]) return;
              const spec = CATALOG[type];
              const size = { width: spec.width, height: spec.side === "intel" ? 250 : 180 };
              const dropped = screenToFlowPosition({ x: e.clientX, y: e.clientY });
              const id = `${type}-${Math.random().toString(36).slice(2, 6)}`;
              setNodes((ns) => [...ns, {
                id, type: "board",
                position: placeNode(occupied(ns, spec.side).map(nodeBox), dropped, size),
                data: { nodeType: type, enabled: true, config: {} },
              }]);
              setTab((current) => (current === "both" ? current : spec.side));
            }}>
            <aside className="v3-pal">
              <div className="v3-search-wrap">
                <input ref={searchRef} className="v3-search" placeholder="Add…" aria-label="Add node" value={query} onChange={(e) => setQuery(e.target.value)} />
                <span className="v3-kbd">/</span>
              </div>
              {PALETTE.map((group) => {
                const types = group.types.filter((type) => !needle || CATALOG[type].headline.toLowerCase().includes(needle));
                const soon = group.side === "create" && (!needle || "hook lab".includes(needle));
                if (!types.length && !soon) return null;
                return (
                  <div key={group.side}>
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
            </aside>
            <div className="v3-flow">
              <ReactFlow nodes={view.nodes} edges={view.edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                onConnect={onConnect} onConnectEnd={onConnectEnd} isValidConnection={isValid} edgesReconnectable={false}
                connectionRadius={40} colorMode="dark" fitView minZoom={0.3}
                connectionLineStyle={{ stroke: "#7c6cff", strokeWidth: 2 }}
                proOptions={{ hideAttribution: false }}>
                <Background gap={24} size={1.1} color="#2c2c33" />
                <Controls showInteractive={false} />
                <ZoneBoxes nodes={nodes} lens={tab} fixture={fixture} />
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
                    {selected.data.nodeType !== "market_timeline" && selected.data.nodeType !== "ad_feed" && "Keys stay in Settings. This board does not ask for them."}
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
                      {(["advertiser", "status", "date_from", "date_to", "longevity_min", "ad_id"] as const).map((key) => (
                        <label key={key} className="v3-fld">{key.replace(/_/g, " ")}
                          <input className="v3-inp" value={selected.data.config[key] || ""} aria-label={key} onChange={(e) => setFeed(key, e.target.value)} />
                        </label>
                      ))}
                    </div>
                  )}
                  {pane === "config" && selected.data.nodeType === "market_timeline" && (
                    <TimelineConfig config={selected.data.config} preview={previewByType.market_timeline} />
                  )}
                  {pane === "config" && selected.data.nodeType !== "ad_feed" && selected.data.nodeType !== "market_timeline" && (
                    <p className="v3-copy">This node takes its stage settings from the template. Connect a matching port to feed it.</p>
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
