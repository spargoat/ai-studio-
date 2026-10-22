"""Runtime files that are not in the function bundle.

OpenCV zoo face models (yunet.onnx, sface.onnx) and a static ffmpeg are
fetched into /tmp on first use. When Blob is configured, an existing
`runtime/<name>` object is used; otherwise the public URL is downloaded
and then stored in Blob for the next instance.

Nothing here is downloaded during import, and a local file (or a binary
already on PATH) always wins, so tests stay offline.
"""
import mimetypes
import os
import shutil
import stat
import tarfile
from pathlib import Path

from . import APP_DIR
from . import storage

# Official OpenCV zoo (GitHub raw follows Git LFS to the model bytes).
MODELS = {
    "yunet.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
FONT_FAMILY = "DejaVu Sans"
FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
FFMPEG_ARCHIVE = "ffmpeg-linux64.tar.xz"


def _download(url, dest, session=None):
    storage.download(url, dest, session=session)


def _ready(directory, names, min_size):
    directory = Path(directory)
    return all((directory / name).is_file() and (directory / name).stat().st_size >= min_size for name in names)


def cached_download(dest, url, pathname, min_size, session=None):
    """Local file, else Blob `pathname`, else `url` (then upload to Blob)."""
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size >= min_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = storage.blob_url(pathname, session=session)
    _download(blob or url, dest, session=session)
    if dest.stat().st_size < min_size:
        raise RuntimeError(f"{dest.name} is {dest.stat().st_size} bytes, expected at least {min_size}")
    if storage.enabled() and not blob:
        try:
            storage.put_file(pathname, dest.read_bytes(), mimetypes.guess_type(dest.name)[0], session=session)
        except Exception:
            pass
    return dest


def model_dir(session=None):
    """Directory that holds yunet.onnx and sface.onnx, downloading them if missing."""
    preferred = APP_DIR / "models"
    if _ready(preferred, MODELS, 10000):
        return preferred
    dest_dir = Path(os.environ.get("STUDIO_MODEL_DIR") or "/tmp/studio-models")
    if _ready(dest_dir, MODELS, 10000):
        return dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        dest = dest_dir / name
        if dest.is_file() and dest.stat().st_size > 10000:
            continue
        cached_download(dest, url, f"runtime/{name}", 10000, session=session)
    return dest_dir


def font_dir(session=None):
    """Directory of caption faces. The committed DejaVu files win; download is a fallback."""
    bundled = APP_DIR / "fonts"
    if _ready(bundled, FONT_FILES, 1000):
        return bundled
    dest = Path("/tmp/studio-fonts/DejaVuSans-Bold.ttf")
    cached_download(dest, FONT_URL, "runtime/DejaVuSans-Bold.ttf", 1000, session=session)
    return dest.parent


def font_path(session=None):
    """Bold face for drawtext. Regular DejaVu Sans sits beside it for libass."""
    directory = font_dir(session=session)
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        path = directory / name
        if path.is_file() and path.stat().st_size > 1000:
            return str(path)
    return None


def font_file(session=None):
    path = font_path(session=session)
    if not path:
        raise RuntimeError("caption font missing: app/fonts/DejaVuSans-Bold.ttf")
    return path


def _fetch_ffmpeg():
    return os.environ.get("VERCEL") == "1" or os.environ.get("STUDIO_FETCH_FFMPEG") == "1"


def _unpack_ffmpeg(archive):
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
    return dest


def ffmpeg_on_disk(name):
    """ffmpeg or ffprobe that is already executable. Does not download."""
    override = os.environ.get("FFMPEG_BIN") if name == "ffmpeg" else os.environ.get("FFPROBE_BIN")
    candidates = [Path(override)] if override else []
    candidates += [APP_DIR / "bin" / name, Path("/tmp/studio-bin") / name]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which(name)


def ffmpeg_bin(name, session=None):
    """Path to ffmpeg or ffprobe.

    Override, a binary already on disk, or PATH. On Vercel (or when
    STUDIO_FETCH_FFMPEG=1) the static archive is fetched into /tmp first.
    A dry board sets STUDIO_NO_FFMPEG_FETCH so that download never starts.
    """
    found = ffmpeg_on_disk(name)
    if found:
        return found
    archives = [APP_DIR / "bin" / FFMPEG_ARCHIVE, Path("/tmp/studio-bin") / FFMPEG_ARCHIVE]
    archive = next((p for p in archives if p.is_file() and p.stat().st_size >= 1_000_000), None)
    if archive is None and _fetch_ffmpeg() and os.environ.get("STUDIO_NO_FFMPEG_FETCH") != "1":
        archive = cached_download(
            Path("/tmp/studio-bin") / FFMPEG_ARCHIVE, FFMPEG_URL, f"runtime/{FFMPEG_ARCHIVE}", 1_000_000, session=session)
    if archive is not None and archive.is_file():
        unpacked = _unpack_ffmpeg(archive) / name
        if unpacked.is_file():
            return str(unpacked)
    return name
