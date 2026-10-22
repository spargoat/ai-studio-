// Asset browser. Personas live here. Purpose names are the titles; the library id stays underneath.
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { Media } from "../components/common";
import { toast } from "../components/Toast";
import { api, fileUrl, ts } from "../lib/api";

const TABS = [
  { id: "pending", label: "Pending approvals" },
  { id: "personas", label: "Personas and avatars" },
  { id: "vsl", label: "VSLs" },
  { id: "voice", label: "Voice notes" },
  { id: "images", label: "Images" },
  { id: "videos", label: "Videos" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function bucket(stage: string): TabId | "other" {
  if (stage === "finish" || stage === "compose") return "vsl";
  if (stage === "voice") return "voice";
  if (stage === "still") return "images";
  if (stage === "video" || stage === "lipsync") return "videos";
  return "other";
}

export default function LibraryPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "pending") as TabId;
  const [q, setQ] = useState("");
  const [persona, setPersona] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [personaId, setPersonaId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const { data: rows, isLoading } = useQuery({ queryKey: ["library", "browser"], queryFn: () => api<any[]>("/api/library?limit=300") });
  const { data: pending } = useQuery({ queryKey: ["library", "pending"], queryFn: () => api<any[]>("/api/library/pending") });
  const { data: personas } = useQuery({ queryKey: ["personas"], queryFn: () => api<any[]>("/api/personas") });
  const promote = useMutation({
    mutationFn: (id: number) => api(`/api/library/${id}/promote`, { body: { note } }),
    onSuccess: () => { toast.success("Promoted"); setDetail(null); setNote(""); qc.invalidateQueries({ queryKey: ["library"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const setTab = (id: TabId) => {
    const next = new URLSearchParams(params);
    next.set("tab", id);
    setParams(next);
  };
  const needle = q.trim().toLowerCase();
  const matches = (row: any) => {
    if (persona && row.persona !== persona) return false;
    if (!needle) return true;
    return `${row.purpose || ""} ${row.prompt || ""} ${row.job_id || ""} ${row.library_id || row.id || ""}`.toLowerCase().includes(needle);
  };
  const counts = useMemo(() => {
    const out: Record<string, number> = { pending: (pending || []).length, personas: (personas || []).length, vsl: 0, voice: 0, images: 0, videos: 0 };
    for (const row of rows || []) {
      const key = bucket(row.stage);
      if (key !== "other") out[key] += 1;
    }
    return out;
  }, [rows, pending, personas]);
  const shown = (tab === "pending" ? pending || [] : (rows || []).filter((row) => bucket(row.stage) === tab)).filter(matches);
  const person = (personas || []).find((p: any) => p.id === (personaId || personas?.[0]?.id));
  return (
    <div className="v3-page">
      <header className="lib-h">
        <div>
          <h1>Library</h1>
          <p>Assets named by what they are for. The id stays underneath the name.</p>
        </div>
      </header>
      <div className="lib-tabs" role="tablist">
        {TABS.map((item) => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "on" : ""} onClick={() => setTab(item.id)}>
            {item.label}<span>{counts[item.id] || 0}</span>
          </button>
        ))}
      </div>
      {tab !== "personas" && (
        <div className="lib-filters">
          <label className="lib-search"><Search size={14} /><input aria-label="Filter assets" value={q} placeholder="Filter by name, prompt, or id" onChange={(e) => setQ(e.target.value)} /></label>
          <select aria-label="Persona filter" value={persona} onChange={(e) => setPersona(e.target.value)}>
            <option value="">Any persona</option>
            {(personas || []).map((p: any) => <option key={p.id} value={p.id}>{p.display}</option>)}
          </select>
        </div>
      )}
      {isLoading ? <div className="v3-loading"><Loader2 size={16} className="v3-spin" /> Loading library</div> : tab === "personas" ? (
        <div className="lib-split">
          <div className="lib-grid">
            {(personas || []).map((p: any) => (
              <button key={p.id} type="button" className={`lib-card ${person?.id === p.id ? "is-sel" : ""}`} onClick={() => setPersonaId(p.id)}>
                {p.refs?.[0]?.exists ? <img src={`/api/personas/${p.id}/ref/0`} alt="" /> : <div className="lib-ph" aria-hidden>{(p.display || "?").slice(0, 1)}</div>}
                <b>{p.display}</b>
                <small>{p.id}{p.voice_name ? ` · ${p.voice_name}` : ""}{p.refs?.[0]?.exists ? "" : " · No face sheet stored"}</small>
              </button>
            ))}
          </div>
          {person && (
            <aside className="lib-detail">
              <h2>{person.display}</h2>
              <p className="v3-muted">id {person.id}</p>
              {person.look && <p>{person.look}</p>}
              <div className="lib-refs">
                {person.refs.map((r: any) => r.exists
                  ? <a key={r.i} href={`/api/personas/${person.id}/ref/${r.i}`}><img src={`/api/personas/${person.id}/ref/${r.i}`} alt="" /><small>{r.name}</small></a>
                  : <span key={r.i} className="lib-missing"><span className="lib-ph" aria-hidden>{(person.display || "?").slice(0, 1)}</span><small>No face sheet stored</small></span>)}
              </div>
              <h3>Lock</h3>
              <dl>{Object.entries(person.lock || {}).filter(([k]) => k !== "sources").map(([k, v]) => (
                <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{Array.isArray(v) ? v.join("; ") : String(v)}</dd></div>
              ))}</dl>
            </aside>
          )}
        </div>
      ) : !shown.length ? <p className="v3-muted lib-empty">{tab === "pending" ? "Nothing is waiting on approval." : "Nothing in this section yet. Approved outputs land here with a purpose name."}</p> : (
        <div className="lib-grid">
          {shown.map((row: any) => (
            <article key={`${row.job_id}-${row.id}`} className="lib-card">
              {bucket(row.stage) === "voice"
                ? (row.path ? <audio className="lib-audio" controls preload="none" src={fileUrl(row.path)} /> : <p className="v3-muted">No audio file stored for this note.</p>)
                : <Media path={row.path} className="lib-media" />}
              <button type="button" className="lib-open" onClick={() => { setDetail(row); setNote(row.note || ""); }}>
                <b>{row.purpose || row.stage}</b>
                <small>id {row.library_id ?? row.id}</small>
              </button>
            </article>
          ))}
        </div>
      )}
      {detail && (
        <div className="lib-modal" role="dialog" aria-label={detail.purpose || "Asset"}>
          <button type="button" className="lib-back" onClick={() => setDetail(null)}>Close</button>
          <h2>{detail.purpose || detail.stage}</h2>
          <p className="v3-muted">id {detail.library_id ?? detail.id} · {detail.job_id}/{detail.stage} v{detail.version} · {ts(detail.added_at)}</p>
          <Media path={detail.path} className="lib-hero" />
          {detail.prompt && <pre>{detail.prompt}</pre>}
          {detail.review_status === "pending" && <p>Waiting on review. Approve it from the run, then it can be promoted here.</p>}
          {detail.review_status !== "pending" && !detail.promoted && (
            <div className="lib-promote">
              <input aria-label="Promotion note" value={note} placeholder="Note" onChange={(e) => setNote(e.target.value)} />
              <button type="button" onClick={() => promote.mutate(detail.id)}>Promote</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
