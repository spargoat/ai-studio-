// Adapted from AMN OS pages/ExecutionLogPage.tsx: run list with status badges and filters, wired to /api/jobs.
// Workspace scoping and 0-10 star grading removed (grading = the engine's identity/visual scores + approve/reject on the job page).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardList, Loader2, Search } from "lucide-react";
import { PageHeader, Input, Select, EmptyState } from "../components/ui";
import JobsTable from "../components/JobsTable";
import { api } from "../lib/api";

const STATUSES = ["", "done", "running", "awaiting_review", "failed", "partial", "blocked_budget"];

export default function RunLogPage() {
  const [q, setQ] = useState(""); const [status, setStatus] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["jobs", q, status], queryFn: () => api(`/api/jobs?q=${encodeURIComponent(q)}&status=${status}`), refetchInterval: 5000 });
  return (
    <div>
      <PageHeader title="Run log" description="Every job, newest first. Open one for its stage timeline, outputs, scores and review." />
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <div className="flex-1"><Input placeholder="Filter by id, persona, template" value={q} onChange={(e) => setQ(e.target.value)} icon={<Search size={14} />} /></div>
        <div className="sm:w-48"><Select value={status} onChange={(e) => setStatus(e.target.value)} options={STATUSES.map((s) => ({ value: s, label: s ? s.replace(/_/g, " ") : "any status" }))} /></div>
      </div>
      {isLoading ? <div className="py-12 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>
        : data?.length ? <JobsTable jobs={data} /> : <EmptyState icon={<ClipboardList size={22} />} title="No matching jobs" description="Change the filter or start a new job." />}
    </div>
  );
}
