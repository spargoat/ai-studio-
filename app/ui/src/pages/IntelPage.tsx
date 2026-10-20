import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesCombined, Loader2 } from "lucide-react";
import { PageHeader, Badge } from "../components/ui";
import { Section } from "../components/common";
import { MarketTimeline, type TimelineEvent } from "../components/MarketTimeline";
import { api } from "../lib/api";

export default function IntelPage() {
  const [eventId, setEventId] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["intel-timeline", eventId],
    queryFn: () => api<any>(`/api/intel/timeline${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`),
  });
  if (isLoading) return <div className="py-16 flex justify-center"><Loader2 className="animate-spin text-zinc-500" /></div>;
  if (error || !data) return (
    <div>
      <PageHeader title="Intel" description="The tracker read failed. This page does not substitute the fixture." />
      <p className="text-sm text-red-300">{(error as Error)?.message || "No timeline."}</p>
    </div>
  );
  const evidence = data.evidence || {};
  const inference = data.inference || {};
  const fixture = evidence.origin !== "live";
  const ad = evidence.ad;
  const event = evidence.emitted_event;
  const pick = (ev: TimelineEvent) => setEventId(ev.id);
  return (
    <div>
      <PageHeader title="Intel" description="Market timeline. The chart and the ad it emits are the recorded read. The reading underneath is inference."
        badge={<Badge variant={fixture ? "warning" : "success"} size="sm">{fixture ? "Fixture · not live" : "Live tracker"}</Badge>}
        actions={<ChartNoAxesCombined size={18} className="text-teal-300" />} />
      <Section title="Market timeline">
        <div className="rounded-xl border border-teal-900/70 bg-zinc-950 p-3">
          <MarketTimeline points={evidence.points || []} events={evidence.events || []} selected={event?.id} onPick={pick} />
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          {(evidence.events || []).map((ev: TimelineEvent) => (
            <button key={ev.id} type="button" onClick={() => pick(ev)}
              className={`text-xs rounded-full px-3 py-1 border ${ev.id === event?.id ? "border-teal-400 text-teal-100 bg-teal-500/15" : "border-zinc-700 text-zinc-400"}`}>
              {ev.label}
            </button>
          ))}
        </div>
      </Section>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Section title="Evidence">
          <p className={`text-sm font-medium ${fixture ? "text-amber-300" : "text-emerald-300"}`}>{evidence.label}</p>
          <p className="text-xs text-zinc-500 mt-1">{evidence.source}{evidence.data_as_of ? ` · data as of ${evidence.data_as_of}` : ""}</p>
          {event && <p className="text-sm text-zinc-200 mt-3"><span className="text-zinc-500">Event. </span>{event.label}</p>}
          {ad && <div className="mt-3 rounded-lg border border-zinc-800 p-3 text-sm">
            <div className="text-white font-medium">{ad.advertiser}: {ad.headline}</div>
            <div className="text-xs text-zinc-500 mt-1">{ad.status} · {ad.first_seen} → {ad.last_seen}{ad.longevity_days != null ? ` · ${ad.longevity_days} days` : ""}</div>
            {ad.primary_text && <p className="text-xs text-zinc-400 mt-2">{ad.primary_text}</p>}
          </div>}
        </Section>
        <Section title="Inference">
          <p className="text-sm text-zinc-300">{inference.note}</p>
          {inference.longevity_note && <p className="text-sm text-amber-200/90 mt-3">{inference.longevity_note}</p>}
        </Section>
      </div>
    </div>
  );
}
