import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Cable, Gauge, KeyRound, Loader2, Shield, SlidersHorizontal, Users } from "lucide-react";
import { api, usd } from "../lib/api";

type Conn = { id: string; label: string; env?: string; set?: boolean; mask?: string; optional?: boolean };
type SectionId = "connections" | "caps" | "defaults" | "members" | "security";

const NAV: { id: SectionId; label: string; icon: typeof Cable }[] = [
  { id: "connections", label: "Connections", icon: Cable },
  { id: "caps", label: "Spend caps", icon: Gauge },
  { id: "defaults", label: "Defaults", icon: SlidersHorizontal },
  { id: "members", label: "Members", icon: Users },
  { id: "security", label: "Security", icon: Shield },
];

const COPY: Record<string, { title: string; blurb: string; detail: string }> = {
  adintel: { title: "Ad Intel tracker", blurb: "Read API · ads-tracker", detail: "URL and shared secret · read-only · set in the server environment" },
  fal: { title: "fal", blurb: "Stills, video, lipsync", detail: "Used when a live video stage runs" },
  elevenlabs: { title: "ElevenLabs", blurb: "Voiceover", detail: "Used when a live voice stage runs" },
  llm: { title: "LLM", blurb: "Briefs and scripts", detail: "Vercel AI Gateway · OIDC on Vercel, or AI_GATEWAY_API_KEY. Anthropic and OpenAI stay optional overrides." },
  higgsfield: { title: "Higgsfield", blurb: "Backup image/video provider", detail: "Optional fallback. Not used by the board chain." },
};

function sectionFromHash(hash: string): SectionId {
  const id = hash.replace("#", "");
  return NAV.some((item) => item.id === id) ? id as SectionId : "connections";
}

export default function SettingsPage() {
  const location = useLocation();
  const [section, setSection] = useState<SectionId>(() => sectionFromHash(location.hash));
  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/settings") });
  useEffect(() => { setSection(sectionFromHash(location.hash)); }, [location.hash]);

  return (
    <div className="v3 v3-page">
      <header className="v3-top">
        <div className="v3-crumb">Workspace / <b>Settings</b></div>
        <div className="v3-sp" />
        <span className="v3-saved">Keys are stored server-side and never shown in full</span>
      </header>
      <div className="v3-settings">
        <nav className="v3-snav" aria-label="Settings sections">
          <h6>SETTINGS</h6>
          {NAV.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" className={`v3-si ${section === id ? "on" : ""}`} onClick={() => setSection(id)}>
              <Icon size={15} />{label}
            </button>
          ))}
        </nav>
        <div className="v3-main">
          {isLoading || !data ? <div className="v3-loading"><Loader2 className="v3-spin" size={18} /> Loading settings</div> : (
            <>
              {section === "connections" && <Connections data={data} />}
              {section === "caps" && <Caps data={data} />}
              {section === "defaults" && <Defaults data={data} />}
              {section === "members" && <Members />}
              {section === "security" && <Security data={data} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Connections({ data }: { data: any }) {
  const rows: Conn[] = data.connections || [];
  const url = rows.find((r) => r.id === "adintel_url");
  const secret = rows.find((r) => r.id === "adintel_secret");
  const both = !!(url?.set && secret?.set);
  const cards = [
    {
      id: "adintel",
      ...COPY.adintel,
      status: both ? "connected" : (url?.set || secret?.set ? "incomplete" : "not set"),
      color: both ? "#3ecf8e" : "#6b6b76",
      mask: (secret?.set ? secret.mask : url?.set ? url.mask : "") || "",
    },
    ...rows.filter((r) => r.id !== "adintel_url" && r.id !== "adintel_secret").map((r) => {
      const held = r.id === "fal" && data.fal_lock;
      const copy = COPY[r.id] || { title: r.label, blurb: r.env || "", detail: "Set in the server environment" };
      return {
        id: r.id, ...copy,
        status: held ? "on hold" : r.set ? "connected" : "not set",
        color: held ? "#e5a54b" : r.set ? "#3ecf8e" : "#6b6b76",
        mask: r.set ? (r.mask || "") : "",
        detail: held ? `Locked since ${data.fal_lock.started_at}. Live runs stay blocked until it is topped up.` : copy.detail,
      };
    }),
  ];
  return (
    <section id="connections">
      <div className="v3-sh">
        <div>
          <h2>Connections</h2>
          <p>The only place keys are entered. Boards and nodes show &quot;Needs key → Settings&quot; instead of a field.</p>
        </div>
        <button type="button" className="v3-btn" disabled title="This page does not call providers">Test all</button>
      </div>
      <div className="v3-banner"><KeyRound size={14} />Dry run stays on until a key is connected <b>and</b> live mode is switched on in the server. Values are never shown here.</div>
      <div className="v3-cgrid">
        {cards.map((card) => (
          <article key={card.id} className="v3-cc">
            <div className="v3-ch">
              <span className="v3-cic"><Cable size={16} /></span>
              <div>
                <div className="v3-ct">{card.title}</div>
                <div className="v3-muted">{card.blurb}</div>
              </div>
              <span className="v3-sd" style={{ ["--s" as string]: card.color }}>{card.status}</span>
            </div>
            <div className="v3-cr">
              <div className="v3-mask v3-mono">{card.mask || "Not set"}</div>
              <button type="button" className="v3-btn" disabled title="Keys are set in the server environment">Test</button>
              <button type="button" className="v3-btn" disabled title="Keys are set in the server environment">{card.mask ? "Replace" : "Add key"}</button>
            </div>
            <div className="v3-muted">{card.detail}</div>
          </article>
        ))}
      </div>
      <div className="v3-row3">
        <Caps data={data} compact />
        <Defaults data={data} compact />
        <Members compact />
      </div>
    </section>
  );
}

function Caps({ data, compact }: { data: any; compact?: boolean }) {
  const global = Number(data.caps?.global || 0);
  const spend = Number(data.spend || 0);
  const pct = global > 0 ? Math.min(100, (spend / global) * 100) : 0;
  return (
    <section className="v3-card" id={compact ? undefined : "caps"}>
      <h3><Gauge size={15} />Spend caps</h3>
      <div className="v3-cap">
        <div className="v3-kv"><span>Global</span><b className="v3-mono">{usd(spend)} / {usd(global)}</b></div>
        <div className="v3-capb"><i style={{ width: `${pct}%` }} /></div>
      </div>
      <div className="v3-kv"><span>Per job</span><b className="v3-mono">{usd(data.caps?.per_job)}</b></div>
      <div className="v3-kv"><span>Daily</span><b>{data.caps?.daily ? usd(data.caps.daily) : "Not set"}</b></div>
      {!compact && Object.entries(data.spend_by_provider || {}).filter(([, v]: any) => v.logged || v.rec).map(([p, v]: any) => (
        <div key={p} className="v3-kv"><span>{p}</span><span className="v3-mono">logged {usd(v.logged)} · confirmed {usd(v.confirmed)}</span></div>
      ))}
      <p className="v3-muted">Caps are read from config. This page does not edit them.</p>
    </section>
  );
}

function Defaults({ data, compact }: { data: any; compact?: boolean }) {
  return (
    <section className="v3-card" id={compact ? undefined : "defaults"}>
      <h3><SlidersHorizontal size={15} />Defaults</h3>
      <div className="v3-fld">
        <label>Run mode</label>
        <div className="v3-seg"><span className="on">Dry run</span><span>Live</span></div>
      </div>
      <p className="v3-copy">Dry run is the board default and stays at $0. Live is a server flag, not a switch on this page.</p>
      {!compact && Object.entries(data.quality || {}).map(([k, v]: any) => (
        <div key={k} className="v3-kv"><span>{k.replace(/_/g, " ")}</span><span>{Array.isArray(v) ? v.join(", ") : String(v)}</span></div>
      ))}
    </section>
  );
}

function Members({ compact }: { compact?: boolean }) {
  return (
    <section className="v3-card" id={compact ? undefined : "members"}>
      <h3><Users size={15} />Members</h3>
      <p className="v3-copy">This workspace has no member list. Access is the server environment, not an invite.</p>
      {!compact && <p className="v3-muted">Nothing here can add or remove a person.</p>}
    </section>
  );
}

function Security({ data }: { data: any }) {
  return (
    <section className="v3-card" id="security">
      <h3><Shield size={15} />Security</h3>
      <p className="v3-copy">Secrets stay in environment variables. Connections show a mask when a name is set, and never the value.</p>
      <p className="v3-muted">Data directory: {data.data_dir}</p>
      {data.fal_lock
        ? <p className="v3-warn">fal is locked (exhausted balance) since {data.fal_lock.started_at} on {data.fal_lock.job_id}/{data.fal_lock.stage}.</p>
        : <p className="v3-muted">No fal lock recorded.</p>}
    </section>
  );
}
