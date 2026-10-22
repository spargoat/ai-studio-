"""DRY_RUN provider: placeholder media made locally with ffmpeg. $0, no network."""
import os
from pathlib import Path
from .base import Provider, ProviderError
from ..assets import font_file
from ..media import ff, esc, duration

# STUDIO_MOCK_FAST=1: render placeholders small/low-fps (tests); outputs are throwaway either way
FAST = os.environ.get("STUDIO_MOCK_FAST") == "1"
W, H, FPS = (270, 480, 10) if FAST else (1080, 1920, 30)

BG = {"marco": "0x1d2b3a", "maksim": "0x2f3a24", "callum": "0x3a2430"}


def _label(lines, size=46, y0=120):
    """drawtext with an explicit font file. The Vercel runtime has no fontconfig."""
    font = esc(font_file())
    parts = []
    for i, line in enumerate(lines):
        parts.append(f"drawtext=fontfile='{font}':text='{esc(line)}':fontcolor=white:fontsize={size}:"
                     f"x=(w-text_w)/2:y={y0 + i * int(size * 1.5)}:box=1:boxcolor=black@0.55:boxborderw=12")
    return ",".join(parts)


class MockProvider(Provider):
    paid = False

    def _maybe_fail(self, ctx):
        fail = ctx.get("fail_inject") or {}
        stage = ctx["stage"]
        if stage in fail and ctx["attempt"] <= fail[stage]:
            raise ProviderError(f"MOCK forced failure at {stage} (attempt {ctx['attempt']}): "
                                f"simulated provider 503 Service Unavailable")

    def _ref_files(self, ctx):
        """Local paths for face refs. A Blob URL is downloaded; a failure skips that ref."""
        from ..storage import is_url, local
        sha = ctx.get("ref_sha256") or {}
        out = []
        for r in ctx.get("refs") or []:
            if not isinstance(r, str):
                continue
            try:
                path = local(r, sha256=sha.get(r)) if is_url(r) else r
            except Exception:
                continue
            if path and os.path.exists(path):
                out.append(path)
                break  # the mock still paints the primary face only
        return out

    def do_image(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "still.png"
        refs = self._ref_files(ctx)
        label = _label(["DRY RUN - MOCK STILL", f"{ctx['persona_display']}", f"would call {ctx['model']}"], 40, 1500)
        if refs:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "eq=saturation=0.6:brightness=-0.05," + label)
            ff(["-i", refs[0], "-vf", vf, "-frames:v", "1", str(out)])
        else:
            ff(["-f", "lavfi", "-i", f"color=c={BG.get(ctx['persona'], '0x333333')}:s={W}x{H}",
                "-vf", label, "-frames:v", "1", str(out)])
        return {"output_path": str(out), "meta": {"mock": True, "refs_used": refs[:1]},
                "units_actual": ctx["params"].get("num_images", 1), "request_id": "mock"}

    def do_video(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "video.mp4"
        secs = ctx["video_seconds"]
        fps = FPS
        frames = int(secs * fps)
        vf = (f"scale={W}:{H},zoompan=z='1+0.0008*on':x='iw/2-(iw/zoom/2)+6*sin(on/20)':"
              f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={fps}," +
              _label(["MOCK SILENT VIDEO", f"{secs}s - {ctx['model']}"], 38, 200))
        ff(["-loop", "1", "-i", ctx["inputs"]["still"], "-vf", vf, "-t", str(secs), "-an",
            "-c:v", "libx264", "-preset", os.environ.get("STUDIO_FFMPEG_PRESET", "veryfast"), "-pix_fmt", "yuv420p", str(out)])
        return {"output_path": str(out), "meta": {"mock": True, "seconds": duration(out)},
                "units_actual": secs, "request_id": "mock"}

    def do_voice(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "voice.mp3"
        secs = ctx["audio_seconds"]
        ff(["-f", "lavfi", "-i", f"sine=frequency=180:duration={secs}", "-af",
            "tremolo=f=4:d=0.7,volume=0.3", "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
        return {"output_path": str(out),
                "meta": {"mock": True, "seconds": duration(out), "voice_id": ctx["voice_id"],
                         "chars": len(ctx["script"])},
                "units_actual": len(ctx["script"]) / 1000.0, "request_id": "mock"}

    def do_lipsync(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "lipsync.mp4"
        vf = _label(["MOCK LIP-SYNC", ctx["model"]], 38, 320)
        ff(["-stream_loop", "-1", "-i", ctx["inputs"]["video"], "-i", ctx["inputs"]["voice"],
            "-map", "0:v", "-map", "1:a", "-vf", vf, "-shortest", "-c:v", "libx264",
            "-preset", os.environ.get("STUDIO_FFMPEG_PRESET", "veryfast"), "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])
        d = duration(out)
        return {"output_path": str(out), "meta": {"mock": True, "seconds": d},
                "units_actual": d, "request_id": "mock"}

    def do_llm(self, ctx):
        import json
        from ..mocktext import run
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "text.json"
        res = run(ctx["params"].get("task", ctx["stage"]), ctx)
        out.write_text(json.dumps(res, indent=2))
        return {"output_path": str(out), "meta": {"mock": True, "task": res["task"], "items": len(res.get("items") or [])},
                "units_actual": 1, "request_id": "mock"}

    do_script = do_llm
