"""Purpose names for assets. The library id stays the key. The name says what the file is for."""
import re

ASPECT = {"9:16": "9x16 HD", "1:1": "1x1", "16:9": "16x9"}
PURPOSE = {
    "finish": "VSL",
    "compose": "VSL",
    "video": "Video",
    "lipsync": "Lipsync",
    "voice": "Voice note",
    "still": "Image",
    "script": "Script",
    "llm": "Copy",
    "brief": "Brief",
}


def purpose_name(persona_display, stage, version, aspect="9:16"):
    who = (persona_display or "Asset").strip() or "Asset"
    purpose = PURPOSE.get(stage or "", (stage or "Asset").replace("_", " ").title())
    ratio = ASPECT.get(aspect or "9:16", "9x16 HD")
    try:
        ver = int(version)
    except (TypeError, ValueError):
        ver = 1
    return f"{who} – {purpose} v{ver} – {ratio}"


def filename_stem(name):
    cleaned = re.sub(r"[^\w\s.–-]", "", name or "", flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "asset"
