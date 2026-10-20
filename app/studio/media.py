import json
import subprocess
from pathlib import Path

from .assets import ffmpeg_bin, font_path

FONT = font_path()
if FONT is None:
    import glob
    c = sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True))
    FONT = c[0] if c else None


def ff(args, timeout=300):
    cmd = [ffmpeg_bin("ffmpeg"), "-v", "error", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-600:]}")


_dur = {}


def duration(path):
    """ffprobe duration, cached by (path, mtime, size)."""
    try:
        st = Path(path).stat(); k = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if k not in _dur:
        _dur[k] = _duration(path)
    return _dur[k]


def _duration(path):
    r = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return round(float(json.loads(r.stdout)["format"]["duration"]), 2)
    except Exception:
        return None


def esc(text):
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def poster(video, out_jpg, t=0.5):
    ff(["-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", str(out_jpg)])
