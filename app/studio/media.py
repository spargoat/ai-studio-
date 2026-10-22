import json
import subprocess
from pathlib import Path

from .assets import ffmpeg_bin, font_file


def ff(args, timeout=300):
    """Run ffmpeg. Stdout is discarded so a chatty filter cannot fill the function's memory."""
    cmd = [ffmpeg_bin("ffmpeg"), "-v", "error", "-y", *args]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except OSError as e:
        raise RuntimeError(f"ffmpeg failed: {e}") from None
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(r.stderr or '').strip()[-600:]}")


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
    from .assets import ffmpeg_on_disk
    probe = ffmpeg_on_disk("ffprobe")
    if not probe:
        return None
    try:
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return round(float(json.loads(r.stdout)["format"]["duration"]), 2)
    except Exception:
        return None


def esc(text):
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def poster(video, out_jpg, t=0.5):
    from .assets import ffmpeg_on_disk
    if ffmpeg_on_disk("ffmpeg") is None:
        return
    ff(["-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", str(out_jpg)])
