"""ElevenLabs TTS adapter (direct API). Only reached in --live mode."""
from pathlib import Path
import base64
import json
import requests
from .base import Provider, ProviderError, NonRetryable
from ..config import get_key
from ..media import duration


class ElevenLabsProvider(Provider):
    paid = True

    def do_voice(self, ctx):
        base = self.cfg.get("base", "https://api.elevenlabs.io/v1")
        fmt = self.cfg.get("output_format", "mp3_44100_128")
        body = {"text": ctx["script"], "model_id": ctx["model"],
                "voice_settings": ctx.get("voice_settings") or {}}
        ts = bool(self.cfg.get("with_timestamps"))
        r = requests.post(f"{base}/text-to-speech/{ctx['voice_id']}" + ("/with-timestamps" if ts else ""),
                          params={"output_format": fmt},
                          json=body, headers={"xi-api-key": get_key(self.cfg)},
                          timeout=self.cfg.get("timeout_seconds", 180))
        if r.status_code in (401, 403, 422):
            raise NonRetryable(f"elevenlabs {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise ProviderError(f"elevenlabs {r.status_code}: {r.text[:300]}")
        out = Path(ctx["out_dir"]) / "voice.mp3"
        meta_extra = {}
        if ts:   # JSON: audio_base64 + alignment {characters, character_start_times_seconds, character_end_times_seconds}
            j = r.json()
            out.write_bytes(base64.b64decode(j["audio_base64"]))
            al = j.get("alignment") or j.get("normalized_alignment")
            if al:
                ap = Path(ctx["out_dir"]) / "alignment.json"
                ap.write_text(json.dumps(al)); meta_extra["alignment_path"] = str(ap)
        else:
            out.write_bytes(r.content)
        chars = int(r.headers.get("character-cost") or len(ctx["script"]))
        return {"output_path": str(out),
                "meta": {"seconds": duration(out), "voice_id": ctx["voice_id"], "chars": chars, **meta_extra},
                "units_actual": chars / 1000.0, "request_id": r.headers.get("request-id", "")}
