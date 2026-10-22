import type { ReactNode } from "react";
import { Badge } from "./ui";
import { fileUrl, statusVariant } from "../lib/api";

export function Status({ s }: { s?: string | null }) {
  return <Badge variant={statusVariant(s)} size="sm" dot>{(s || "not run").replace(/_/g, " ")}</Badge>;
}

/** Flat section with a heading rule (no stacked cards). */
export function Section({ title, right, children, id }: { title?: ReactNode; right?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="mb-8 scroll-mt-16">
      {(title || right) && <div className="flex items-center justify-between gap-3 mb-2 pb-2 border-b border-zinc-800">
        <h2 className="text-sm font-semibold text-white">{title}</h2>{right}</div>}
      {children}
    </section>
  );
}

export function Media({ path, poster, className = "" }: { path?: string | null; poster?: string | null; className?: string }) {
  if (!path) return null;
  const u = fileUrl(path), p = path.toLowerCase();
  const cls = `rounded-md bg-black border border-zinc-800 object-contain ${className}`;
  if (/\.(png|jpe?g|webp)$/.test(p)) return <a href={u} target="_blank" rel="noreferrer"><img src={u} loading="lazy" className={cls} alt="" /></a>;
  if (p.endsWith(".mp4")) return <video src={poster ? u : `${u}#t=0.5`} poster={poster ? fileUrl(poster) : undefined} controls playsInline preload={poster ? "none" : "metadata"} className={cls} />;
  if (/\.(mp3|wav|m4a|ogg|aac)$/.test(p)) return <audio src={u} controls preload="metadata" className="w-full max-w-xs" />;
  return <a href={u} target="_blank" rel="noreferrer" className="text-xs text-violet-400 underline">{path.split("/").pop()}</a>;
}

export const th = "text-left font-medium text-zinc-500 py-2 px-2 text-xs";
export const td = "py-2 px-2 align-top";
export const tr = "border-b border-zinc-800/60 hover:bg-zinc-800/20";

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="px-4 py-3 border-r border-zinc-800 last:border-r-0">
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className={`text-xl font-bold ${tone === "bad" ? "text-red-400" : tone === "good" ? "text-emerald-400" : "text-white"}`}>{value}</div>
      {sub && <div className="text-[11px] text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  );
}
