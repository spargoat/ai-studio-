// Rebuilt (AMN OS has no studio dashboard); uses harvested PageHeader/Badge/Button and the CreditsPage summary-row pattern.
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, PlusCircle, Loader2 } from "lucide-react";
import { PageHeader, Button, Badge } from "../components/ui";
import { Section, Kpi } from "../components/common";
import JobsTable from "../components/JobsTable";
import { api, usd } from "../lib/api";

export default function DashboardPage() {
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: () => api("/api/overview"), refetchInterval: 5000 });
  if (isLoading || !data) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  const pct = data.caps.global ? (data.spend / data.caps.global) * 100 : 0;
  return (
    <div>
      <PageHeader title="Dashboard" description="Recent jobs, spend against caps, and anything blocking a live run."
        actions={<Button icon={<PlusCircle size={14} />} onClick={() => nav("/new")}>New job</Button>} />
      <div className="grid grid-cols-2 md:grid-cols-4 border border-zinc-800 rounded-lg mb-8 bg-zinc-900/40">
        <Kpi label="Spend counted vs cap" value={usd(data.spend)} sub={<><span>of {usd(data.caps.global, 0)} · {pct.toFixed(0)}%</span>
          <div className="h-1 mt-1 bg-zinc-800 rounded"><div className="h-1 rounded bg-violet-500" style={{ width: `${Math.min(pct, 100)}%` }} /></div></>} />
        <Kpi label="Jobs" value={data.job_count} sub={Object.entries(data.counts).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ")} />
        <Kpi label="Awaiting review" value={data.pending.length} sub={`${data.library_count} in library`} />
        <Kpi label="Live preflight" value={data.blockers.length ? "Blocked" : "OK"} tone={data.blockers.length ? "bad" : "good"} sub={`${data.blockers.length} blocker(s)`} />
      </div>
      {data.blockers.length > 0 && <Section title="Blockers (from studio doctor)">
        <ul className="text-sm space-y-1">{data.blockers.map((b: string) => <li key={b} className="flex gap-2 text-red-300"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{b}</li>)}</ul>
        <p className="text-xs text-zinc-500 mt-2">Dry runs are unaffected; these only block live (paid) runs.</p></Section>}
      {data.pending.length > 0 && <Section title="Awaiting review">
        <div className="flex flex-wrap gap-2">{data.pending.slice(0, 20).map((p: any) => (
          <Link key={p.job_id + p.stage} to={`/jobs/${p.job_id}#st-${p.stage}`}><Badge variant="warning" size="sm">{p.job_id} / {p.stage} v{p.version}</Badge></Link>))}</div></Section>}
      <Section title="Recent jobs" right={<Link to="/jobs" className="text-xs text-violet-400">Run log →</Link>}><JobsTable jobs={data.jobs} /></Section>
      <details className="text-xs text-zinc-500"><summary className="cursor-pointer">Doctor output</summary><pre className="mt-2 whitespace-pre-wrap text-zinc-400">{data.doctor}</pre></details>
    </div>
  );
}
