// Adapted from AMN OS pages/CreditsPage.tsx: summary row, by provider / by model, recent calls table, wired to /api/spend.
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { PageHeader, Badge } from "../components/ui";
import { Section, Kpi, th, td, tr, Status } from "../components/common";
import { api, usd, ts } from "../lib/api";

export default function SpendPage() {
  const { data, isLoading } = useQuery({ queryKey: ["spend"], queryFn: () => api("/api/spend") });
  if (isLoading || !data) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  const List = ({ o }: { o: Record<string, number> }) => Object.keys(o).length ? <div>{Object.entries(o).map(([k, v]) => <div key={k} className="flex justify-between py-1.5 border-b border-zinc-800/60 text-sm"><span className="text-zinc-300 font-mono text-xs truncate">{k}</span><Badge size="sm">{usd(v, 4)}</Badge></div>)}</div> : <p className="text-sm text-zinc-500">No live usage.</p>;
  return (
    <div>
      <PageHeader title="Spend" badge={<Badge variant="info" size="sm">{data.calls} live calls</Badge>} description="Live spend from the attempt ledger, plus dashboard reconciliations. Dry runs cost $0." />
      <div className="grid grid-cols-2 md:grid-cols-4 border border-zinc-800 rounded-lg mb-8 bg-zinc-900/40">
        <Kpi label="Logged (ledger)" value={usd(data.logged)} /><Kpi label="Counted vs caps" value={usd(data.counted)} sub="includes reconciliation" />
        <Kpi label="Global cap" value={usd(data.caps.global, 0)} sub={`${((data.counted / (data.caps.global || 1)) * 100).toFixed(0)}% used`} /><Kpi label="Per-job cap" value={usd(data.caps.per_job, 0)} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8"><Section title="By provider"><List o={data.by_provider} /></Section><Section title="By model"><List o={data.by_model} /></Section></div>
      <Section title="Recent attempts (live and dry)"><div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="border-b border-zinc-700">
        <th className={th}>Time</th><th className={th}>Job / stage</th><th className={`${th} hidden md:table-cell`}>Model</th><th className={th}>Status</th><th className={`${th} text-right`}>Cost</th></tr></thead>
        <tbody>{data.recent.slice(0, 80).map((r: any, i: number) => <tr key={i} className={tr}><td className={`${td} text-zinc-500`}>{ts(r.started_at)}</td>
          <td className={td}>{r.job_id}/{r.stage} <span className={r.mode === "live" ? "text-red-400" : "text-zinc-600"}>{r.mode}</span></td>
          <td className={`${td} hidden md:table-cell font-mono text-zinc-400`}>{r.model}</td><td className={td}><Status s={r.status} /></td>
          <td className={`${td} text-right font-mono`}>{usd(r.actual_cost_usd, 4)}</td></tr>)}</tbody></table></div></Section>
    </div>
  );
}
