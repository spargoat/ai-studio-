// Rebuilt (no AMN OS equivalent). Harvested table/badge/button styles; calls `studio export` via /api/export.
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PackageOpen, Download, Loader2 } from "lucide-react";
import { PageHeader, Button, Badge, EmptyState } from "../components/ui";
import { toast } from "../components/Toast";
import { Media, Section, th, td, tr } from "../components/common";
import { api } from "../lib/api";

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;

export default function ExportPage() {
  const qc = useQueryClient();
  const [allStages, setAllStages] = useState(false); const [sel, setSel] = useState<Set<number>>(new Set());
  const { data, isLoading } = useQuery({ queryKey: ["library", "export", allStages], queryFn: () => api(`/api/library?final=${allStages ? "" : 1}`) });
  const { data: recent } = useQuery({ queryKey: ["exports"], queryFn: () => api("/api/exports") });
  const exp = useMutation({ mutationFn: () => api("/api/export", { body: { ids: [...sel], all_stages: allStages } }),
    onSuccess: (r: any) => { toast.success(`${r.name} ready (${mb(r.size)})`); qc.invalidateQueries({ queryKey: ["exports"] }); const a = document.createElement("a"); a.href = r.url; a.download = r.name; a.click(); },
    onError: (e: any) => toast.error(e.message) });
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const all = data?.length && sel.size === data.length;
  return (
    <div>
      <PageHeader title="Export" description="Bundle selected items into one zip: videos in every aspect, .srt captions, poster, caption.txt and a manifest. When Blob is configured the zip is stored there, so the link still works on the next request."
        actions={<Button icon={<Download size={14} />} disabled={!sel.size} loading={exp.isPending} onClick={() => exp.mutate()} data-action="export">Download zip ({sel.size})</Button>} />
      <label className="flex items-center gap-2 text-xs text-zinc-400 mb-3"><input type="checkbox" checked={allStages} onChange={(e) => { setAllStages(e.target.checked); setSel(new Set()); }} /> include intermediate stages (stills, voice…)</label>
      {isLoading ? <div className="py-12 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div> : !data?.length ? <EmptyState icon={<PackageOpen size={22} />} title="No final outputs in the library yet" description="Finish or approve a job first." /> :
      <div className="overflow-x-auto mb-8"><table className="w-full text-sm"><thead><tr className="border-b border-zinc-700">
        <th className={`${th} w-8`}><input type="checkbox" aria-label="select all" checked={!!all} onChange={() => setSel(all ? new Set() : new Set(data.map((r: any) => r.id)))} /></th><th className={th}>Preview</th><th className={th}>Item</th></tr></thead>
        <tbody>{data.map((r: any) => (
          <tr key={r.id} className={`${tr} cursor-pointer`} onClick={() => toggle(r.id)}>
            <td className={td}><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggle(r.id)} onClick={(e) => e.stopPropagation()} data-pick={r.id} /></td>
            <td className={`${td} w-28`} onClick={(e) => e.stopPropagation()}><Media path={r.path} className="w-24 h-24 object-cover" /></td>
            <td className={td}><b className="text-white">{r.purpose || `#${r.id}`}</b> {r.promoted ? <Badge variant="primary" size="sm">★</Badge> : null}
              <div className="text-[11px] text-zinc-500">id {r.library_id ?? r.id} · {r.job_id}/{r.stage} v{r.version} · {r.persona} · <span className={r.mode === "live" ? "text-red-400" : ""}>{r.mode}</span></div></td>
          </tr>))}</tbody></table></div>}
      {recent?.length > 0 && <Section title="Recent bundles"><ul className="text-sm divide-y divide-zinc-800">{recent.map((z: any) => (
        <li key={z.name} className="py-1.5 flex justify-between gap-2"><a href={z.url} className="text-violet-400 hover:underline truncate">{z.name}</a><span className="text-xs text-zinc-500 whitespace-nowrap">{mb(z.size)} · {z.at}</span></li>))}</ul></Section>}
    </div>
  );
}
