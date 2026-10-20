import { Link } from "react-router-dom";

export function NeedsKey({ provider }: { provider?: string }) {
  return (
    <Link to="/settings#connections" className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-200 border border-amber-500/40 bg-amber-500/10 rounded-full px-2 py-0.5">
      {provider ? <span className="text-amber-100/80">{provider}</span> : null}
      Needs key → Settings
    </Link>
  );
}
