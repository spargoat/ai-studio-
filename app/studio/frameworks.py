"""Copy framework prompts for the script/hook stage. Text only; an LLM fills them (live needs a key)."""
FRAMEWORKS = {
    "PAS": ("Write the spoken script in PAS order. Problem: name the viewer's specific problem in their words. "
            "Agitate: show what it costs them, concretely, without fear-mongering. Solution: the persona's calm, "
            "specific way through, ending on one clear next step."),
    "AIDA": ("Write the spoken script in AIDA order. Attention: the hook line. Interest: one surprising, specific "
             "point. Desire: what changes for the viewer, shown not claimed. Action: one clear next step."),
}
HOOKS = ("Then write {n} alternative opening hooks (max 12 words each), one per style: question, contrarian, "
         "number, story, pattern-interrupt. Hooks must be true to the script; no income claims, no guarantees.")
RULES = ("Voice: {cadence}. Keep it under {max_words} spoken words. No hype, no promises of returns, no third-party "
         "P&L as the persona's own. Return JSON: {{\"framework\", \"beats\": {{...}}, \"script\", \"hooks\": [...]}}.")


def prompt(params, persona_cfg, brief):
    fw = str(params.get("framework", "PAS")).upper()
    lock = (persona_cfg or {}).get("lock") or {}
    cadence = lock.get("voice_cadence") or "unknown"
    return " ".join([
        f"Topic: {brief.get('topic') or brief.get('headline') or 'see brief'}. Audience: {brief.get('audience', 'unknown')}.",
        FRAMEWORKS.get(fw, FRAMEWORKS["PAS"]),
        HOOKS.format(n=int(params.get("hooks", 5))),
        RULES.format(cadence=cadence, max_words=int(params.get("max_words", 45))),
    ])
