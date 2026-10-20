// Adapted from AMN OS pages/QuickActionsPage.tsx: action picker (icon/label/desc) + form + result panel,
// wired to the studio engine: templates instead of quick actions, /api/estimate and /api/jobs (dry runs only).
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Video, Image, FileText, Wand2, Calculator, Play, Lock } from "lucide-react";
import { PageHeader, Button, Input, Textarea, Select, Badge } from "../components/ui";
import { toast } from "../components/Toast";
import { Section, th, td, tr } from "../components/common";
import { api, usd } from "../lib/api";

const TEMPLATE_INFO: Record<string, { label: string; desc: string; icon: typeof Video; color: string; needs: string[] }> = {
  talking_head: { label: "Talking head", desc: "Still, voice, video, lipsync, finish, compose", icon: Video, color: "#ec4899", needs: ["script"] },
  talking_head_scripted: { label: "Scripted talking head", desc: "Writes the script from a topic, then the talking head", icon: Wand2, color: "#f472b6", needs: ["topic"] },
  static_ad: { label: "Static ad", desc: "Image ad with headline and CTA in three aspects", icon: Image, color: "#8b5cf6", needs: ["topic", "headline", "cta"] },
  script_to_scene_beats: { label: "Script to scene beats", desc: "Breaks a script into shot beats", icon: FileText, color: "#10b981", needs: ["script"] },
};

export default function NewJobPage() {
  const nav = useNavigate(); const qc = useQueryClient();
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta") });
  const [f, setF] = useState<any>({ persona: "", template: "talking_head", mode: "dry", script: "", setting: "", topic: "", headline: "", cta: "", id: "" });
  const [blocked, setBlocked] = useState<string | null>(null);
  const set = (k: string) => (e: any) => setF((x: any) => ({ ...x, [k]: e.target.value }));
  const persona = f.persona || meta?.personas?.[0]?.id || "";
  const brief = () => ({ persona, template: f.template, script: f.script, setting: f.setting, topic: f.topic, headline: f.headline, cta: f.cta, id: f.id });

  const est = useMutation({ mutationFn: () => api("/api/estimate", { body: { brief: brief() } }), onError: (e: any) => toast.error(e.message) });
  const start = useMutation({
    mutationFn: () => api("/api/jobs", { body: { brief: brief(), mode: f.mode } }),
    onSuccess: (r: any) => { qc.invalidateQueries(); toast.success(`Dry run ${r.id} started`); nav(`/jobs/${r.id}`); },
    onError: (e: any) => { if (f.mode === "live") setBlocked(e.message); else toast.error(e.message); },
  });
  const info = TEMPLATE_INFO[f.template];
  const templates: string[] = meta?.templates || Object.keys(TEMPLATE_INFO);

  return (
    <div>
      <PageHeader title="New job" description="Pick a template and persona, write the brief, check the estimate, then start a dry run." />
      <Section title="Template">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
          {templates.map((t) => { const i = TEMPLATE_INFO[t] || { label: t, desc: "", icon: FileText, color: "#6b7280", needs: [] }; const Icon = i.icon; const on = f.template === t;
            return (
              <button key={t} onClick={() => setF((x: any) => ({ ...x, template: t }))} data-template={t}
                className={`text-left px-3 py-2.5 rounded-lg border transition-colors ${on ? "border-violet-500 bg-violet-600/10" : "border-zinc-800 hover:border-zinc-600 hover:bg-zinc-800/40"}`}>
                <div className="flex items-center gap-2"><Icon size={15} style={{ color: i.color }} /><span className="text-sm text-white font-medium">{i.label}</span></div>
                <div className="text-[11px] text-zinc-500 mt-0.5">{i.desc}</div>
              </button>); })}
        </div>
      </Section>
      <Section title="Brief">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Select label="Persona" value={persona} onChange={set("persona")} options={(meta?.personas || []).map((p: any) => ({ value: p.id, label: p.display }))} />
          <Select label="Mode" value={f.mode} onChange={(e) => { setBlocked(null); set("mode")(e); }}
            options={[{ value: "dry", label: "Dry run (mock providers, $0)" }, { value: "live", label: "Live (paid)" }]} />
          <Input label="Job id (optional)" placeholder="auto" value={f.id} onChange={set("id")} />
          <div className="md:col-span-3"><Textarea label={`Script${info?.needs.includes("script") ? "" : " (optional; this template writes it)"}`} rows={5} value={f.script} onChange={set("script")} placeholder="What the persona says." /></div>
          <div className="md:col-span-3"><Input label="Setting" value={f.setting} onChange={set("setting")} placeholder="a bright, lived-in room" /></div>
          {(info?.needs.includes("topic") || f.template === "static_ad") && <Input label="Topic" value={f.topic} onChange={set("topic")} />}
          {f.template === "static_ad" && <><Input label="Headline" value={f.headline} onChange={set("headline")} /><Input label="CTA" value={f.cta} onChange={set("cta")} /></>}
        </div>
        {f.mode === "live" && <div className="mt-3 flex gap-2 items-start text-sm text-amber-300 border border-amber-500/30 bg-amber-500/10 rounded-lg px-3 py-2">
          <Lock size={14} className="mt-0.5 shrink-0" /><span>{blocked || (meta?.fal_lock ? `Live runs are blocked: fal has been locked (exhausted balance) since ${meta.fal_lock.started_at}. Top up first.` : "Live runs start from the CLI so the spend guards stay explicit. This app starts dry runs only.")}</span></div>}
        <div className="flex flex-wrap gap-2 mt-4">
          <Button variant="secondary" icon={<Calculator size={14} />} loading={est.isPending} onClick={() => est.mutate()}>Estimate cost</Button>
          <Button icon={<Play size={14} />} loading={start.isPending} onClick={() => start.mutate()} data-action="start">{f.mode === "live" ? "Start live run" : "Start dry run"}</Button>
        </div>
      </Section>
      {est.data && <Section title="Estimate" right={<Badge variant="info" size="sm">one clean pass, no retries</Badge>}>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-zinc-700"><th className={th}>Stage</th><th className={`${th} hidden md:table-cell`}>Model</th><th className={th}>Units</th><th className={`${th} text-right`}>USD</th></tr></thead>
          <tbody>{(est.data as any).rows.map((r: any) => <tr key={r.stage} className={tr}><td className={td}>{r.stage}</td><td className={`${td} hidden md:table-cell font-mono text-xs text-zinc-400`}>{r.model}</td>
            <td className={`${td} text-zinc-400`}>{r.units} {r.unit}</td><td className={`${td} text-right font-mono`}>{usd(r.est_usd, 4)}</td></tr>)}
            <tr><td className={`${td} font-semibold`}>Total</td><td className="hidden md:table-cell" /><td className={`${td} text-xs text-zinc-500`}>worst case ×3 {usd((est.data as any).total * 3)}</td>
              <td className={`${td} text-right font-mono font-semibold`}>{usd((est.data as any).total, 4)}</td></tr></tbody></table></div>
        <p className="text-xs text-zinc-500 mt-2">This is what a live run would cost. Dry runs use mock providers and cost $0.</p></Section>}
    </div>
  );
}
