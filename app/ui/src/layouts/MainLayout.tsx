// Harvested from AMN Production OS layouts/MainLayout.tsx (WelcomeModal removed; toast container added).
import { Outlet } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import ToastContainer from "../components/Toast";
import { useUIStore } from "../stores/uiStore";
import { Menu, X } from "lucide-react";

export default function MainLayout() {
  const { sidebarOpen, setSidebarOpen, toggleSidebar } = useUIStore();
  return (
    <div className="h-screen w-screen flex relative">
      <ToastContainer />
      <button onClick={toggleSidebar} aria-label="Toggle sidebar"
        className="fixed top-3 left-3 z-[60] md:hidden w-9 h-9 rounded-lg bg-zinc-900 border border-zinc-700 flex items-center justify-center text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors">
        {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
      </button>
      {sidebarOpen && <div className="fixed inset-0 z-[49] bg-black/60 md:hidden" onClick={() => setSidebarOpen(false)} />}
      <div className={`fixed md:relative z-[50] h-full transition-transform duration-200 ease-in-out ${sidebarOpen ? "translate-x-0" : "-translate-x-full"} md:translate-x-0`}>
        <Sidebar onNavigate={() => setSidebarOpen(false)} />
      </div>
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <div className="flex-1 overflow-y-auto px-4 pt-14 pb-10 md:px-8 md:pt-6"><div className="max-w-6xl mx-auto"><Outlet /></div></div>
      </div>
    </div>
  );
}
