// Adapted from AMN OS components/pages/CharactersPage.tsx: character list + selected-character detail with the lock.
// Create/edit/delete, brands, workspaces and the LLM "generate lock" call removed (locks live in config/personas.yaml).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { User, Loader2 } from "lucide-react";
import { PageHeader, Badge } from "../components/ui";
import { Section } from "../components/common";
import { api } from "../lib/api";

const fmt = (v: any) => (Array.isArray(v) ? v.join("; ") : String(v));
const unknown = (v: any) => typeof v === "string" && (v.toLowerCase().includes("unknown") || v.includes("CONFLICT"));

export default function PersonasPage() {
  const { data, isLoading } = useQuery({ queryKey: ["personas"], queryFn: () => api("/api/personas") });
  const [sel, setSel] = useState<string | null>(null);
  if (isLoading || !data) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  const p = data.find((x: any) => x.id === sel) || data[0];
  return (
    <div>
      <PageHeader title="Personas" description="Character locks and reference images. Edit config/personas.yaml to change them." />
      <div className="grid grid-cols-1 md:grid-cols-[13rem_1fr] gap-6">
        <div className="flex md:flex-col gap-1 overflow-x-auto">{data.map((x: any) => (
          <button key={x.id} onClick={() => setSel(x.id)} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm whitespace-nowrap ${p.id === x.id ? "bg-violet-600/10 text-violet-400 border-l-[3px] border-violet-500" : "text-zinc-400 hover:bg-zinc-800/40"}`}>
            <User size={14} />{x.display}{x.missing.length > 0 && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="has unknown fields" />}</button>))}</div>
        <div>
          <div className="flex flex-wrap items-center gap-2 mb-1"><h2 className="text-lg font-semibold text-white">{p.display}</h2><Badge size="sm">{p.id}</Badge>
            {p.voice_name && <Badge variant="info" size="sm">voice {p.voice_name}</Badge>}{p.lora && <Badge size="sm">LoRA: {p.lora}</Badge>}</div>
          {p.look && <p className="text-sm text-zinc-400 mb-4">{p.look}</p>}
          <Section title={`Reference images (${p.refs.length})`}>
            <div className="flex gap-2 overflow-x-auto">{p.refs.map((r: any) => r.exists ? (
              <a key={r.i} href={`/api/personas/${p.id}/ref/${r.i}`} target="_blank" rel="noreferrer" className="shrink-0"><img src={`/api/personas/${p.id}/ref/${r.i}`} alt={r.name} className="h-40 rounded-md border border-zinc-800 object-cover" loading="lazy" /><div className="text-[10px] text-zinc-500 mt-1 truncate w-32">{r.name}</div></a>)
              : <div key={r.i} className="text-xs text-red-400">missing: {r.name}</div>)}</div>
          </Section>
          <Section title="Lock">
            <table className="w-full text-sm"><tbody>{Object.entries(p.lock).filter(([k]) => k !== "sources").map(([k, v]) => (
              <tr key={k} className="border-b border-zinc-800/60"><th className="text-left text-xs text-zinc-500 font-medium py-1.5 pr-3 align-top w-36">{k.replace(/_/g, " ")}</th>
                <td className={`py-1.5 text-xs ${unknown(v) ? "text-amber-400" : "text-zinc-300"}`}>{fmt(v)}</td></tr>))}</tbody></table>
            {p.missing.length > 0 && <p className="text-xs text-amber-400 mt-2">Not injected into prompts (unknown): {p.missing.join(", ")}</p>}
          </Section>
          <Section title="Prompt injection (image)"><pre className="text-[11px] text-zinc-400 whitespace-pre-wrap">{p.lock_text || "—"}</pre></Section>
          {p.lock.sources && <Section title="Sources"><ul className="text-[11px] text-zinc-500 list-disc pl-4">{p.lock.sources.map((s: string) => <li key={s}>{s}</li>)}</ul></Section>}
        </div>
      </div>
    </div>
  );
}
