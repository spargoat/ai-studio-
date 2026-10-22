// Fields the right-hand inspector edits. Keys match the node config the runner reads.

export type InspectorControl = "text" | "textarea" | "select";

export type InspectorField = {
  key: string;
  label: string;
  control: InspectorControl;
  aria: string;
  placeholder?: string;
  options?: { value: string; label: string }[];
  optionsFrom?: "personas" | "voices" | "funnels";
  blank?: string;
  fallback?: string;
  inputType?: "text" | "url" | "date";
};

const ratios = (values: string[]) => values.map((value) => ({ value, label: value }));

export const INSPECTOR_FIELDS: Record<string, InspectorField[]> = {
  brief: [
    { key: "persona", label: "Persona", control: "select", aria: "Brief persona", optionsFrom: "personas", blank: "Board persona" },
    { key: "offer", label: "Offer", control: "text", aria: "Offer", placeholder: "What we are offering" },
    { key: "angle", label: "Angle", control: "text", aria: "Angle", placeholder: "Our angle, not the source line" },
    { key: "length", label: "Length", control: "select", aria: "Length", fallback: "30", options: ["15", "30", "45", "60"].map((n) => ({ value: n, label: `${n}s` })) },
    { key: "platform", label: "Platform", control: "select", aria: "Platform", fallback: "instagram", options: ratios(["instagram", "tiktok", "youtube"]) },
  ],
  script: [
    { key: "script", label: "Script", control: "textarea", aria: "Script", placeholder: "Line the voice reads" },
    { key: "framework", label: "Framework", control: "select", aria: "Framework", fallback: "PAS", options: [{ value: "PAS", label: "PAS" }, { value: "AIDA", label: "AIDA" }] },
  ],
  voice: [
    { key: "voice", label: "Voice", control: "select", aria: "Voice", optionsFrom: "voices", blank: "Persona voice" },
  ],
  still: [
    { key: "prompt", label: "Image prompt", control: "textarea", aria: "Image prompt", placeholder: "Describe the owned image. Leave blank for the persona still prompt." },
    { key: "aspect_ratio", label: "Aspect", control: "select", aria: "Image aspect", fallback: "9:16", options: ratios(["9:16", "1:1", "4:5", "16:9"]) },
  ],
  video: [
    { key: "format", label: "Format", control: "select", aria: "Format", fallback: "talking_head", options: [{ value: "talking_head", label: "Talking head" }] },
    { key: "aspect", label: "Aspect", control: "select", aria: "Aspect", fallback: "9:16", options: ratios(["9:16", "1:1", "16:9"]) },
  ],
  landing: [
    { key: "offer", label: "Offer", control: "text", aria: "Landing offer", placeholder: "Offer on our page" },
    { key: "angle", label: "Angle", control: "text", aria: "Landing angle", placeholder: "Our angle" },
  ],
  funnel_route: [
    { key: "funnel_id", label: "Funnel", control: "select", aria: "Funnel", optionsFrom: "funnels", blank: "From the connected ad" },
  ],
  funnel_step: [
    { key: "label", label: "Label", control: "text", aria: "Step label" },
    { key: "kind", label: "Kind", control: "text", aria: "Step kind" },
    { key: "url", label: "URL", control: "text", aria: "Step URL", inputType: "url", placeholder: "https://" },
    { key: "state", label: "State", control: "text", aria: "Step state" },
  ],
  ad_breakdown: [],
  teardown: [],
  note: [
    { key: "title", label: "Heading", control: "text", aria: "Note heading" },
    { key: "notes", label: "Notes", control: "textarea", aria: "Note body" },
  ],
  quick_link: [
    { key: "title", label: "Link name", control: "text", aria: "Link name" },
    { key: "url", label: "URL", control: "text", aria: "Quick link URL", inputType: "url", placeholder: "https://" },
    { key: "notes", label: "Notes", control: "textarea", aria: "Link notes" },
  ],
  funnel_stage: [
    { key: "title", label: "Stage name", control: "text", aria: "Stage name" },
    { key: "status", label: "State", control: "select", aria: "Stage state", fallback: "planned", options: ["planned", "building", "ready for review", "published", "verified"].map((value) => ({ value, label: value })) },
    { key: "url", label: "URL", control: "text", aria: "Stage URL", inputType: "url", placeholder: "https://" },
    { key: "checked_on", label: "Checked on", control: "text", aria: "Checked on", inputType: "date" },
    { key: "notes", label: "Notes", control: "textarea", aria: "Stage notes" },
  ],
  ad_reference: [
    { key: "title", label: "Source ad", control: "text", aria: "Source ad" },
    { key: "url", label: "Ad record URL", control: "text", aria: "Ad record URL", inputType: "url", placeholder: "https://" },
    { key: "notes", label: "Notes", control: "textarea", aria: "Source notes" },
  ],
};

export const CREATE_INTEL_TYPES = [
  "brief", "script", "voice", "still", "video", "landing",
  "ad_breakdown", "funnel_route", "teardown", "funnel_step",
] as const;
