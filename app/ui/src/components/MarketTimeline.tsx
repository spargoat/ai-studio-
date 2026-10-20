export type TimelinePoint = { date: string; reach: number; live_ads: number };
export type TimelineEvent = { id: string; date: string; kind: string; label: string; ad_id?: string };

export function MarketTimeline({
  points, events, selected, onPick,
}: {
  points: TimelinePoint[];
  events: TimelineEvent[];
  selected?: string;
  onPick: (event: TimelineEvent) => void;
}) {
  if (!points.length) return <p className="text-sm text-zinc-500">No series in this read.</p>;
  const w = 720, h = 260, padL = 36, padR = 16, padT = 28, padB = 36;
  const maxR = Math.max(...points.map((p) => Number(p.reach) || 0), 1);
  const maxL = Math.max(...points.map((p) => Number(p.live_ads) || 0), 1);
  const t0 = Date.parse(points[0].date);
  const t1 = Date.parse(points[points.length - 1].date);
  const xOf = (date: string) => {
    const t = Date.parse(date);
    const u = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
    return padL + Math.min(1, Math.max(0, u)) * (w - padL - padR);
  };
  const yR = (v: number) => h - padB - (v / maxR) * (h - padT - padB);
  const yL = (v: number) => h - padB - (v / maxL) * (h - padT - padB);
  const line = (ys: (p: TimelinePoint) => number) =>
    points.map((p, i) => `${i ? "L" : "M"}${xOf(p.date).toFixed(1)},${ys(p).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-auto" role="img" aria-label="Reach and live ads">
      <text x={padL} y={16} fill="#5eead4" fontSize="12">Reach</text>
      <text x={w - padR} y={16} textAnchor="end" fill="#fbbf24" fontSize="12">Live ads</text>
      {[0, 0.5, 1].map((u) => {
        const y = yR(maxR * u);
        return <line key={u} x1={padL} x2={w - padR} y1={y} y2={y} stroke="#27272a" />;
      })}
      <path d={line((p) => yR(Number(p.reach) || 0))} fill="none" stroke="#2dd4bf" strokeWidth="2.4" />
      <path d={line((p) => yL(Number(p.live_ads) || 0))} fill="none" stroke="#fbbf24" strokeWidth="2.2" strokeDasharray="5 3" />
      <text x={padL} y={h - 8} fill="#71717a" fontSize="11">{points[0].date}</text>
      <text x={w - padR} y={h - 8} textAnchor="end" fill="#71717a" fontSize="11">{points[points.length - 1].date}</text>
      {events.map((ev) => {
        const on = ev.id === selected;
        const x = xOf(ev.date);
        const removed = ev.kind === "removed";
        const stroke = removed ? "#fbbf24" : "#5eead4";
        return (
          <g key={ev.id}>
            <line x1={x} y1={padT} x2={x} y2={h - padB} stroke={stroke} strokeOpacity={on ? 0.9 : 0.35} />
            <circle
              cx={x} cy={h - padB} r={on ? 7 : 5.5}
              fill={on ? stroke : "#09090b"} stroke={stroke} strokeWidth="2"
              className="cursor-pointer"
              role="button"
              tabIndex={0}
              aria-label={`${ev.label}. Emits this ad.`}
              onClick={() => onPick(ev)}
              onKeyDown={(e) => { if (e.key === "Enter") onPick(ev); }}
            />
          </g>
        );
      })}
    </svg>
  );
}
