"""Deterministic DRY_RUN text for LLM stages. Clearly marked [MOCK]; never presented as real copy."""
import json

FRAMEWORKS = {
    "PAS": ["Problem", "Agitate", "Solution"],
    "AIDA": ["Attention", "Interest", "Desire", "Action"],
}
HOOK_STYLES = ["question", "contrarian", "number", "story", "pattern-interrupt"]


def _b(ctx, k, d):
    return (ctx.get("brief") or {}).get(k) or d


def run(task, ctx):
    p, brief = ctx.get("params") or {}, ctx.get("brief") or {}
    topic = _b(ctx, "topic", _b(ctx, "headline", "the offer"))
    if task == "visual_bible":
        return {"task": task, "text": f"[MOCK] Visual bible for {topic}: palette, light, framing, type rules "
                                      "would come from the LLM reading the brand images.", "items": []}
    if task == "creative_director":
        n = int(p.get("n", 10))
        items = [f"[MOCK prompt {i+1}/{n}] {ctx.get('prompt','')[:200]}" for i in range(n)]
        return {"task": task, "text": "*".join(items), "items": items}
    if task == "script":
        fw = p.get("framework", "PAS").upper()
        beats = FRAMEWORKS.get(fw, FRAMEWORKS["PAS"])
        lines = [f"[MOCK {b}] {topic}: one plain sentence for the {b.lower()} beat." for b in beats]
        hooks = [f"[MOCK {s} hook] {topic}" for s in HOOK_STYLES[:int(p.get("hooks", 5))]]
        script = brief.get("script") or " ".join(l.split("] ", 1)[1] for l in lines)
        return {"task": task, "framework": fw, "beats": dict(zip(beats, lines)), "hooks": hooks,
                "script": script, "text": "\n".join(lines), "items": hooks,
                "note": "brief.script kept as the spoken script" if brief.get("script") else "mock script"}
    if task == "scene_beats":
        n = int(p.get("beats", 4))
        items = [{"beat": i + 1, "shot": f"[MOCK beat {i+1}] {topic}", "camera": "static mid-shot",
                  "duration_s": 3} for i in range(n)]
        return {"task": task, "text": json.dumps(items), "items": [i["shot"] for i in items], "beats": items}
    return {"task": task, "text": f"[MOCK] {task}", "items": []}
