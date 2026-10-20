"""Runtime files that are not in git: OpenCV zoo face models and a static ffmpeg.

Models are the YuNet detector and SFace embedder named in score.py
(yunet.onnx, sface.onnx), fetched from the public opencv/opencv_zoo repo.
ffmpeg is a static Linux build, kept compressed in the function bundle and
unpacked to /tmp on first use (the deployment filesystem is read-only).
"""
import os
import stat
import tarfile
import urllib.request
from pathlib import Path

from . import APP_DIR

# Official OpenCV zoo (GitHub raw follows Git LFS to the model bytes).
MODELS = {
    "yunet.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"


def _download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def _writable(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def model_dir():
    """Directory that holds yunet.onnx and sface.onnx, downloading them if missing."""
    preferred = APP_DIR / "models"
    ready = (preferred / "yunet.onnx").exists() and (preferred / "sface.onnx").exists()
    if ready and (preferred / "sface.onnx").stat().st_size > 10000:
        return preferred
    dest_dir = preferred if _writable(preferred) else Path("/tmp/studio-models")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        dest = dest_dir / name
        if dest.exists() and dest.stat().st_size > 10000:
            continue
        _download(url, dest)
    return dest_dir


def font_path():
    candidates = [
        APP_DIR / "fonts" / "DejaVuSans-Bold.ttf",
        Path("/tmp/studio-fonts/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 1000:
            return str(path)
    dest = APP_DIR / "fonts" / "DejaVuSans-Bold.ttf"
    if not _writable(dest.parent):
        dest = Path("/tmp/studio-fonts/DejaVuSans-Bold.ttf")
    try:
        _download(FONT_URL, dest)
    except Exception:
        return None
    return str(dest) if dest.exists() else None


def ffmpeg_bin(name):
    """Path to ffmpeg or ffprobe: bundled binary, /tmp unpack of the static archive, or PATH."""
    override = os.environ.get("FFMPEG_BIN") if name == "ffmpeg" else os.environ.get("FFPROBE_BIN")
    candidates = [Path(override)] if override else []
    candidates += [APP_DIR / "bin" / name, Path("/tmp/studio-bin") / name]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    archive = APP_DIR / "bin" / "ffmpeg-linux64.tar.xz"
    if archive.is_file():
        dest = Path("/tmp/studio-bin")
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                base = Path(member.name).name
                if base in ("ffmpeg", "ffprobe") and member.isfile():
                    target = dest / base
                    if target.exists():
                        continue
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    target.write_bytes(src.read())
                    target.chmod(target.stat().st_mode | stat.S_IEXEC)
        unpacked = dest / name
        if unpacked.is_file():
            return str(unpacked)
    return name
