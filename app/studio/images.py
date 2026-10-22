"""Image bytes sent to fal.

The approved outfit and handwriting sheets are 24–27 MB PNGs. Face sheets can
be several megabytes too. fal stores the upload we hand it, so anything past
a long edge of 2048 or 4 MB is written down to a JPEG first. Callers that
never send those roles still go through this, which keeps a large file from
being uploaded if one is passed in.
"""
from pathlib import Path

MAX_EDGE = 2048
MAX_BYTES = 4_000_000


def prepare_for_fal(path):
    """Return a path fal can upload. Small files are returned unchanged."""
    path = Path(path)
    try:
        from PIL import Image
    except ImportError:
        return str(path)
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            heavy = path.stat().st_size > MAX_BYTES or max(w, h) > MAX_EDGE
            if not heavy:
                return str(path)
            scale = min(1.0, MAX_EDGE / max(w, h))
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            dest = path.with_name(path.stem + "-fal.jpg")
            im.save(dest, "JPEG", quality=85, optimize=True)
    except Exception:
        return str(path)
    return str(dest)
