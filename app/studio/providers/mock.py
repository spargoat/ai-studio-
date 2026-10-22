"""DRY_RUN provider: placeholder media made locally with ffmpeg. $0, no network.

When ffmpeg is not already on the host (the Vercel function), a fast dry run
writes a tiny file instead of downloading the static ffmpeg archive.
"""
import os
import struct
import subprocess
import zlib
from functools import lru_cache
from pathlib import Path
from .base import Provider, ProviderError
from ..assets import font_file, ffmpeg_bin
from ..media import ff, esc, duration

def mock_fast():
    """Dry placeholders stay small. STUDIO_MOCK_FAST=0 asks for full-size media."""
    return os.environ.get("STUDIO_MOCK_FAST", "1") != "0"


def frame_size():
    return (270, 480, 10) if mock_fast() else (1080, 1920, 30)


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def placeholder_bytes(kind):
    """A small file a dry stage can store when ffmpeg is not installed."""
    if kind == "png":
        raw = b"\x00" + b"\x1d\x2b\x3a"
        return (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                + _chunk(b"IDAT", zlib.compress(raw))
                + _chunk(b"IEND", b""))
    if kind == "mp3":
        return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + (b"\x00" * 413)
    payload = b"\x00" * 128
    return (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
            + b"\x00\x00\x00\x08free"
            + struct.pack(">I", 8 + len(payload)) + b"mdat" + payload)


def write_placeholder(kind, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(placeholder_bytes(kind))
    return path


def ffmpeg_ready():
    from ..assets import ffmpeg_on_disk
    return ffmpeg_on_disk("ffmpeg") is not None


def byte_media():
    """Fast dry media on a host with no ffmpeg. Skips the runtime download."""
    return mock_fast() and not ffmpeg_ready()


def placeholder_seconds(secs):
    """A dry board used to encode the full 3–15s clip at 1080p. One second is enough to prove the path."""
    try:
        secs = float(secs or 1)
    except (TypeError, ValueError):
        secs = 1.0
    if mock_fast():
        return min(secs, 1.0)
    return secs


# Kept for callers that read the constants at import. frame_size() is the live choice.
W, H, FPS = frame_size()

BG = {"marco": "0x1d2b3a", "maksim": "0x2f3a24", "callum": "0x3a2430"}


@lru_cache(maxsize=1)
def _has_drawtext():
    result = subprocess.run([ffmpeg_bin("ffmpeg"), "-hide_banner", "-filters"],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and " drawtext " in result.stdout


def _label(lines, size=46, y0=120):
    """drawtext with an explicit font file. The Vercel runtime has no fontconfig."""
    if not _has_drawtext():
        # Some local ffmpeg builds omit freetype. Keep dry media visibly tinted
        # and runnable, while the job metadata still identifies it as mock.
        return "drawbox=x=0:y=0:w=iw:h=ih:color=0x7c6cff@0.55:t=fill"
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

    def do_image(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "still.png"
        if byte_media():
            write_placeholder("png", out)
            return {"output_path": str(out), "meta": {"mock": True, "placeholder": True, "refs_used": []},
                    "units_actual": ctx["params"].get("num_images", 1), "request_id": "mock"}
        refs = [r for r in (ctx.get("refs") or []) if os.path.exists(r)]
        W, H, _fps = frame_size()
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
        secs = placeholder_seconds(ctx["video_seconds"])
        if byte_media():
            write_placeholder("mp4", out)
            return {"output_path": str(out), "meta": {"mock": True, "placeholder": True, "seconds": secs},
                    "units_actual": secs, "request_id": "mock"}
        W, H, fps = frame_size()
        preset = os.environ.get("STUDIO_FFMPEG_PRESET", "veryfast")
        if mock_fast():
            # zoompan at full length is what made a dry board take minutes per clip.
            ff(["-f", "lavfi", "-i", f"color=c={BG.get(ctx['persona'], '0x333333')}:s={W}x{H}:d={secs}:r={fps}",
                "-vf", _label(["MOCK SILENT VIDEO", f"{secs}s - {ctx['model']}"], 28, 80),
                "-t", str(secs), "-an", "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p", str(out)])
        else:
            frames = int(secs * fps)
            vf = (f"scale={W}:{H},zoompan=z='1+0.0008*on':x='iw/2-(iw/zoom/2)+6*sin(on/20)':"
                  f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={fps}," +
                  _label(["MOCK SILENT VIDEO", f"{secs}s - {ctx['model']}"], 38, 200))
            ff(["-loop", "1", "-i", ctx["inputs"]["still"], "-vf", vf, "-t", str(secs), "-an",
                "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p", str(out)])
        return {"output_path": str(out), "meta": {"mock": True, "seconds": duration(out)},
                "units_actual": secs, "request_id": "mock"}

    def do_voice(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "voice.mp3"
        secs = placeholder_seconds(ctx["audio_seconds"])
        if byte_media():
            write_placeholder("mp3", out)
            return {"output_path": str(out),
                    "meta": {"mock": True, "placeholder": True, "seconds": secs, "voice_id": ctx["voice_id"],
                             "chars": len(ctx["script"])},
                    "units_actual": len(ctx["script"]) / 1000.0, "request_id": "mock"}
        ff(["-f", "lavfi", "-i", f"sine=frequency=180:duration={secs}", "-af",
            "tremolo=f=4:d=0.7,volume=0.3", "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
        return {"output_path": str(out),
                "meta": {"mock": True, "seconds": duration(out), "voice_id": ctx["voice_id"],
                         "chars": len(ctx["script"])},
                "units_actual": len(ctx["script"]) / 1000.0, "request_id": "mock"}

    def do_lipsync(self, ctx):
        self._maybe_fail(ctx)
        out = Path(ctx["out_dir"]) / "lipsync.mp4"
        if byte_media():
            src = Path((ctx.get("inputs") or {}).get("video") or "")
            if src.is_file() and src.stat().st_size > 32:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(src.read_bytes())
            else:
                write_placeholder("mp4", out)
            return {"output_path": str(out), "meta": {"mock": True, "placeholder": True, "seconds": 1},
                    "units_actual": 1, "request_id": "mock"}
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
