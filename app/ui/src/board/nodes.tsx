import { createContext, useContext, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { CATALOG, LONGEVITY_NOTE, PORT_COLOR, REFERENCE_LABEL, type NodeSpec } from "./catalog";

export type Artefact = {
  type?: string;
  label?: string;
  pattern_summary?: string;
  interpretation?: string;
  decode?: string;
  emitted_ad_id?: string;
  ad?: { id?: string; headline?: string; advertiser?: string; status?: string; longevity_days?: number };
  page?: { url?: string; title?: string };
  evidence?: {
    source?: string;
    data_as_of?: string;
    origin?: string;
    live?: boolean;
    label?: string;
    fields?: Record<string, unknown>;
  };
  inference?: { note?: string; longevity_note?: string; match_count?: number };
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
  wired: Set<string>;
  persona: string;
}>({ keys: {}, toggle: () => {}, follow: () => {}, pickEvent: () => {}, wired: new Set(), persona: "" });

function money(n: number) {
  return `$${n.toFixed(2)}`;
}

function statusClass(status: string) {
  if (status === "done") return "ok";
  if (status === "needs review" || status === "running") return "rv";
  if (status === "failed" || status === "blocked") return "bad";
  return "q";
}

function shortDate(iso: string) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso.slice(5);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function EvidenceBox({ evidence, children }: { evidence?: Artefact["evidence"]; children?: ReactNode }) {
  return (
    <div className="v3-ev">
      <div className="v3-ev-l">Evidence</div>
      {evidence ? (
        <p className="v3-copy">{evidence.origin === "live" ? "Live tracker" : "Fixture · not a live read"} · {evidence.source || "—"}{evidence.data_as_of ? ` · ${evidence.data_as_of}` : ""}</p>
      ) : <p className="v3-muted">No read yet.</p>}
      {children}
    </div>
  );
}

function InferenceBox({ note }: { note?: string }) {
  return (
    <div className="v3-inf">
      <div className="v3-inf-l">Inference</div>
      <p className="v3-copy v3-clamp">{note || "Nothing inferred yet."}</p>
    </div>
  );
}

function TimelineChart({ nodeId, artefact, selectedId }: { nodeId: string; artefact?: Artefact | null; selectedId?: string }) {
  const { pickEvent } = useContext(BoardCtx);
  const fields = artefact?.evidence?.fields || {};
  const points = (fields.points as { date: string; reach: number; live_ads: number }[] | undefined) || [];
  const events = (fields.events as { id: string; date: string; kind: string; label: string; ad_id?: string }[] | undefined) || [];
  const selected = selectedId || (fields.emitted_event as { id?: string } | undefined)?.id;
  const selectedEvent = events.find((ev) => ev.id === selected);
  if (!points.length) return <p className="v3-muted">No series in this read.</p>;
  const w = 400, h = 96, padT = 14, padB = 14;
  const maxR = Math.max(...points.map((p) => Number(p.reach) || 0), 1);
  const maxL = Math.max(...points.map((p) => Number(p.live_ads) || 0), 1);
  const t0 = Date.parse(points[0].date);
  const t1 = Date.parse(points[points.length - 1].date);
  const xOf = (date: string) => {
    const t = Date.parse(date);
    const u = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
    return 8 + Math.min(1, Math.max(0, u)) * (w - 16);
  };
  const yR = (v: number) => padT + (1 - v / maxR) * (h - padT - padB - 6);
  const yL = (v: number) => padT + (1 - v / maxL) * (h - padT - padB - 6);
  const base = h - padB;
  const line = (ys: (p: { date: string; reach: number; live_ads: number }) => number) =>
    points.map((p, i) => `${i ? "L" : "M"}${xOf(p.date).toFixed(1)} ${ys(p).toFixed(1)}`).join(" ");
  const reach = line((p) => yR(Number(p.reach) || 0));
  const x0 = xOf(points[0].date);
  const xN = xOf(points[points.length - 1].date);
  const area = `${reach} L${xN.toFixed(1)} ${base} L${x0.toFixed(1)} ${base} Z`;
  const gid = `reach-${nodeId.replace(/[^a-z0-9]/gi, "")}`;
  const ticks = [points[0], points[Math.floor(points.length / 2)], points[points.length - 1]];
  return (
    <div>
      <div className="v3-leg">
        <span className="v3-lg"><i style={{ background: "#5ab0ff" }} />Reach</span>
        <span className="v3-lg"><i style={{ background: "#c9a0ff" }} />Live ads</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="v3-chart" role="img" aria-label="Reach and live ads">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#5ab0ff" stopOpacity="0.28" />
            <stop offset="1" stopColor="#5ab0ff" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2={w} y1={padT + g * (base - padT)} y2={padT + g * (base - padT)} stroke="#ffffff0a" />
        ))}
        <path d={area} fill={`url(#${gid})`} />
        <path d={reach} fill="none" stroke="#5ab0ff" strokeWidth="1.5" />
        <path d={line((p) => yL(Number(p.live_ads) || 0))} fill="none" stroke="#c9a0ff" strokeWidth="1.5" />
        {events.map((ev) => {
          const on = ev.id === selected;
          const x = xOf(ev.date);
          const y = 10;
          return (
            <g key={ev.id} className="nodrag nopan">
              <line x1={x} y1={y + 6} x2={x} y2={base} stroke={on ? "#7c6cff" : "#ffffff26"} strokeDasharray="2 3" />
              <rect x={x - 4} y={y - 4} width="8" height="8" transform={`rotate(45 ${x} ${y})`}
                fill={on ? "#7c6cff" : "#2e2e35"} stroke={on ? "#c4bbff" : "#55555f"} className="v3-diamond" />
              {on && <circle cx={x} cy={y} r="9" fill="none" stroke="#7c6cff" strokeOpacity="0.5" />}
              <circle cx={x} cy={y} r="8" fill="transparent" className="nodrag nopan v3-hit" role="button" tabIndex={0}
                aria-label={`${ev.label}. Emits this ad.`}
                onClick={(e) => { e.stopPropagation(); pickEvent(nodeId, ev); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); pickEvent(nodeId, ev); } }} />
            </g>
          );
        })}
        {ticks.map((p) => (
          <text key={p.date} className="v3-ax" x={xOf(p.date)} y={h - 2} textAnchor="middle">{shortDate(p.date)}</text>
        ))}
      </svg>
      {selectedEvent && <p className="v3-callout">{selectedEvent.label}{selectedEvent.date ? ` · ${shortDate(selectedEvent.date)}` : ""}</p>}
    </div>
  );
}

function FunnelSteps({ steps }: { steps: { label?: string }[] }) {
  const shown = steps.filter((s) => s.label).slice(0, 5);
  if (!shown.length) return null;
  return (
    <div className="v3-fm">
      {shown.map((s, i) => (
        <div key={`${s.label}-${i}`} style={{ display: "contents" }}>
          {i > 0 && <span className="v3-fma" />}
          <div className="v3-fmn"><small>{s.label}</small></div>
        </div>
      ))}
    </div>
  );
}

function IntelBody({ artefact, nodeType, nodeId, selectedEvent, config }: {
  artefact?: Artefact | null; nodeType: string; nodeId: string; selectedEvent?: string; config: Record<string, string>;
}) {
  const evidence = artefact?.evidence;
  const fields = evidence?.fields || {};
  const picked = (fields.picked as { headline?: string; advertiser?: string; longevity_days?: number } | undefined) || artefact?.ad;
  const steps = (fields.steps as { label?: string }[] | undefined) || [];
  const ads = (fields.ads as { headline?: string; longevity_days?: number; id?: string }[] | undefined) || [];
  if (nodeType === "market_timeline") {
    return (
      <div className="v3-bd">
        <TimelineChart nodeId={nodeId} artefact={artefact} selectedId={selectedEvent} />
        <EvidenceBox evidence={evidence} />
        <InferenceBox note={artefact?.inference?.note} />
      </div>
    );
  }
  return (
    <div className="v3-bd">
      {nodeType === "ad_feed" && (
        <>
          <div className="v3-chips">
            {config.status && <span className="v3-chip">{config.status}</span>}
            {config.longevity_min && <span className="v3-chip">≥ {config.longevity_min}d</span>}
            {config.advertiser && <span className="v3-chip">{config.advertiser}</span>}
          </div>
          {ads.length > 0 && (
            <div className="v3-thumbs">
              {ads.slice(0, 4).map((ad) => (
                <div key={ad.id || ad.headline} className={`v3-thumb ${ad.id && ad.id === config.ad_id ? "is-sel" : ""}`}>
                  <span>{ad.longevity_days != null ? `${ad.longevity_days}d` : "ad"}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {nodeType === "funnel_route" && <FunnelSteps steps={steps} />}
      {nodeType === "teardown" && <p className="v3-warn">{REFERENCE_LABEL}</p>}
      {nodeType === "teardown" && artefact?.pattern_summary && <p className="v3-copy v3-clamp">{artefact.pattern_summary}</p>}
      <EvidenceBox evidence={evidence}>
        {picked?.headline && <p className="v3-copy">{picked.advertiser ? `${picked.advertiser}: ` : ""}{picked.headline}</p>}
      </EvidenceBox>
      <InferenceBox note={artefact?.inference?.note} />
      {nodeType === "ad_breakdown" && <p className="v3-warn">{artefact?.inference?.longevity_note || LONGEVITY_NOTE}</p>}
      {nodeType === "teardown" && artefact?.decode && <p className="v3-muted v3-clamp">{artefact.decode}</p>}
    </div>
  );
}

function CreateBody({ nodeType, persona }: { nodeType: string; persona: string }) {
  return (
    <div className="v3-bd">
      {nodeType === "brief" && (
        <>
          <span className="v3-pchip"><span className="v3-av" />{persona || "Persona"}</span>
          <p className="v3-copy">Wraps the brief stage. Pattern summary stays above the decode.</p>
        </>
      )}
      {nodeType === "script" && <p className="v3-copy">Wraps the script stage. Dry run writes [MOCK] copy.</p>}
      {nodeType === "voice" && <p className="v3-copy">Wraps the voice stage. Dry run is silent audio, $0.</p>}
      {nodeType === "video" && <p className="v3-copy">Wraps the video stage. Dry run is a placeholder, $0.</p>}
    </div>
  );
}

function Handles({ spec, id }: { spec: NodeSpec; id: string }) {
  const { wired } = useContext(BoardCtx);
  const spot = (i: number, n: number) => `${((i + 1) / (n + 1)) * 100}%`;
  return (
    <>
      {spec.inputs.map((p, i) => (
        <Handle key={`in-${p.id}`} type="target" position={Position.Left} id={p.id}
          className={wired.has(`${id}:in:${p.id}`) ? "connected" : ""}
          style={{ top: spot(i, spec.inputs.length), ["--c" as string]: PORT_COLOR[p.type] || "#555" }}
          title={`${p.id} · ${p.type}`} />
      ))}
      {spec.outputs.map((p, i) => (
        <Handle key={`out-${p.id}`} type="source" position={Position.Right} id={p.id}
          className={wired.has(`${id}:out:${p.id}`) ? "connected" : ""}
          style={{ top: spot(i, spec.outputs.length), ["--c" as string]: PORT_COLOR[p.type] || "#555" }}
          title={`${p.id} · ${p.type}`} />
      ))}
    </>
  );
}

export function BoardNode({ id, data, selected }: NodeProps<Node<BoardData, "board">>) {
  const { keys, toggle, persona } = useContext(BoardCtx);
  const spec = CATALOG[data.nodeType];
  if (!spec) return null;
  const Icon = spec.icon;
  const intel = spec.side === "intel";
  const status = data.status || "idle";
  const cost = data.ran && data.actual != null ? money(data.actual) : money(data.est ?? 0);
  const live = data.preview?.evidence?.origin === "live";
  const count = data.preview?.inference?.match_count;
  return (
    <article data-node={data.nodeType} style={{ width: spec.width }}
      className={`v3-node ${selected ? "is-sel" : ""} ${data.enabled ? "" : "is-off"}`}>
      <div className="v3-node-clip">
        <header className="v3-h">
          <span className={`v3-ic ${intel ? "in" : "cr"}`}><Icon size={14} aria-hidden /></span>
          <h3 className="v3-title">{spec.headline}</h3>
          <span className="v3-hm">
            {data.nodeType === "market_timeline" && <span className={live ? "v3-live" : "v3-fix"}>{live ? "LIVE" : "FIXTURE"}</span>}
            {data.nodeType === "ad_feed" && count != null && <span className="v3-mono">{count}</span>}
            {data.nodeType !== "market_timeline" && <span className="v3-mono">$0</span>}
            {data.nodeType === "market_timeline" && <span className="v3-mono">· $0</span>}
          </span>
          <button type="button" role="switch" aria-checked={data.enabled} aria-label={`${data.enabled ? "Disable" : "Enable"} ${spec.headline}`}
            className={`nodrag nopan v3-tg ${data.enabled ? "" : "is-off"}`}
            onClick={(e) => { e.stopPropagation(); toggle(id); }}>
            <i />
          </button>
        </header>
        {spec.needsKey && !keys[spec.needsKey] && (
          <div className="v3-nk-row">
            <Link to="/settings#connections" className="nodrag nopan v3-nk">Needs key → Settings</Link>
          </div>
        )}
        {intel
          ? <IntelBody artefact={data.preview} nodeType={data.nodeType} nodeId={id} selectedEvent={data.config.event_id} config={data.config} />
          : <CreateBody nodeType={data.nodeType} persona={persona} />}
        <footer className="v3-ft">
          <span className={`v3-st ${statusClass(status)}`}>{status}</span>
          <span className="v3-cost v3-mono" title={data.ran ? `actual ${cost}` : `estimate ${cost}`}>{data.ran ? cost : cost}</span>
        </footer>
      </div>
      <Handles spec={spec} id={id} />
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
    <button type="button" data-stub={data.followId} onClick={() => follow(data.followId)} className="nodrag nopan v3-stub">
      <span className={`v3-ic ${intel ? "in" : "cr"}`}><Icon size={14} aria-hidden /></span>
      <span>
        <span className="v3-stub-k">{spec.side} · follow</span>
        <span className="v3-title">{spec.headline}</span>
      </span>
    </button>
  );
}

export const nodeTypes = { board: BoardNode, stub: StubNode };
