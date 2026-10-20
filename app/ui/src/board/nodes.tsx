import { createContext, useContext } from "react";
import { Link } from "react-router-dom";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Badge } from "../components/ui";
import { CATALOG, LONGEVITY_NOTE, PORT_COLOR, REFERENCE_LABEL, type NodeSpec } from "./catalog";

export type Artefact = {
  type?: string;
  label?: string;
  pattern_summary?: string;
  interpretation?: string;
  decode?: string;
  emitted_ad_id?: string;
  ad?: { id?: string; headline?: string; advertiser?: string; status?: string };
  page?: { url?: string; title?: string };
  evidence?: {
    source?: string;
    data_as_of?: string;
    origin?: string;
    live?: boolean;
    label?: string;
    fields?: Record<string, unknown>;
  };
  inference?: { note?: string; longevity_note?: string };
};

export type BoardData = {
  nodeType: string;
  enabled: boolean;
  config: Record<string, string>;
  preview?: Artefact | null;
  status?: string;
  est?: number | null;
  actual?: number | null;
  ran?: boolean;
};

export type StubData = { nodeType: string; followId: string; side: "source" | "target" };

export const BoardCtx = createContext<{
  keys: Record<string, boolean>;
  toggle: (id: string) => void;
  follow: (id: string) => void;
  pickEvent: (id: string, event: { id: string; ad_id?: string }) => void;
}>({ keys: {}, toggle: () => {}, follow: () => {}, pickEvent: () => {} });

const STATUS_VARIANT: Record<string, "default" | "info" | "warning" | "success" | "danger"> = {
  idle: "default", waiting: "info", running: "warning", "needs review": "warning",
  done: "success", failed: "danger", blocked: "danger",
};

function money(n: number) {
  return `$${n.toFixed(2)}`;
}

function Origin({ evidence }: { evidence?: Artefact["evidence"] }) {
  if (!evidence) return <p className="text-[10px] text-zinc-500">No read yet.</p>;
  const fixture = evidence.origin !== "live";
  return (
    <div>
      <p className={`text-[10px] font-semibold ${fixture ? "text-amber-300" : "text-emerald-300"}`}>
        {fixture ? "Fixture · not a live read" : "Live tracker"}
        {evidence.data_as_of ? ` · data as of ${evidence.data_as_of}` : ""}
      </p>
      <p className="text-[10px] text-zinc-500">{evidence.source}</p>
    </div>
  );
}

function Split({ artefact, nodeType, nodeId, selectedEvent }: { artefact?: Artefact | null; nodeType: string; nodeId: string; selectedEvent?: string }) {
  const evidence = artefact?.evidence;
  const fields = evidence?.fields || {};
  const picked = (fields.picked as { headline?: string; advertiser?: string } | undefined) || artefact?.ad;
  const steps = (fields.steps as { label?: string }[] | undefined) || [];
  return (
    <div className="px-2.5 py-2 space-y-2">
      <div>
        <div className="text-[9px] font-bold tracking-wider text-teal-300/90">Evidence</div>
        <Origin evidence={evidence} />
        {picked?.headline && <p className="text-[11px] text-zinc-200 mt-1">{picked.advertiser ? `${picked.advertiser}: ` : ""}{picked.headline}</p>}
        {nodeType === "funnel_route" && steps.length > 0 && (
          <p className="text-[10px] text-zinc-400 mt-1">{steps.map((s) => s.label).filter(Boolean).join(" → ")}</p>
        )}
        {nodeType === "market_timeline" && <TimelineChart nodeId={nodeId} artefact={artefact} selectedId={selectedEvent} />}
        {nodeType === "market_timeline" && !!fields.emitted_event && (
          <p className="text-[10px] text-zinc-200">
            <span className="text-zinc-500">Event. </span>
            {String((fields.emitted_event as { label?: string }).label || "marker")}
          </p>
        )}
      </div>
      <div className="border-t border-dashed border-zinc-700 pt-1.5">
        <div className="text-[9px] font-bold tracking-wider text-amber-300/90">Inference</div>
        <p className="text-[10px] text-zinc-400 leading-snug">{artefact?.inference?.note || "Nothing inferred yet."}</p>
        {nodeType === "ad_breakdown" && (
          <p className="text-[10px] text-amber-200/90 mt-1">{artefact?.inference?.longevity_note || LONGEVITY_NOTE}</p>
        )}
      </div>
      {nodeType === "teardown" && (
        <div className="space-y-1">
          {artefact?.pattern_summary && <p className="text-[10px] text-zinc-300"><span className="text-zinc-500">Pattern. </span>{artefact.pattern_summary}</p>}
          {artefact?.interpretation && <p className="text-[10px] text-zinc-300"><span className="text-zinc-500">Ours. </span>{artefact.interpretation}</p>}
          {artefact?.decode && <p className="text-[10px] text-zinc-500"><span className="text-zinc-600">Decode. </span>{artefact.decode}</p>}
          <p className="text-[10px] leading-snug text-amber-200 border border-amber-500/30 bg-amber-500/10 rounded px-1.5 py-1">{REFERENCE_LABEL}</p>
        </div>
      )}
    </div>
  );
}

function TimelineChart({ nodeId, artefact, selectedId }: { nodeId: string; artefact?: Artefact | null; selectedId?: string }) {
  const { pickEvent } = useContext(BoardCtx);
  const fields = artefact?.evidence?.fields || {};
  const points = (fields.points as { date: string; reach: number; live_ads: number }[] | undefined) || [];
  const events = (fields.events as { id: string; date: string; kind: string; label: string; ad_id?: string }[] | undefined) || [];
  const selected = selectedId || (fields.emitted_event as { id?: string } | undefined)?.id;
  if (!points.length) return <p className="text-[10px] text-zinc-500 mt-1">No series in this read.</p>;
  const w = 308, h = 128, pad = 18;
  const maxR = Math.max(...points.map((p) => Number(p.reach) || 0), 1);
  const maxL = Math.max(...points.map((p) => Number(p.live_ads) || 0), 1);
  const t0 = Date.parse(points[0].date);
  const t1 = Date.parse(points[points.length - 1].date);
  const xOf = (date: string) => {
    const t = Date.parse(date);
    const u = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
    return pad + Math.min(1, Math.max(0, u)) * (w - pad * 2);
  };
  const yR = (v: number) => h - pad - (v / maxR) * (h - pad * 2);
  const yL = (v: number) => h - pad - (v / maxL) * (h - pad * 2);
  const line = (ys: (p: { date: string; reach: number; live_ads: number }) => number) =>
    points.map((p, i) => `${i ? "L" : "M"}${xOf(p.date).toFixed(1)},${ys(p).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-1 w-full" role="img" aria-label="Reach and live ads">
      <text x={pad} y={11} fill="#5eead4" fontSize="8">Reach</text>
      <text x={w - pad} y={11} textAnchor="end" fill="#fbbf24" fontSize="8">Live ads</text>
      <path d={line((p) => yR(Number(p.reach) || 0))} fill="none" stroke="#2dd4bf" strokeWidth="1.6" />
      <path d={line((p) => yL(Number(p.live_ads) || 0))} fill="none" stroke="#fbbf24" strokeWidth="1.6" strokeDasharray="3 2" />
      {events.map((ev) => {
        const on = ev.id === selected;
        const x = xOf(ev.date);
        const removed = ev.kind === "removed";
        const stroke = removed ? "#fbbf24" : "#5eead4";
        return (
          <g key={ev.id}>
            <line x1={x} y1={pad + 4} x2={x} y2={h - 16} stroke={stroke} strokeOpacity={on ? 0.85 : 0.35} strokeWidth="1" />
            <circle
              cx={x} cy={h - 10} r={on ? 6 : 4.5}
              fill={on ? stroke : "#09090b"} stroke={stroke} strokeWidth="1.6"
              className="nodrag nopan cursor-pointer"
              role="button"
              tabIndex={0}
              aria-label={`${ev.label}. Emits this ad.`}
              onClick={(e) => { e.stopPropagation(); pickEvent(nodeId, ev); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); pickEvent(nodeId, ev); } }}
            />
          </g>
        );
      })}
    </svg>
  );
}

function Handles({ spec }: { spec: NodeSpec }) {
  const spot = (i: number, n: number) => `${((i + 1) / (n + 1)) * 100}%`;
  return (
    <>
      {spec.inputs.map((p, i) => (
        <Handle key={`in-${p.id}`} type="target" position={Position.Left} id={p.id}
          style={{ top: spot(i, spec.inputs.length), background: PORT_COLOR[p.type] || "#a1a1aa" }}
          title={`${p.id} · ${p.type}`} />
      ))}
      {spec.outputs.map((p, i) => (
        <Handle key={`out-${p.id}`} type="source" position={Position.Right} id={p.id}
          style={{ top: spot(i, spec.outputs.length), background: PORT_COLOR[p.type] || "#a1a1aa" }}
          title={`${p.id} · ${p.type}`} />
      ))}
    </>
  );
}

export function BoardNode({ id, data, selected }: NodeProps<Node<BoardData, "board">>) {
  const { keys, toggle } = useContext(BoardCtx);
  const spec = CATALOG[data.nodeType];
  if (!spec) return null;
  const Icon = spec.icon;
  const intel = spec.side === "intel";
  const status = data.status || "idle";
  const cost = data.ran && data.actual != null ? money(data.actual) : money(data.est ?? 0);
  const costTitle = data.ran ? `actual ${money(data.actual ?? 0)}` : `estimate ${money(data.est ?? 0)}`;
  const preview = data.preview;
  return (
    <article data-node={data.nodeType} style={{ width: spec.width }}
      className={`rounded-xl border bg-zinc-950/95 shadow-lg ${selected ? "ring-2 ring-offset-0" : ""} ${
        intel ? "border-teal-700/80 ring-teal-400" : "border-violet-700/80 ring-violet-400"} ${data.enabled ? "" : "opacity-60"}`}>
      <div className="overflow-hidden rounded-[11px]">
      <header className={`flex items-center gap-2 px-2.5 py-2 border-b ${intel ? "bg-teal-950/50 border-teal-900/60" : "bg-violet-950/40 border-violet-900/60"}`}>
        <span className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${intel ? "bg-teal-500/15 text-teal-300" : "bg-violet-500/15 text-violet-300"}`}>
          <Icon size={16} aria-hidden />
        </span>
        <h3 className="text-xs font-semibold text-zinc-100 truncate">{spec.headline}</h3>
        <button type="button" role="switch" aria-checked={data.enabled} aria-label={`${data.enabled ? "Disable" : "Enable"} ${spec.headline}`}
          className={`nodrag nopan ml-auto shrink-0 w-8 h-4 rounded-full relative ${data.enabled ? (intel ? "bg-teal-500" : "bg-violet-500") : "bg-zinc-700"}`}
          onClick={(e) => { e.stopPropagation(); toggle(id); }}>
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${data.enabled ? "left-4" : "left-0.5"}`} />
        </button>
      </header>
      <div className="flex flex-wrap gap-1 px-2.5 py-1.5">
        <Badge size="sm" variant={STATUS_VARIANT[status] || "default"} dot>{status}</Badge>
        <Badge size="sm" variant="info">dry</Badge>
        <Badge size="sm" variant="default" title={costTitle}>{data.ran ? cost : `est ${cost}`}</Badge>
      </div>
      {spec.needsKey && !keys[spec.needsKey] && (
        <div className="px-2.5 pb-1">
          <Link to="/settings#connections" className="nodrag nopan inline-flex text-[10px] font-medium text-amber-200 border border-amber-500/40 bg-amber-500/10 rounded-full px-2 py-0.5">
            Needs key → Settings
          </Link>
        </div>
      )}
      {intel ? <Split artefact={preview} nodeType={data.nodeType} nodeId={id} selectedEvent={data.config.event_id} /> : (
        <p className="px-2.5 pb-2 text-[10px] text-zinc-500">
          {data.nodeType === "script" && "Wraps the script stage. Dry run writes [MOCK] copy."}
          {data.nodeType === "voice" && "Wraps the voice stage. Dry run is silent audio, $0."}
          {data.nodeType === "video" && "Wraps the video stage. Dry run is a placeholder, $0."}
          {data.nodeType === "brief" && "Wraps the brief stage. Pattern summary stays above the decode."}
        </p>
      )}
      </div>
      <Handles spec={spec} />
    </article>
  );
}

export function StubNode({ data }: NodeProps<Node<StubData, "stub">>) {
  const { follow } = useContext(BoardCtx);
  const spec = CATALOG[data.nodeType];
  if (!spec) return null;
  const Icon = spec.icon;
  const intel = spec.side === "intel";
  return (
    <button type="button" data-stub={data.followId} onClick={() => follow(data.followId)}
      className={`nodrag nopan w-44 text-left rounded-xl border border-dashed px-2.5 py-2 flex items-center gap-2 bg-zinc-950/90 ${intel ? "border-teal-600" : "border-violet-500"}`}>
      <span className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${intel ? "bg-teal-500/15 text-teal-300" : "bg-violet-500/15 text-violet-300"}`}>
        <Icon size={16} aria-hidden />
      </span>
      <span>
        <span className="block text-[9px] uppercase tracking-wider text-zinc-500">{spec.side} · follow</span>
        <span className="block text-xs font-semibold text-zinc-100">{spec.headline}</span>
      </span>
    </button>
  );
}

export const nodeTypes = { board: BoardNode, stub: StubNode };
