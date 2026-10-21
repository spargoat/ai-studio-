// The section order follows Aaron's "Video sales letter" playbook.
export const VSL_SECTIONS = [
  ["Niche headline", "Name the audience, transformation and mechanism. Check the claim against evidence."],
  ["Case studies and evidence", "Add real before and after evidence. Leave this open if proof is missing."],
  ["Identification", "Record desires, fears, failures, enemies, suspicions and market trends from research."],
  ["Core concept", "State the one belief the viewer needs before the method."],
  ["Background and credibility", "Tell the route from problem to working solution with verifiable credentials."],
  ["Thesis and method", "Teach three to five actions. Keep the service pitch out of this section."],
  ["Summary", "Tie the method back to the transformation."],
  ["Implementation options", "Compare ways to implement the method with concrete tradeoffs."],
  ["Identification again", "Recheck who this is for after the method."],
  ["Case studies again", "Optional deeper proof. Do not invent a case study."],
  ["Benefits", "Describe the change in the buyer's work or life."],
  ["Features", "Show how each feature produces a benefit with a demonstration."],
  ["Call to action", "Give one next step and point it at a working destination."],
  ["Warning", "Optional. Use only a real capacity or timing constraint."],
  ["Guarantee", "Add only the terms that are actually offered and approved."],
  ["Questions and objections", "Answer the common questions from real calls or research."],
] as const;

export type KitNode = { id: string; type: string; position: { x: number; y: number }; enabled: boolean; config: Record<string, string> };
export type KitEdge = { id: string; source: string; sourceHandle: string; target: string; targetHandle: string; label?: string };

export function vslKit(prefix: string, x = 0, y = 0): { nodes: KitNode[]; edges: KitEdge[] } {
  const nodes: KitNode[] = [];
  const add = (key: string, type: string, px: number, py: number, config: Record<string, string> = {}) => {
    nodes.push({ id: `${prefix}-${key}`, type, position: { x: x + px, y: y + py }, enabled: true, config });
  };
  add("overview", "note", 0, 0, { title: "VSL funnel", notes: "Map source evidence, write the VSL, produce the media, build the page and connect distribution. Each item is saved on this board." });
  add("source", "quick_link", 300, 0, { title: "Competitor or research source", notes: "Paste a source URL. Record what is observed and what is inferred." });
  VSL_SECTIONS.forEach(([title, guidance], i) => add(`section-${i + 1}`, "note", (i % 4) * 300, 270 + Math.floor(i / 4) * 310, {
    title: `${i + 1}. ${title}`, notes: guidance,
  }));
  const py = 1610;
  add("brief", "brief", 0, py, {});
  add("script", "script", 300, py, {});
  add("voice", "voice", 600, py, {});
  add("lip-sync", "funnel_stage", 900, py, { title: "Lip sync", status: "planned", notes: "Choose model, source video and approved voice. Connect a provider before claiming generation." });
  add("video", "video", 1200, py, { format: "talking_head", aspect: "9:16" });
  add("still", "still", 1500, py, {});
  add("landing", "landing", 0, py + 360, {});
  add("opt-in", "funnel_stage", 300, py + 360, { title: "Opt-in and lead capture", status: "planned", notes: "Capture consent, destination and form test." });
  add("application", "funnel_stage", 600, py + 360, { title: "Application and booking", status: "planned", notes: "Record qualification, reachable endings and booking URL." });
  add("follow-up", "funnel_stage", 900, py + 360, { title: "Follow-up", status: "planned", notes: "Define message timing and verify the workflow after publication." });
  add("distribution", "funnel_stage", 1200, py + 360, { title: "Distribution channel", status: "planned", notes: "Record account, creative, destination, UTM and live check." });
  add("live-link", "quick_link", 1500, py + 360, { title: "Published funnel URL", notes: "Add the live URL after a read back." });
  const edge = (from: string, out: string, to: string, inn: string): KitEdge => ({
    id: `${prefix}-${from}-${to}`, source: `${prefix}-${from}`, sourceHandle: out, target: `${prefix}-${to}`, targetHandle: inn,
  });
  return { nodes, edges: [edge("brief", "brief", "script", "brief"), edge("script", "script", "voice", "script"),
    edge("voice", "audio", "video", "audio"), edge("brief", "brief", "still", "brief"),
    edge("still", "still", "video", "still"), edge("brief", "brief", "landing", "brief")] };
}

export type KitDefinition = {
  id: string;
  name: string;
  group: "Operating maps" | "Funnel strategies" | "Asset workflows";
  description: string;
  source: string;
  stages?: readonly [string, string][];
};

// These are editable starting layouts, not claims that the external systems are connected.
export const KITS: readonly KitDefinition[] = [
  { id: "fxfarm", name: "FX Farm operating ecosystem", group: "Operating maps", source: "TWF Excalidraw acquisition-engine build spec",
    description: "Map the acquisition to retained-value spine, then record owners, systems, measures and controls.", stages: [
      ["Partner and market", "Partner opportunity, audience, market, language and commercial terms."],
      ["Persona and brand", "Audience beliefs, account ownership, identity, message and claim review."],
      ["Competitive intelligence", "Observed ads, routes and offers. Keep inferred steps and unknowns distinct."],
      ["Creative and proof", "Brief, script, still, video, voice, edit and approval. Preserve source and proof context."],
      ["Paid, organic and partner distribution", "Accounts, campaigns, posts, links, UTMs and launch checks."],
      ["Telegram acquisition and nurture", "Channel entry, content cadence, voice notes, proof and CTA."],
      ["Bot, CRM and human conversation", "Qualification, intent, handoff, setter and closer outcomes."],
      ["Broker activation", "Registration, KYC, deposit, attribution, product access and first use."],
      ["Product and retention", "Signal/product delivery, support, activity, inactivity and reactivation."],
      ["Economics and reinvestment", "Reconcile costs, retained activity, collected payouts and budget decisions."],
      ["People and capacity", "Named owner, queue, response standard, QA and escalation for each handoff."],
      ["Data and control plane", "Source IDs, event history, scorecard, claims gate, access and run evidence."],
    ] },
  { id: "teardown", name: "Competitor funnel teardown", group: "Operating maps", source: "Funnel architectures; TWF Excalidraw acquisition-engine build spec",
    description: "Collect ads and destinations, distinguish observed from inferred, then form an owned hypothesis.", stages: [
      ["Competitor and offer", "Record advertiser, market, audience and offer with source links."],
      ["Creative inventory", "Add source ads to the canvas; group by concept, format, claim and date."],
      ["Public route", "Map only reachable pages, links, forms and redirects in observed order."],
      ["Conversion mechanic", "Record CTA, capture, booking, checkout or conversation path."],
      ["Unknown and inferred", "List hidden steps and what evidence would resolve them."],
      ["Owned hypothesis", "Write the transferable mechanic without copying identity, claims or creative."],
      ["Experiment and learning", "Specify variant, destination, measurement and decision rule."],
    ] },
  { id: "paid-lead", name: "Paid lead to application", group: "Funnel strategies", source: "Funnel architectures",
    description: "Run traffic through opt-in, education, qualification and a verified booking path.", stages: [
      ["Ad and audience", "Creative, channel, account, approved claim and UTM."],
      ["Opt-in page", "Lead magnet, consent, form and submit event."],
      ["Education asset", "Guide, assessment, Loom or process-selling video."],
      ["Application", "Experience, intent, qualification and reachable endings."],
      ["Booking", "Working calendar destination, confirmation and handoff."],
      ["Follow-up", "Message sequence, exclusions and reply ownership."],
      ["Outcome", "Qualified application, attended call, sale and acquisition cost."],
    ] },
  { id: "profile-dm", name: "Profile to DM to booking", group: "Funnel strategies", source: "Profile funnel architecture",
    description: "Use the social profile as the conversion surface and route conversations to a call.", stages: [
      ["Traffic asset", "Video ad, carousel, one-pager or organic content."],
      ["Profile", "Bio, pinned content, proof, link and account ownership."],
      ["Nurture", "Stories, long-form, retargeting and repeated belief shifts."],
      ["DM trigger", "Comment, story reply or direct conversation CTA."],
      ["Setter and routing", "Conversation framework, qualification, owner and SLA."],
      ["Booking and pre-call", "Form, working calendar, confirmation and education."],
      ["Follow-up and outcome", "No-show, downsell, close and next step."],
    ] },
  { id: "challenge", name: "Challenge or event funnel", group: "Funnel strategies", source: "Challenge funnel build",
    description: "Coordinate registration, attendance, community, offer and post-event follow-up.", stages: [
      ["Offer and dates", "Challenge promise, content, owners, date and access rules."],
      ["Traffic and partners", "Ads, organic posts, partner links and UTMs."],
      ["Registration page", "Ticket options, form, consent and conversion event."],
      ["Confirmation", "Video, calendar, community access and any approved upgrade."],
      ["Warm-up", "Email and group content before the event."],
      ["Delivery", "Sessions, links, presenters, support and attendance tracking."],
      ["Offer and follow-up", "CTA, checkout or booking, objections and outcome read-back."],
    ] },
  { id: "low-ticket", name: "Low-ticket ascension", group: "Funnel strategies", source: "Persona funnels; Funnel architectures",
    description: "Map a free entry point through a first purchase, recurring value and higher-tier offer.", stages: [
      ["Audience and entry", "Persona, channel, free content or lead magnet."],
      ["Capture and nurture", "Opt-in, consent, first-use delivery and messages."],
      ["Tripwire", "Offer, page, price, payment link and fulfilment."],
      ["Recurring tier", "Subscription value, billing terms and retention experience."],
      ["Flagship or service", "Qualification, offer, sales path and delivery capacity."],
      ["Economics", "Receipts, refunds, conversion, retention and acquisition cost."],
    ] },
  { id: "offer-hub", name: "Multi-offer hub", group: "Funnel strategies", source: "Funnel architectures",
    description: "Route cold visitors to distinct offers, each with its own conversion mechanic.", stages: [
      ["Audience entry", "Channel, query, intent and source attribution."],
      ["Routing hub", "Clear offer choices, audience cues and no dead ends."],
      ["Offer page A", "Proof, process, calculator or other conversion mechanic."],
      ["Offer page B", "Distinct promise, proof, CTA and handoff."],
      ["Offer page C", "Distinct promise, proof, CTA and handoff."],
      ["Shared qualification", "Form, scoring or booking route with reachable endings."],
      ["Measurement", "Segment choice, page-to-conversion rates and booked outcomes."],
    ] },
  { id: "partner-launch", name: "Partner and brand launch", group: "Operating maps", source: "TWF Excalidraw acquisition-engine build spec",
    description: "Provision a partner, brand, traffic path, conversion coverage and operating scorecard.", stages: [
      ["Partner opportunity", "Audience, relationship source, terms and owner."],
      ["Qualification and service tier", "Market fit, capacity, commercial approval and launch scope."],
      ["Brand and accounts", "Identity, domains, social accounts, credentials, recovery and permissions."],
      ["Assets and funnel", "Persona, offer, creative, landing, channel, bot and links."],
      ["Human coverage", "Setter, closer, onboarding and escalation owners."],
      ["Launch readiness", "QA the route, events, claims, access and support."],
      ["Scorecard", "Source to active user, partner quality, economics and next decision."],
    ] },
  { id: "creative-sprint", name: "Creative testing sprint", group: "Asset workflows", source: "Running creative tests on Meta",
    description: "Turn a hypothesis into controlled variants, a test plan and recorded learning.", stages: [
      ["Research and hypothesis", "Source ads, audience insight, claim and expected mechanism."],
      ["Baseline", "Choose an existing creative and record its actual performance."],
      ["Variables", "Isolate hook, angle, persona, offer, visual or proof."],
      ["Variant briefs", "Define meaningful changes, not cosmetic duplicates."],
      ["Production", "Script, image, video, audio, edit and QA."],
      ["Launch", "Campaign, budget, destination, UTM and approval."],
      ["Readout", "Record spend, conversion evidence, learnings and scale/hold/kill decision."],
    ] },
  { id: "production", name: "Multi-asset production", group: "Asset workflows", source: "Creative arsenal matrix; TWF Excalidraw acquisition-engine build spec",
    description: "Plan and produce stills, scripts, voice, video, edits, proof and channel-ready variants.", stages: [
      ["Asset brief", "Audience, angle, placement, format, source and claim constraints."],
      ["Script and storyboard", "Hook, beats, shot list, CTA and source lineage."],
      ["Image and stills", "Prompt, references, character consistency and generated output. Provider integration pending."],
      ["Voice and lip sync", "Voice approval, spoken text, source video and lip-sync output."],
      ["Video and edit", "Generated or filmed clips, sequence, captions, polish and QA."],
      ["Proof and approval", "Verify result source and context, consent, redaction and claims review."],
      ["Variants and handoff", "Aspect ratios, thumbnails, copy, file links and destination."],
    ] },
  { id: "list-launch", name: "List launch campaign", group: "Asset workflows", source: "Twelve-day list launch campaign",
    description: "Map launch, problem, differentiation and verified closing messages across channels.", stages: [
      ["Offer and claims", "Audience, actual offer, evidence, terms and working destination."],
      ["Launch asset", "Announcement, specific outcome and reply/DM CTA."],
      ["Problem series", "Three distinct buyer problems with real stories or proof."],
      ["Differentiation series", "Three reasons the method differs, backed by evidence."],
      ["Close series", "Real deadline or capacity only; verify before sending."],
      ["Replies and routing", "Owner, response SLA, qualification and booking/checkout path."],
      ["Readout", "Delivery, replies, conversions, receipts and follow-up learning."],
    ] },
  { id: "vsl", name: "VSL funnel", group: "Funnel strategies", source: "Video sales letter",
    description: "Sixteen script sections plus media, page, lead capture and distribution stages." },
];

export function kitById(id: string): KitDefinition | undefined { return KITS.find((kit) => kit.id === id); }

export function buildKit(id: string, prefix: string, x = 0, y = 0): { nodes: KitNode[]; edges: KitEdge[] } {
  if (id === "vsl") return vslKit(prefix, x, y);
  const kit = kitById(id);
  if (!kit?.stages) throw new Error(`Unknown board kit: ${id}`);
  const nodes: KitNode[] = [
    { id: `${prefix}-overview`, type: "note", position: { x, y }, enabled: true, config: { title: kit.name, notes: `${kit.description}\nSource: ${kit.source}. Add owners, links, evidence and decisions to each stage.` } },
    { id: `${prefix}-source`, type: "quick_link", position: { x: x + 310, y }, enabled: true, config: { title: "Source and working links", notes: "Add research, reference board, live destinations and operating documents." } },
  ];
  kit.stages.forEach(([title, notes], index) => nodes.push({
    id: `${prefix}-stage-${index + 1}`, type: "funnel_stage",
    position: { x: x + (Math.floor(index / 4) % 2 ? 3 - index % 4 : index % 4) * 310, y: y + 290 + Math.floor(index / 4) * 300 },
    enabled: true, config: { title: `${index + 1}. ${title}`, status: "planned",
      notes: id === "fxfarm" ? `${notes}\n\nInput:\nTrigger:\nAction:\nOwner:\nSystem:\nMetric:\nControl:\nRun evidence:` : notes },
  }));
  const edges: KitEdge[] = kit.stages.slice(1).map(([title], index) => ({
    id: `${prefix}-handoff-${index + 1}`, source: `${prefix}-stage-${index + 1}`, sourceHandle: "flow",
    target: `${prefix}-stage-${index + 2}`, targetHandle: "flow", label: `→ ${title}`,
  }));
  if (["production", "creative-sprint", "profile-dm", "paid-lead"].includes(id)) {
    const py = y + 310 + Math.ceil(kit.stages.length / 4) * 300;
    const add = (key: string, type: string, column: number, row = 0) => nodes.push({
      id: `${prefix}-${key}`, type, position: { x: x + column * 310, y: py + row * 310 }, enabled: true, config: {},
    });
    const link = (from: string, out: string, to: string, input: string) => edges.push({
      id: `${prefix}-${from}-${to}`, source: `${prefix}-${from}`, sourceHandle: out,
      target: `${prefix}-${to}`, targetHandle: input,
    });
    add("brief", "brief", 0);
    if (id === "paid-lead") {
      add("script", "script", 1);
      add("landing", "landing", 2);
      link("brief", "brief", "script", "brief");
      link("brief", "brief", "landing", "brief");
    } else {
      add("script", "script", 1);
      add("voice", "voice", 2);
      add("video", "video", 3);
      add("still", "still", 2, 1);
      link("brief", "brief", "script", "brief");
      link("script", "script", "voice", "script");
      link("brief", "brief", "still", "brief");
      link("voice", "audio", "video", "audio");
      link("still", "still", "video", "still");
    }
  }
  return { nodes, edges };
}
