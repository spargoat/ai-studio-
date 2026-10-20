import { useLocation, useNavigate } from "react-router-dom";
import {
  ClipboardList, Library, Users, Settings, DollarSign, Workflow, LucideIcon,
} from "lucide-react";

interface NavItem { path: string; icon: LucideIcon; label: string }

const NAV: NavItem[] = [
  { path: "/board", icon: Workflow, label: "Board" },
  { path: "/library", icon: Library, label: "Library" },
  { path: "/jobs", icon: ClipboardList, label: "Runs" },
  { path: "/spend", icon: DollarSign, label: "Spend" },
  { path: "/personas", icon: Users, label: "Personas" },
];

export default function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const go = (path: string) => { navigate(path); onNavigate?.(); };
  const settings = location.pathname.startsWith("/settings");
  return (
    <nav className="v3-rail" aria-label="Studio">
      <button type="button" className="v3-logo" onClick={() => go("/board")} aria-label="AI Studio">S</button>
      {NAV.map(({ path, icon: Icon, label }) => {
        const on = location.pathname === path || location.pathname.startsWith(`${path}/`);
        return (
          <button key={path} type="button" data-nav={path} title={label} onClick={() => go(path)}
            className={`v3-ri ${on ? "on" : ""}`}>
            <Icon size={18} strokeWidth={1.6} />
            <span>{label}</span>
          </button>
        );
      })}
      <button type="button" data-nav="/settings" title="Settings" onClick={() => go("/settings")}
        className={`v3-ri bot ${settings ? "on" : ""}`}>
        <Settings size={18} strokeWidth={1.6} />
        <span>Settings</span>
      </button>
      <div className="v3-av" aria-hidden>AS</div>
    </nav>
  );
}
