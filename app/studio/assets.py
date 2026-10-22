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
# Minimum sizes are just under the real files (yunet 232 KB, sface 38 MB) so a
# partial download is not treated as a hit and fetched again on the next score.
MODELS = {
    "yunet.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        200_000,
    ),
    "sface.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        30_000_000,
    ),
}
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
FONT_FAMILY = "DejaVu Sans"
FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
FFMPEG_ARCHIVE = "ffmpeg-linux64.tar.xz"


def _download(url, dest, session=None):
    storage.download(url, dest, session=session)


def _ready(directory, mins):
    directory = Path(directory)
    return all((directory / name).is_file() and (directory / name).stat().st_size >= size for name, size in mins.items())


def cached_download(dest, url, pathname, min_size, session=None):
    """Local file, else Blob `pathname`, else `url` (then upload to Blob).

    One path per asset. A short file is deleted and fetched again; a failed
    write does not leave a second copy beside it.
    """
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size >= min_size:
        return dest
    if dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = storage.blob_url(pathname, session=session)
    try:
        _download(blob or url, dest, session=session)
    except OSError:
        dest.unlink(missing_ok=True)
        raise
    if dest.stat().st_size < min_size:
        size = dest.stat().st_size
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"{dest.name} is {size} bytes, expected at least {min_size}")
    if storage.enabled() and not blob:
        try:
            storage.put_path(pathname, dest, mimetypes.guess_type(dest.name)[0], session=session)
        except Exception:
            pass
    return dest


def model_dir(session=None):
    """The one shared directory for yunet.onnx and sface.onnx.

    A file already in that directory is reused. Nothing copies the models into
    a job folder, and a partial download is not kept.
    """
    mins = {name: size for name, (_url, size) in MODELS.items()}
    preferred = APP_DIR / "models"
    if _ready(preferred, mins):
        return preferred
    dest_dir = Path(os.environ.get("STUDIO_MODEL_DIR") or "/tmp/studio-models")
    if _ready(dest_dir, mins):
        return dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, size) in MODELS.items():
        dest = dest_dir / name
        if dest.is_file() and dest.stat().st_size >= size:
            continue
        cached_download(dest, url, f"runtime/{name}", size, session=session)
    return dest_dir


def font_dir(session=None):
    """Directory of caption faces. The committed DejaVu files win; download is a fallback."""
    bundled = APP_DIR / "fonts"
    if _ready(bundled, {name: 1000 for name in FONT_FILES}):
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


def ffmpeg_dir():
    """One directory for the ffmpeg and ffprobe binaries. Not per job."""
    dest = Path(os.environ.get("STUDIO_FFMPEG_DIR") or "/tmp/studio-bin")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _drop_archive(dest=None):
    """The BtbN archive is 143 MB and the two binaries are 172 MB each.

    Keeping the archive beside them fills Vercel's /tmp (512 MB) before the
    face models or the next stage can write. The binaries are the cache.
    """
    dest = Path(dest) if dest else ffmpeg_dir()
    archive = dest / FFMPEG_ARCHIVE
    ready = (dest / "ffmpeg").is_file() and (dest / "ffprobe").is_file()
    if ready and archive.is_file():
        archive.unlink()


def _unpack_ffmpeg(archive):
    dest = ffmpeg_dir()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            base = Path(member.name).name
            if base not in ("ffmpeg", "ffprobe") or not member.isfile():
                continue
            target = dest / base
            if target.exists():
                continue
            src = tar.extractfile(member)
            if src is None:
                continue
            with target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1 << 20)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)
    archive = Path(archive)
    if archive.resolve().parent == dest.resolve():
        _drop_archive(dest)
    return dest


def reclaim_tmp():
    """Free scratch a previous stage left on this instance.

    Drops the ffmpeg archive once both binaries are cached, partial downloads,
    and cached persona sheets larger than a face ref. Outfit and handwriting
    are the files over that line; no stage uses them.
    """
    _drop_archive()
    roots = [ffmpeg_dir(), Path(os.environ.get("STUDIO_MODEL_DIR") or "/tmp/studio-models")]
    for root in roots:
        if not root.is_dir():
            continue
        for part in root.glob("*.part"):
            part.unlink(missing_ok=True)
    cache = storage.cache_dir()
    for path in cache.glob("*"):
        try:
            if path.is_file() and path.stat().st_size > storage.LARGE_CACHE_BYTES:
                path.unlink()
        except OSError:
            pass


def ffmpeg_bin(name, session=None):
    """Path to ffmpeg or ffprobe.

    Override, a binary already on disk, or PATH. On Vercel (or when
    STUDIO_FETCH_FFMPEG=1) the static archive is fetched once into the shared
    bin directory, unpacked, and the archive is deleted.
    """
    override = os.environ.get("FFMPEG_BIN") if name == "ffmpeg" else os.environ.get("FFPROBE_BIN")
    candidates = [Path(override)] if override else []
    candidates += [APP_DIR / "bin" / name, ffmpeg_dir() / name]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            _drop_archive()
            return str(path)
    found = shutil.which(name)
    if found and not _fetch_ffmpeg():
        return found
    archives = [APP_DIR / "bin" / FFMPEG_ARCHIVE, ffmpeg_dir() / FFMPEG_ARCHIVE]
    archive = next((p for p in archives if p.is_file() and p.stat().st_size >= 1_000_000), None)
    if archive is None and _fetch_ffmpeg():
        archive = cached_download(
            ffmpeg_dir() / FFMPEG_ARCHIVE, FFMPEG_URL, f"runtime/{FFMPEG_ARCHIVE}", 1_000_000, session=session)
    if archive is not None and archive.is_file():
        unpacked = _unpack_ffmpeg(archive) / name
        if unpacked.is_file():
            return str(unpacked)
    if found:
        return found
    return name
