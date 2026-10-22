// Spend from the attempt ledger. Empty live usage stays at $0. Nothing here is estimated from a mock.
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api, ts, usd } from "../lib/api";

export default function SpendPage() {
  const { data, isLoading } = useQuery({ queryKey: ["spend"], queryFn: () => api<any>("/api/spend") });
  if (isLoading || !data) return <div className="v3-page v3-loading"><Loader2 size={16} className="v3-spin" /> Loading spend</div>;
  const cap = Number(data.caps?.global || 0);
  const counted = Number(data.counted || 0);
  const pct = cap > 0 ? Math.min(100, (counted / cap) * 100) : 0;
  const providers = Object.entries(data.by_provider || {}) as [string, number][];
  const max = Math.max(...providers.map(([, v]) => Number(v)), 0.0001);
  const fallback = data.higgsfield_fallback || [];
  return (
    <div className="v3-page">
      <header className="lib-h">
        <div>
          <h1>Spend</h1>
          <p>Live calls from the attempt ledger. Dry runs stay at $0 and are not in the bars.</p>
        </div>
        <span className="v3-badge">{data.calls} live calls</span>
      </header>
      <div className="spend-grid">
        <section className="v3-card">
          <h3>Budget vs cap</h3>
          <div className="spend-fig">{usd(counted)} <small>of {usd(cap, 0)}</small></div>
          <div className="v3-capb" aria-label="Budget used"><i style={{ width: `${pct}%` }} /></div>
          <p className="v3-muted">{pct.toFixed(0)}% of the global cap. Per-job cap {usd(data.caps?.per_job, 0)}.</p>
        </section>
        <section className="v3-card">
          <h3>Forecast</h3>
          <div className="spend-fig">{usd(data.forecast?.month ?? 0)}</div>
          <p className="v3-muted">{data.forecast?.note}</p>
        </section>
        <section className="v3-card">
          <h3>Logged</h3>
          <div className="spend-fig">{usd(data.logged)}</div>
          <p className="v3-muted">Counted against caps {usd(counted)}, including reconciliation.</p>
        </section>
      </div>
      <div className="spend-split">
        <section className="v3-card">
          <h3>Cost by provider</h3>
          {providers.length ? providers.map(([name, cost]) => (
            <div key={name} className="spend-row">
              <span>{name}</span>
              <i><b style={{ width: `${(Number(cost) / max) * 100}%` }} /></i>
              <em>{usd(Number(cost), 4)}</em>
            </div>
          )) : <p className="v3-muted">No live usage.</p>}
        </section>
        <section className="v3-card">
          <h3>Top APIs</h3>
          {(data.top_apis || []).length ? (data.top_apis as any[]).map((row) => (
            <div key={`${row.provider}-${row.api}`} className="spend-api">
              <div><b>{row.api}</b><small>{row.provider} · {row.calls} calls</small></div>
              <em>{usd(row.cost, 4)}</em>
            </div>
          )) : <p className="v3-muted">No live API calls.</p>}
        </section>
      </div>
      <section className="v3-card spend-fall">
        <h3>Higgsfield fallback</h3>
        {fallback.length ? (
          <ul>{fallback.map((row: any, i: number) => (
            <li key={i}>{ts(row.started_at)} · {row.job_id}/{row.stage} · {row.model} · {row.status} · {usd(row.actual_cost_usd, 4)}</li>
          ))}</ul>
        ) : <p className="v3-muted">Higgsfield fallback has not been used.</p>}
      </section>
      <section className="v3-card">
        <h3>Recent attempts</h3>
        <div className="spend-table">
          <table>
            <thead><tr><th>Time</th><th>Job</th><th>API</th><th>Status</th><th>Cost</th></tr></thead>
            <tbody>
              {(data.recent || []).slice(0, 40).map((row: any, i: number) => (
                <tr key={i}>
                  <td>{ts(row.started_at)}</td>
                  <td>{row.job_id}/{row.stage} · {row.mode}</td>
                  <td>{row.provider}/{row.model}</td>
                  <td>{row.status}</td>
                  <td>{usd(row.actual_cost_usd, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
