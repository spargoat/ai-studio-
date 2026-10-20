"""Character lock -> prompt text, and the approved reference images.

Only fields with known values are injected; 'unknown' is never guessed.

A ref is a string (legacy local path) or an entry with role, Blob URL or
pathname, and sha256. Face-locking and identity scoring use identity and face
roles only. identity_primary is the only image that may lead that list.
face_sheet_secondary is an extra face, never the primary. outfit, character,
detail, handwriting and backstory stay out of both.
"""
from pathlib import Path
from urllib.parse import urlparse

VISUAL = ("age", "face", "skin_tone", "eye_colour", "hair", "build")
FACE_ROLES = ("identity_primary", "identity_secondary", "face_sheet_secondary")
_FACE_RANK = {"identity_primary": 0, "identity_secondary": 1, "face_sheet_secondary": 2}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


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


def _is_url(value):
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _name(value):
    if not value:
        return ""
    path = urlparse(value).path if _is_url(value) else value
    return Path(path).name


def entries(raw_refs):
    """Normalise legacy path strings and Blob entries into one shape."""
    out = []
    for i, raw in enumerate(raw_refs or []):
        if isinstance(raw, str):
            url = raw if _is_url(raw) else None
            path = None if url else raw
            out.append({"i": i, "role": "identity_primary", "url": url, "path": path,
                        "pathname": None, "sha256": None, "note": None, "name": _name(raw)})
            continue
        if not isinstance(raw, dict):
            continue
        url = raw.get("url") if _is_url(raw.get("url")) else None
        path = raw.get("path")
        if path and _is_url(path):
            url = url or path
            path = None
        pathname = raw.get("pathname")
        loc = url or path or pathname
        out.append({"i": i, "role": raw.get("role") or "identity_primary", "url": url,
                    "path": path, "pathname": pathname, "sha256": raw.get("sha256"),
                    "note": raw.get("note"), "name": raw.get("name") or _name(loc)})
    return out


def location(entry):
    """Blob URL when one is stored, otherwise a local path or pathname."""
    if not entry:
        return None
    return entry.get("url") or entry.get("path") or entry.get("pathname")


def face_lock_entries(raw_refs):
    """Identity and face refs, primary first.

    face_sheet_secondary is kept only after an identity ref. It is never the
    image a face-lock call treats as the person. Outfit and the other
    non-face roles are dropped.
    """
    ranked = [e for e in entries(raw_refs) if e["role"] in FACE_ROLES]
    ranked.sort(key=lambda e: (_FACE_RANK[e["role"]], e["i"]))
    if ranked and ranked[0]["role"] == "face_sheet_secondary":
        ranked = [e for e in ranked if e["role"] != "face_sheet_secondary"]
    return ranked


def face_lock_locations(raw_refs):
    return [location(e) for e in face_lock_entries(raw_refs) if location(e)]


def refs_for_stage(raw_refs, kind):
    """Refs whose bytes this stage may download.

    image is the only kind that sends a face to a model. voice, video,
    lipsync, finish and compose use the upstream output, not the persona
    sheets. outfit, character, detail, handwriting and backstory are never
    downloaded for a stage.
    """
    if kind != "image":
        return []
    return face_lock_entries(raw_refs)


def is_image(entry):
    name = (entry or {}).get("name") or ""
    return name.lower().endswith(IMAGE_SUFFIXES)


def materialize(entry, getter=None):
    """Local file for a ref. Blob URLs download through storage.local, checked against sha256."""
    loc = location(entry)
    if not loc:
        return None
    if _is_url(loc):
        from .storage import local
        return local(loc, getter=getter, sha256=entry.get("sha256"))
    return loc if Path(loc).exists() else None
