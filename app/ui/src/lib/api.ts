// Small fetch client for the studio engine (Flask, `studio serve`). Replaces AMN OS lib/api.ts + Supabase auth.
export const INTEL_TIMEOUT_MS = 18000;

export async function api<T = any>(path: string, opts: { method?: string; body?: any; timeoutMs?: number } = {}): Promise<T> {
  const timeoutMs = opts.timeoutMs;
  const ctrl = new AbortController();
  const timer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : undefined;
  try {
    const r = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: timeoutMs ? ctrl.signal : undefined,
    });
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && (data as any).error) || `${r.status} ${r.statusText}`);
    return data as T;
  } catch (e) {
    const name = e && typeof e === "object" && "name" in e ? String((e as { name?: string }).name) : "";
    if (timeoutMs && name === "AbortError") throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Tracker reads. One client retry is configured on the query; this bounds a hung function. */
export function intel<T = any>(path: string): Promise<T> {
  return api<T>(path, { timeoutMs: INTEL_TIMEOUT_MS });
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
