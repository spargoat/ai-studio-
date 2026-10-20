"""Automated gate checks (blank / corrupt output). Not a quality verdict."""
import subprocess
from .assets import ffmpeg_bin
from .media import duration


def _mean_luma_std(path, is_video):
    args = [ffmpeg_bin("ffmpeg"), "-v", "error", "-i", str(path)]
    if is_video:
        args += ["-ss", "0.5"]
    args += ["-frames:v", "1", "-vf", "scale=64:-2,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(args, capture_output=True, timeout=60).stdout
    if not raw:
        return None
    vals = list(raw)
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def check(kind, path):
    """Return (ok, reason)."""
    if kind == "image":
        sd = _mean_luma_std(path, False)
        if sd is None:
            return False, "image unreadable"
        return (sd > 8, f"luma std {sd:.1f} ({'ok' if sd > 8 else 'looks blank'})")
    if kind in ("voice", "video", "lipsync"):
        d = duration(path)
        if not d or d < 1:
            return False, f"duration {d}"
        if kind == "voice":
            r = subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True)
            mv = [l for l in r.stderr.splitlines() if "mean_volume" in l]
            mean = float(mv[0].split(":")[1].split()[0]) if mv else -99
            return (mean > -45, f"{d}s, mean volume {mean} dB")
        sd = _mean_luma_std(path, True)
        return (sd is not None and sd > 8, f"{d}s, luma std {sd}")
    return True, "no check"
