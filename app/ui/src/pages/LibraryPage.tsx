// Adapted from AMN OS pages/UnifiedLibraryPage.tsx (tab bar with counts) wired to /api/library.
// Tabs: All / Promoted / Finals; Supabase asset store, swipes and prompts tabs removed.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Library, Star, Film, Search, Loader2, Eye } from "lucide-react";
import { PageHeader, Button, Input, Select, Badge, Modal, EmptyState } from "../components/ui";
import { toast } from "../components/Toast";
import { Media, th, td, tr } from "../components/common";
import { api, ts } from "../lib/api";

const TABS = [{ id: "all", label: "All", icon: Library }, { id: "promoted", label: "Promoted", icon: Star }, { id: "final", label: "Final outputs", icon: Film }] as const;
const STAGES = ["", "still", "voice", "video", "lipsync", "finish", "compose", "script", "llm"];

function identityPhrase(r: { identity?: number | null; scores_json?: string | null }) {
  if (String(r.scores_json || "").includes("dry run, self-reference")) return "dry run, self-reference";
  if (r.identity != null) return Number(r.identity).toFixed(2);
  return "not scored";
}

function identityLabel(r: { identity?: number | null; passed?: number | boolean | null; scores_json?: string | null }) {
  const phrase = identityPhrase(r);
  if (phrase === "dry run, self-reference" || r.identity == null) return <span className="text-xs text-zinc-500">{phrase}</span>;
  return <Badge variant={r.passed ? "success" : "danger"} size="sm">{phrase}</Badge>;
}

export default function LibraryPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>("all"); const [q, setQ] = useState(""); const [persona, setPersona] = useState(""); const [stage, setStage] = useState("");
  const [preview, setPreview] = useState<any>(null); const [note, setNote] = useState("");
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta") });
  const qs = `q=${encodeURIComponent(q)}&persona=${persona}&stage=${stage}&promoted=${tab === "promoted" ? 1 : ""}&final=${tab === "final" ? 1 : ""}`;
  const { data, isLoading } = useQuery({ queryKey: ["library", qs], queryFn: () => api(`/api/library?${qs}`) });
  const promote = useMutation({ mutationFn: (id: number) => api(`/api/library/${id}/promote`, { body: { note } }),
    onSuccess: (_r, id) => { toast.success(`#${id} promoted`); setPreview(null); setNote(""); qc.invalidateQueries({ queryKey: ["library"] }); }, onError: (e: any) => toast.error(e.message) });
  return (
    <div>
      <PageHeader title="Library" description="Approved outputs and final renders, with prompts and scores. Promote the ones worth reusing."
        actions={<Link to="/export" className="text-xs text-violet-400">Export</Link>} />
      <div className="flex items-center gap-1 mb-3 overflow-x-auto">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors whitespace-nowrap ${tab === id ? "bg-violet-600/20 text-violet-400 border border-violet-500/30" : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"}`}>
            <Icon size={13} />{label}{tab === id && data && <span className="text-[9px] text-zinc-500 ml-0.5">{data.length}</span>}</button>))}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_10rem_10rem] gap-2 mb-4">
        <Input placeholder="Search prompts, jobs, templates, notes" value={q} onChange={(e) => setQ(e.target.value)} icon={<Search size={14} />} />
        <Select value={persona} onChange={(e) => setPersona(e.target.value)} options={[{ value: "", label: "any persona" }, ...(meta?.personas || []).map((p: any) => ({ value: p.id, label: p.display }))]} />
        <Select value={stage} onChange={(e) => setStage(e.target.value)} options={STAGES.map((s) => ({ value: s, label: s || "any stage" }))} />
      </div>
      {isLoading ? <div className="py-12 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div> : !data?.length ? <EmptyState icon={<Library size={22} />} title="Nothing here yet" description="Approved stage outputs and finished jobs land here automatically." /> :
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-zinc-700"><th className={th}>Preview</th><th className={th}>Item</th><th className={`${th} hidden md:table-cell`}>Identity</th><th className={`${th} hidden sm:table-cell`}></th></tr></thead>
        <tbody>{data.map((r: any) => (
          <tr key={r.id} className={tr}>
            <td className={`${td} w-24 sm:w-28`}><Media path={r.path} className="w-20 h-20 sm:w-24 sm:h-24 object-cover" /></td>
            <td className={td}><div className="flex items-center gap-1.5 flex-wrap"><b className="text-white">#{r.id}</b>{r.promoted ? <Badge variant="primary" size="sm">★ promoted</Badge> : null}
              <Link to={`/jobs/${r.job_id}#st-${r.stage}`} className="text-violet-400 hover:underline">{r.job_id}</Link><span className="text-zinc-400">/{r.stage} v{r.version}</span></div>
              <div className="text-[11px] text-zinc-500">{r.persona} · {r.template} · {r.mode} · {r.reason}{r.note ? ` · ${r.note}` : ""} · {ts(r.added_at)}</div>
              {r.prompt && <div className="text-[11px] text-zinc-600 line-clamp-2 mt-0.5">{r.prompt}</div>}
              <div className="flex gap-1 mt-2 sm:hidden"><Button size="xs" variant="ghost" icon={<Eye size={12} />} onClick={() => { setPreview(r); setNote(r.note || ""); }}>Open</Button>
                {!r.promoted && <Button size="xs" variant="secondary" icon={<Star size={12} />} onClick={() => promote.mutate(r.id)}>Promote</Button>}</div></td>
            <td className={`${td} hidden md:table-cell`}>{identityLabel(r)}</td>
            <td className={`${td} text-right whitespace-nowrap hidden sm:table-cell`}><Button size="xs" variant="ghost" icon={<Eye size={12} />} onClick={() => { setPreview(r); setNote(r.note || ""); }}>Open</Button>
              {!r.promoted && <Button size="xs" variant="secondary" icon={<Star size={12} />} loading={promote.isPending && promote.variables === r.id} onClick={() => promote.mutate(r.id)}>Promote</Button>}</td>
          </tr>))}</tbody></table></div>}
      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview ? `#${preview.id} ${preview.job_id}/${preview.stage} v${preview.version}` : ""} size="xl"
        footer={preview && !preview.promoted ? <><Input inputSize="sm" placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} /><Button size="sm" icon={<Star size={12} />} onClick={() => promote.mutate(preview.id)}>Promote</Button></> : undefined}>
        {preview && <div className="space-y-2"><Media path={preview.path} className="w-full max-h-[60vh]" />
          <div className="text-xs text-zinc-400">{preview.persona} · {preview.template} · {preview.mode} · identity {identityPhrase(preview)}</div>
          {preview.prompt && <pre className="text-[11px] text-zinc-400 whitespace-pre-wrap max-h-40 overflow-y-auto">{preview.prompt}</pre>}</div>}
      </Modal>
    </div>
  );
}
