// Adapted from AMN OS pages/SettingsPage.tsx (workspace/API-key/database sections) → caps, quality gate, keys (names only), fal lock.
// Read-only: values come from config/config.yaml. Supabase/workspace sections removed.
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Shield, Cable, Gauge, Loader2 } from "lucide-react";
import { PageHeader, Badge } from "../components/ui";
import { Section } from "../components/common";
import { api, usd } from "../lib/api";

const Row = ({ k, v }: { k: string; v: any }) => <div className="flex justify-between gap-4 py-1.5 border-b border-zinc-800/60 text-sm"><span className="text-zinc-400">{k}</span><span className="text-white text-right font-mono text-xs">{v}</span></div>;

export default function SettingsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/settings") });
  useEffect(() => {
    if (data && window.location.hash === "#connections") document.getElementById("connections")?.scrollIntoView();
  }, [data]);
  if (isLoading || !data) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  return (
    <div className="max-w-3xl">
      <PageHeader title="Settings" description="Read from config/config.yaml. Edit the file to change values; restart studio serve to apply." />
      <Section title={<span className="flex items-center gap-2"><Gauge size={15} className="text-violet-400" />Budget caps</span>}>
        <Row k="Global cap" v={usd(data.caps.global)} /><Row k="Per job" v={usd(data.caps.per_job)} /><Row k="Daily" v={data.caps.daily ? usd(data.caps.daily) : "none"} />
        <Row k="Spend counted against caps" v={usd(data.spend)} />
        {Object.entries(data.spend_by_provider).filter(([, v]: any) => v.logged || v.rec).map(([p, v]: any) => <Row key={p} k={`  ${p}`} v={`logged ${usd(v.logged)} · confirmed ${usd(v.confirmed)}`} />)}
      </Section>
      <Section title={<span className="flex items-center gap-2"><Shield size={15} className="text-emerald-400" />Quality gate</span>}>
        {Object.entries(data.quality).map(([k, v]: any) => <Row key={k} k={k.replace(/_/g, " ")} v={Array.isArray(v) ? v.join(", ") : String(v)} />)}
      </Section>
      <Section id="connections" title={<span className="flex items-center gap-2"><Cable size={15} className="text-amber-400" />Connections <span className="text-[11px] text-zinc-500 font-normal">status and a mask — values never leave the server</span></span>}>
        <p className="text-xs text-zinc-500 mb-2">Keys stay here. A stage that needs a missing key links back to this section.</p>
        {(data.connections || []).map((k: any) => <div key={k.id} className="flex justify-between items-center gap-3 py-1.5 border-b border-zinc-800/60 text-sm">
          <span className="text-zinc-300">{k.label} <code className="text-[11px] text-zinc-500">{k.env}</code></span>
          <span className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-zinc-400">{k.set ? k.mask : "—"}</span>
            <Badge size="sm" variant={k.set ? "success" : k.optional ? "default" : "danger"}>{k.set ? "set" : k.optional ? "not set" : "NOT SET"}</Badge>
          </span>
        </div>)}
      </Section>
      <Section title="fal account">{data.fal_lock ? <p className="text-sm text-red-300">Locked (exhausted balance) since {data.fal_lock.started_at} on {data.fal_lock.job_id}/{data.fal_lock.stage}. Live runs are blocked until it is topped up.</p> : <p className="text-sm text-zinc-400">No lock recorded.</p>}</Section>
      <p className="text-[11px] text-zinc-600">Data directory: {data.data_dir}</p>
    </div>
  );
}
