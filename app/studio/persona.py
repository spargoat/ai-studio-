"""Character lock -> prompt text. Only fields with known values are injected; 'unknown' is never guessed."""
VISUAL = ("age", "face", "skin_tone", "eye_colour", "hair", "build")


def _known(v):
    return bool(v) and "unknown" not in str(v).lower() and "CONFLICT" not in str(v)


def lock_text(persona_cfg, kind):
    L = (persona_cfg or {}).get("lock") or {}
    if not L or kind not in ("image", "video"):
        return ""
    parts = [f"{k.replace('_', ' ')}: {L[k]}" for k in VISUAL if _known(L.get(k))]
    w = [x for x in (L.get("wardrobe_signatures") or []) if _known(x)]
    if w:
        parts.append(f"wardrobe: {w[0]}")
    s = "Character lock (match the reference images): " + "; ".join(parts) + "."
    if L.get("banned_traits"):
        s += " Avoid: " + "; ".join(L["banned_traits"]) + "."
    return s


def missing(persona_cfg):
    L = (persona_cfg or {}).get("lock") or {}
    return [k for k, v in L.items() if isinstance(v, str) and v.strip().lower() == "unknown"]
