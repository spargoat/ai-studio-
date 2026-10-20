// Rebuilt for the stage engine. Uses the RunDetail layout from AMN OS ExecutionLogPage (summary bar, per-node rows
// with status badges, asset strip) plus harvested Badge/Button/Input. Polls while the job runs.
import { useEffect, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, RotateCcw, Check, X, BookmarkPlus, ChevronDown, ChevronRight } from "lucide-react";
import { PageHeader, Button, Badge, Input } from "../components/ui";
import { toast } from "../components/Toast";
import { Status, Media, Section } from "../components/common";
import { api, usd, ts, fileUrl } from "../lib/api";

function Scores({ sc }: { sc: any }) {
  if (!sc) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      <Badge variant={sc.passed ? "success" : "danger"} size="sm">{sc.passed ? "PASS" : "FLAG"}</Badge>
      <Badge variant="info" size="sm">identity {sc.identity == null ? "n/a" : sc.identity.toFixed(2)}</Badge>
      {sc.visual && <Badge size="sm">{sc.visual.width}x{sc.visual.height} · sharp {Math.round(sc.visual.sharpness)}</Badge>}
      <Badge size="sm" title="needs an LLM key">copy/brand/engagement: not scored</Badge>
      {(sc.flags || []).map((f: string) => <div key={f} className="w-full text-[11px] text-red-400">{f}</div>)}
    </div>
  );
}

function StageRow({ jid, s, running }: { jid: string; s: any; running: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = useState(""); const [open, setOpen] = useState(false);
  const last = s.runs[s.runs.length - 1];
  const inv = () => { qc.invalidateQueries({ queryKey: ["job", jid] }); qc.invalidateQueries({ queryKey: ["overview"] }); };
  const review = useMutation({
    mutationFn: (verdict: string) => api(`/api/jobs/${jid}/review/${s.id}`, { body: { verdict, note } }),
    onSuccess: (_r, v) => { toast.success(`${s.id} v${last.version} ${v}${v === "approved" ? " and added to the library" : ""}`); setNote(""); inv(); },
    onError: (e: any) => toast.error(e.message),
  });
  const rerun = useMutation({ mutationFn: () => api(`/api/jobs/${jid}/rerun/${s.id}`, { method: "POST" }),
    onSuccess: () => { toast.info(`Re-running ${s.id}`); inv(); }, onError: (e: any) => toast.error(e.message) });
  const addLib = useMutation({ mutationFn: () => api(`/api/library/add`, { body: { job_id: jid, stage: s.id, version: last.version } }),
    onSuccess: () => { toast.success(`${s.id} v${last.version} added to the library (promoted)`); inv(); }, onError: (e: any) => toast.error(e.message) });
  const older = s.runs.slice(0, -1).reverse();
  return (
    <section id={`st-${s.id}`} className="scroll-mt-16 border-b border-zinc-800 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-white w-20">{s.id}</span>
        <Status s={last ? (last.review_status && last.status === "succeeded" ? last.review_status : last.status) : null} />
        {s.review && <Badge variant="warning" size="sm">review gate</Badge>}
        {last?.stale && <Badge size="sm">stale</Badge>}
        <span className="text-[11px] text-zinc-500 font-mono">{s.provider} · {s.model}</span>
        {s.model !== s.default_model && <Badge size="sm" title="template default">default {s.default_model}</Badge>}
      </div>
      {last && <div className="mt-2 grid grid-cols-1 md:grid-cols-[minmax(0,320px)_1fr] gap-4">
        <div>{last.output_path && <Media path={last.output_path} poster={last.poster} className="w-full max-h-80" />}</div>
        <div className="text-xs text-zinc-400 space-y-1">
          <div><b className="text-white">v{last.version}</b> · {last.mode} · {usd(last.actual_cost_usd, 3)} · tries {last.attempts} · {ts(last.finished_at || last.started_at)}</div>
          <Scores sc={last.score} />
          {last.exports && <div>exports: {Object.entries(last.exports).map(([a, p]: any) => <a key={a} href={fileUrl(p)} target="_blank" rel="noreferrer" className="text-violet-400 mr-2 underline">{a}</a>)}</div>}
          {last.review_note && <div>note: {last.review_note}</div>}
          {last.error && last.status !== "succeeded" && <div className="text-red-400 whitespace-pre-wrap">{String(last.error).slice(0, 400)}</div>}
          <div className="flex flex-col sm:flex-row gap-2 pt-2">
            {last.status === "succeeded" && s.id !== "brief" && <>
              <div className="flex-1"><Input inputSize="sm" placeholder="Reason (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} /></div>
              <div className="flex gap-2 flex-wrap">
                <Button size="sm" variant="success" icon={<Check size={13} />} loading={review.isPending && review.variables === "approved"} onClick={() => review.mutate("approved")} data-approve={s.id}>Approve v{last.version}</Button>
                <Button size="sm" variant="danger" icon={<X size={13} />} disabled={!note.trim()} title={note.trim() ? "" : "Write a reason first"} loading={review.isPending && review.variables === "rejected"} onClick={() => review.mutate("rejected")}>Reject</Button>
                <Button size="sm" variant="ghost" icon={<BookmarkPlus size={13} />} loading={addLib.isPending} onClick={() => addLib.mutate()} title="Add to library as promoted">Library</Button>
              </div></>}
            {s.id !== "brief" && <Button size="sm" variant="secondary" icon={<RotateCcw size={13} />} disabled={running} loading={rerun.isPending} onClick={() => rerun.mutate()}>Re-run</Button>}
          </div>
        </div>
      </div>}
      {older.length > 0 && <div className="mt-2">
        <button className="text-[11px] text-zinc-500 flex items-center gap-1" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{older.length} earlier version{older.length > 1 ? "s" : ""}</button>
        {open && <div className="flex gap-3 overflow-x-auto pt-2">{older.map((r: any) => (
          <div key={r.id} className="shrink-0 w-44 text-[11px] text-zinc-500"><Media path={r.output_path} poster={r.poster} className="w-44 h-28" />
            <div className="mt-1">v{r.version} <Status s={r.review_status || r.status} /></div>{r.review_note && <div>note: {r.review_note}</div>}</div>))}</div>}
      </div>}
    </section>
  );
}

export default function JobDetailPage() {
  const { id = "" } = useParams(); const loc = useLocation(); const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["job", id], queryFn: () => api(`/api/jobs/${id}`),
    refetchInterval: (q: any) => (q.state.data?.running || q.state.data?.job?.status === "running" ? 1000 : 6000) });
  const resume = useMutation({ mutationFn: () => api(`/api/jobs/${id}/resume`, { method: "POST" }),
    onSuccess: () => { toast.info("Running due stages"); qc.invalidateQueries({ queryKey: ["job", id] }); }, onError: (e: any) => toast.error(e.message) });
  useEffect(() => { if (data && loc.hash) document.getElementById(loc.hash.slice(1))?.scrollIntoView(); }, [!!data]);
  if (isLoading) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  if (error || !data) return <div className="text-red-400 text-sm">Job not found. <Link to="/jobs" className="text-violet-400">Run log</Link></div>;
  const j = data.job; const running = data.running || j.status === "running";
  const done = data.stages.filter((s: any) => s.runs.length && s.runs[s.runs.length - 1].status === "succeeded").length;
  return (
    <div>
      <PageHeader title={j.id} badge={<Status s={j.status} />}
        description={`${j.persona} · ${j.template} · ${j.mode} · est ${usd(j.est)} · actual ${usd(j.act)}`}
        actions={<Button icon={<Play size={14} />} loading={resume.isPending} disabled={running} onClick={() => resume.mutate()}>Run due stages</Button>} />
      {j.brief?.script && <p className="text-xs text-zinc-500 italic -mt-3 mb-4 line-clamp-3">{j.brief.script}</p>}
      <ol className="flex gap-1 overflow-x-auto pb-2 mb-2" aria-label="timeline">
        {data.stages.map((s: any) => { const l = s.runs[s.runs.length - 1]; const st = l ? (l.status === "succeeded" ? (l.review_status || "succeeded") : l.status) : "pending";
          const col = st === "approved" || st === "succeeded" ? "bg-emerald-500" : st === "running" ? "bg-amber-400 animate-pulse" : st === "pending" && l ? "bg-amber-500" : st === "failed" || st === "rejected" ? "bg-red-500" : "bg-zinc-700";
          return <li key={s.id} className="flex-1 min-w-16"><a href={`#st-${s.id}`} className="block"><div className={`h-1.5 rounded-full ${col}`} />
            <div className="text-[11px] text-zinc-400 mt-1 truncate">{s.id}</div><div className="text-[10px] text-zinc-600 truncate">{l ? st.replace(/_/g, " ") : "not run"}</div></a></li>; })}
      </ol>
      <div className="text-xs text-zinc-500 mb-2 flex items-center gap-2">{running && <Loader2 size={12} className="animate-spin text-amber-400" />}
        {done}/{data.stages.length} stages succeeded{running ? " · running, updating live" : ""}</div>
      <div>{data.stages.map((s: any) => <StageRow key={s.id} jid={id} s={s} running={running} />)}</div>
      <Section title="Events"><div className="text-[11px] text-zinc-500 space-y-0.5 max-h-64 overflow-y-auto font-mono">
        {data.events.map((e: any) => <div key={e.id}><span className="text-zinc-600">{(e.created_at || "").slice(11, 19)}</span> {e.stage || ""} <span className={e.level === "error" ? "text-red-400" : ""}>{e.level}</span>: {e.message}</div>)}</div></Section>
    </div>
  );
}
