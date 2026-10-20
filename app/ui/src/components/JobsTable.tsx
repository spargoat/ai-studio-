import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Status, th, td, tr } from "./common";
import { usd, ts } from "../lib/api";

export default function JobsTable({ jobs }: { jobs: any[] }) {
  return (
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead><tr className="border-b border-zinc-700"><th className={th}>Job</th><th className={th}>Status</th>
        <th className={`${th} hidden md:table-cell`}>Persona</th><th className={`${th} hidden md:table-cell`}>Template</th>
        <th className={`${th} hidden md:table-cell`}>Mode</th><th className={`${th} text-right`}>Est / actual</th><th className={`${th} hidden lg:table-cell`}>Created</th></tr></thead>
      <tbody>{jobs.map((j) => (
        <tr key={j.id} className={tr}>
          <td className={td}><Link to={`/jobs/${j.id}`} className="text-violet-400 hover:underline font-medium">{j.id}</Link>
            <div className="text-[11px] text-zinc-500 md:hidden">{j.persona} · {j.template}</div></td>
          <td className={td}><span className="inline-flex items-center gap-1"><Status s={j.status} />{j.running && <Loader2 size={12} className="animate-spin text-amber-400" />}</span></td>
          <td className={`${td} hidden md:table-cell text-zinc-300`}>{j.persona}</td>
          <td className={`${td} hidden md:table-cell text-zinc-400`}>{j.template}</td>
          <td className={`${td} hidden md:table-cell`}><span className={j.mode === "live" ? "text-red-400" : "text-zinc-500"}>{j.mode}</span></td>
          <td className={`${td} text-right font-mono text-xs text-zinc-400`}>{usd(j.est)} / {usd(j.act)}</td>
          <td className={`${td} hidden lg:table-cell text-zinc-500 text-xs`}>{ts(j.created_at)}</td>
        </tr>))}
        {!jobs.length && <tr><td colSpan={7} className="py-8 text-center text-zinc-500 text-sm">No jobs yet. <Link to="/new" className="text-violet-400">Start one</Link>.</td></tr>}
      </tbody></table></div>
  );
}
