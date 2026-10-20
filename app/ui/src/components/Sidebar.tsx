// Harvested from AMN Production OS components/Sidebar.tsx.
// Removed: workspace selector, multi-tenant roles, global search, notifications, credit API (Supabase).
// Kept: sections, collapsed/hover/pin behaviour, theme toggle, usage meter (now spend vs cap from the studio engine).
import { useState, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard, PlusCircle, ClipboardList, Library, PackageOpen, Users, Settings, DollarSign,
  Sun, Moon, Pin, PinOff, LucideIcon,
} from "lucide-react";
import { useTheme } from "../hooks/useTheme";
import { api } from "../lib/api";

interface NavItem { path: string; icon: LucideIcon; label: string; description?: string }
interface NavSection { title: string; items: NavItem[] }

const NAV_SECTIONS: NavSection[] = [
  { title: "CREATE", items: [
    { path: "/", icon: LayoutDashboard, label: "Dashboard", description: "Jobs, spend, blockers" },
    { path: "/new", icon: PlusCircle, label: "New job", description: "Brief, estimate, dry run" },
  ] },
  { title: "LIBRARY", items: [
    { path: "/library", icon: Library, label: "Library", description: "Approved outputs" },
    { path: "/export", icon: PackageOpen, label: "Export", description: "Bundle to zip" },
    { path: "/personas", icon: Users, label: "Personas", description: "Locks and refs" },
  ] },
  { title: "MANAGE", items: [
    { path: "/jobs", icon: ClipboardList, label: "Run log", description: "All jobs and stages" },
    { path: "/spend", icon: DollarSign, label: "Spend", description: "Usage vs caps" },
    { path: "/settings", icon: Settings, label: "Settings", description: "Caps, threshold, keys" },
  ] },
];

const LS_KEY = "studio_sidebar_pinned";

export default function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  const [pinned, setPinned] = useState(() => { try { return (localStorage.getItem(LS_KEY) ?? "true") === "true"; } catch { return true; } });
  const [hovered, setHovered] = useState(false);
  const expanded = pinned || hovered || !!onNavigateIsMobile();
  useEffect(() => { try { localStorage.setItem(LS_KEY, String(pinned)); } catch {} }, [pinned]);

  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta"), refetchInterval: 10000 });
  const used = meta?.spend ?? 0;
  const limit = meta?.caps?.global ?? 0;
  const pct = limit > 0 ? (used / limit) * 100 : 0;
  const color = pct < 50 ? "#10b981" : pct < 80 ? "#f59e0b" : "#ef4444";

  return (
    <div onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      className="bg-zinc-900 border-r border-zinc-800 flex flex-col py-3 overflow-y-auto overflow-x-hidden relative h-full"
      style={{ width: expanded ? 200 : 56, transition: "width 200ms ease" }}>
      <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-violet-600/[0.06] to-transparent pointer-events-none" />
      <div className="relative mb-2 shrink-0 z-10 px-2">
        <div className={`flex items-center gap-2 rounded-lg bg-violet-600 font-bold text-sm text-white border border-violet-500/30 ${expanded ? "w-full px-2.5 py-2" : "w-9 h-9 mx-auto justify-center"}`}>
          <span className="shrink-0 w-5 h-5 flex items-center justify-center text-xs">S</span>
          {expanded && <span className="text-xs truncate flex-1">AI Studio</span>}
          {expanded && <span className={`text-[9px] px-1.5 py-0.5 rounded ${meta?.live ? "bg-red-500/30" : "bg-violet-900/60"}`}>{meta?.live ? "LIVE" : "DRY RUN"}</span>}
        </div>
      </div>
      {NAV_SECTIONS.map((section, si) => (
        <div key={section.title} className="w-full flex flex-col">
          {si > 0 && <div className="w-6 h-px bg-zinc-800 my-1.5 mx-auto" />}
          <span className={`text-zinc-600 font-bold tracking-widest mb-0.5 ${expanded ? "text-[9px] px-4 mt-1" : "text-[8px] text-center"}`}>{section.title}</span>
          {section.items.map(({ path, icon: Icon, label, description }) => {
            const isActive = path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);
            return (
              <button key={path} onClick={() => { navigate(path); onNavigate?.(); }}
                className={`flex items-center gap-2.5 transition-all duration-200 group relative ${expanded ? "px-3 py-2 mx-1.5 rounded-lg" : "w-10 h-10 rounded-lg mx-auto justify-center"} ${
                  isActive ? "text-violet-400 bg-violet-600/10 border-l-[3px] border-violet-500" : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40"}`}
                title={!expanded ? label : description} data-nav={path}>
                <Icon size={17} className="shrink-0" />
                {expanded && <span className="text-xs truncate">{label}</span>}
              </button>
            );
          })}
        </div>
      ))}
      <div className="mt-auto pt-2 shrink-0 flex flex-col items-center gap-1 px-2">
        <button onClick={() => { navigate("/spend"); onNavigate?.(); }}
          className={`group relative flex items-center gap-2 rounded-lg hover:bg-zinc-800 transition-colors ${expanded ? "w-full px-2.5 py-1.5" : "w-10 py-1 justify-center flex-col"}`}
          title={`$${used.toFixed(2)} of $${limit} cap`}>
          <div className={`rounded-full bg-zinc-700 overflow-hidden ${expanded ? "w-16 h-1.5" : "w-6 h-1"}`}>
            <div className="h-full rounded-full" style={{ width: `${Math.min(pct, 100)}%`, background: color }} />
          </div>
          <span className={`font-mono ${expanded ? "text-[10px]" : "text-[8px]"}`} style={{ color }}>${used.toFixed(expanded ? 2 : 0)}/{limit}</span>
          {expanded && <span className="text-[9px] text-zinc-600 ml-auto">cap</span>}
        </button>
        <div className="w-6 h-px bg-zinc-800 mx-auto" />
        <button onClick={toggleTheme} className={`flex items-center gap-2.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors ${expanded ? "w-full px-2.5 py-1.5" : "w-10 h-10 justify-center"}`}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}{expanded && <span className="text-xs">{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
        </button>
        <button onClick={() => setPinned(!pinned)} className={`hidden md:flex items-center gap-2.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors ${expanded ? "w-full px-2.5 py-1.5" : "w-10 h-10 justify-center"}`}>
          {pinned ? <PinOff size={15} /> : <Pin size={15} />}{expanded && <span className="text-xs">{pinned ? "Unpin sidebar" : "Pin sidebar"}</span>}
        </button>
      </div>
    </div>
  );
}

function onNavigateIsMobile() { return typeof window !== "undefined" && window.innerWidth < 768; }
