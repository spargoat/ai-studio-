// Small fetch client for the studio engine (Flask, `studio serve`). Replaces AMN OS lib/api.ts + Supabase auth.
export async function api<T = any>(path: string, opts: { method?: string; body?: any } = {}): Promise<T> {
  const r = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && (data as any).error) || `${r.status} ${r.statusText}`);
  return data as T;
}
export const fileUrl = (p: string) => `/file?p=${encodeURIComponent(p)}`;
export const usd = (n: number | null | undefined, d = 2) => `$${(n ?? 0).toFixed(d)}`;
export const ts = (s?: string | null) => (s ? s.slice(0, 16).replace("T", " ") : "");
export function statusVariant(s?: string | null): "success" | "danger" | "warning" | "default" | "info" | "primary" {
  if (!s) return "default";
  if (["succeeded", "done", "approved", "pass"].includes(s)) return "success";
  if (["failed", "rejected", "blocked_budget", "flag"].includes(s)) return "danger";
  if (["running", "awaiting_review", "pending", "partial"].includes(s)) return "warning";
  if (s === "stale") return "default";
  return "info";
}
