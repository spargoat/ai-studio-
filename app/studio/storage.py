"""Job files.

Local paths stay local when Blob is not configured (CLI and tests).
On Vercel, a stage pulls its inputs into /tmp, runs, uploads the outputs,
and stores the Blob URL. /file redirects only to those Blob hosts.

Uploads follow @vercel/blob 2.8.2: PUT https://vercel.com/api/blob/?pathname=
with x-api-version 13. The public object host (*.blob.vercel-storage.com) is
only for downloads.
"""
import hashlib
import logging
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

# defaultVercelBlobApiUrl + getApiUrl("/") in @vercel/blob. Query pathname is appended by requests.
BLOB_PUT = "https://vercel.com/api/blob/"
BLOB_API_VERSION = "13"


class BlobError(RuntimeError):
    """Blob rejected the call. The message includes the response body."""


def enabled():
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def _token():
    return os.environ.get("BLOB_READ_WRITE_TOKEN") or ""


def _store_id(token):
    """Fourth underscore segment of a read-write token, same as the JS SDK."""
    parts = (token or "").split("_")
    return parts[3] if len(parts) > 3 else ""


def cache_dir():
    p = Path(os.environ.get("STUDIO_TMP") or "/tmp/studio-cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


# Persona sheets above this size are the outfit and handwriting refs. No stage
# downloads them; a warm instance that already did can drop them.
LARGE_CACHE_BYTES = 12_000_000


class NoSpace(OSError):
    """Errno 28 with the free space that was left, so a failed stage log shows it."""

    def __init__(self, path=None):
        free = tmp_free()
        super().__init__(28, f"No space left on device; tmp free {free} bytes", str(path or "/tmp"))


def _budget():
    raw = os.environ.get("STUDIO_TMP_BUDGET")
    if not raw:
        return None
    return int(raw)


def _accounted_dirs():
    """Directories a simulated budget counts. Real runs use disk_usage('/tmp') instead."""
    dirs = [cache_dir()]
    for key in ("STUDIO_MODEL_DIR", "STUDIO_FFMPEG_DIR"):
        value = os.environ.get(key)
        if value:
            dirs.append(Path(value))
    return dirs


def _used_bytes():
    total, seen = 0, set()
    for directory in _accounted_dirs():
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def tmp_free():
    """Bytes still available for scratch. A STUDIO_TMP_BUDGET replaces the real disk."""
    limit = _budget()
    if limit is not None:
        return max(0, limit - _used_bytes())
    path = "/tmp" if Path("/tmp").is_dir() else cache_dir()
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def space_note(message):
    text = str(message)
    if "tmp free " in text:
        return text
    return f"{text}; tmp free {tmp_free()} bytes"


def ensure_space(n, path=None):
    """Refuse a scratch write that would pass a simulated budget. Real disks raise on write."""
    limit = _budget()
    if limit is None or n <= 0:
        return
    if _used_bytes() + n > limit:
        raise NoSpace(path)


def release_local(path):
    """Delete a file this process downloaded into the Blob cache. Leaves other paths alone."""
    if not path:
        return
    target = Path(path)
    try:
        root = cache_dir().resolve()
        resolved = target.resolve()
    except OSError:
        return
    if resolved == root or root not in resolved.parents:
        return
    target.unlink(missing_ok=True)


def _get(http, url, timeout):
    try:
        return http.get(url, timeout=timeout, stream=True)
    except TypeError:
        return http.get(url, timeout=timeout)


def _iter_bytes(res):
    iterator = getattr(res, "iter_content", None)
    if callable(iterator):
        try:
            yield from iterator(1 << 20)
            return
        except TypeError:
            pass
    data = getattr(res, "content", b"") or b""
    if data:
        yield data


def _write_stream(res, dest):
    """Stream a response to dest. A partial file is removed if the write fails."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with tmp.open("wb") as out:
            for chunk in _iter_bytes(res):
                if not chunk:
                    continue
                ensure_space(len(chunk), dest)
                digest.update(chunk)
                out.write(chunk)
        tmp.replace(dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        if getattr(e, "errno", None) == 28 and not isinstance(e, NoSpace):
            raise NoSpace(dest) from e
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def is_url(path):
    return isinstance(path, str) and path.startswith(("https://", "http://"))


def allowed_redirect(url):
    """True only for Blob URLs this app stored. Rejects open redirects."""
    if not is_url(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    return host == "blob.vercel-storage.com" or host.endswith(".blob.vercel-storage.com")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def local(path, getter=None, sha256=None):
    """Filesystem path for a stored output. Blob URLs are downloaded under /tmp.

    The cache key is the content sha256 when the caller knows it, otherwise
    the sha256 of the URL. A cached file whose bytes do not match sha256 is
    discarded and fetched again.
    """
    if not path or not is_url(str(path)):
        return path
    url = str(path)
    suffix = Path(urlparse(url).path).suffix
    key = sha256 or hashlib.sha256(url.encode()).hexdigest()
    dest = cache_dir() / (key + suffix)
    if dest.exists() and dest.stat().st_size > 0:
        if not sha256 or _sha256(dest.read_bytes()) == sha256:
            return str(dest)
        dest.unlink()
    http = getter or requests
    res = _get(http, url, 120)
    res.raise_for_status()
    digest = _write_stream(res, dest)
    if sha256 and digest != sha256:
        dest.unlink(missing_ok=True)
        raise BlobError(f"Blob download sha256 mismatch for {url}")
    return str(dest)


def _headers(extra=None):
    token = _token()
    headers = {
        "authorization": f"Bearer {token}",
        "x-api-version": BLOB_API_VERSION,
        "x-vercel-blob-store-id": _store_id(token),
    }
    if extra:
        headers.update(extra)
    return headers


def _response_text(res):
    text = getattr(res, "text", None)
    if text:
        return text
    content = getattr(res, "content", b"") or b""
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return str(content)


def _check(res, action):
    status = getattr(res, "status_code", 200)
    if status is None:
        status = 200
    if int(status) >= 400:
        message = f"Blob {action} failed ({status}): {_response_text(res)}"
        log.error(message)
        raise BlobError(message)
    try:
        res.raise_for_status()
    except BlobError:
        raise
    except Exception as e:
        message = f"Blob {action} failed: {_response_text(res) or e}"
        log.error(message)
        raise BlobError(message) from e


def list_prefix(prefix, session=None):
    """Blob objects whose pathname starts with prefix. Empty when Blob is off."""
    if not enabled() or not prefix:
        return []
    http = session or requests
    res = http.get(BLOB_PUT, params={"prefix": prefix, "limit": "100"}, headers=_headers(), timeout=60)
    _check(res, "list")
    return (res.json() or {}).get("blobs") or []


def blob_url(pathname, session=None):
    """Public URL of an existing Blob object with this exact pathname, or None."""
    if not enabled() or not pathname:
        return None
    http = session or requests
    try:
        res = http.get(BLOB_PUT, params={"prefix": pathname, "limit": "20"}, headers=_headers(), timeout=60)
        _check(res, "list")
        body = res.json()
    except Exception:
        return None
    for blob in body.get("blobs") or []:
        if blob.get("pathname") == pathname and blob.get("url"):
            return blob["url"]
    return None


def download(url, dest, session=None):
    """Stream a URL to dest. Used for Blob cache hits and public runtime assets."""
    http = session or requests
    res = _get(http, url, 300)
    res.raise_for_status()
    _write_stream(res, dest)
    return Path(dest)


def put_path(pathname, path, content_type, session=None):
    """Upload a file without reading it all into memory first."""
    with open(path, "rb") as handle:
        return put_file(pathname, handle, content_type, session=session)


def put_file(pathname, data, content_type, session=None):
    """Upload bytes to Vercel Blob. Returns the public URL.

    Headers match createPutHeaders + requestApi in @vercel/blob: the stored
    type is x-content-type, overwrite is allowed so a rerun of the same
    pathname replaces the object, and a random suffix is not added.
    """
    http = session or requests
    res = http.put(
        BLOB_PUT,
        params={"pathname": pathname},
        data=data,
        headers=_headers({
            "x-content-type": content_type or "application/octet-stream",
            "x-vercel-blob-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }),
        timeout=300,
    )
    _check(res, "upload")
    body = res.json()
    if "url" not in body:
        message = f"Blob upload returned no url: {_response_text(res)}"
        log.error(message)
        raise BlobError(message)
    return body["url"]


def publish_tree(prefix, directory, session=None):
    """Upload every file in directory. Returns {resolved local path: blob url}."""
    if not enabled():
        return {}
    mapping = {}
    base = Path(directory)
    if not base.exists():
        return mapping
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        url = put_path(f"{prefix}/{rel}", path, mimetypes.guess_type(path.name)[0], session=session)
        mapping[str(path.resolve())] = url
    return mapping


def _map_path(path, mapping):
    if not path or not mapping:
        return path
    try:
        key = str(Path(path).resolve())
    except OSError:
        key = str(path)
    return mapping.get(key, mapping.get(str(path), path))


def publish_run(db, run_id, out_dir, session=None):
    """Upload a finished stage directory and rewrite stored paths to Blob URLs."""
    if not enabled():
        return None
    run = db.run(run_id)
    if not run:
        return None
    prefix = f"jobs/{run['job_id']}/{run['stage']}/v{run['version']}"
    mapping = publish_tree(prefix, out_dir, session=session)
    if not mapping:
        return None
    import json
    meta = json.loads(run["meta_json"] or "{}")
    for key in ("poster", "alignment_path"):
        if meta.get(key):
            meta[key] = _map_path(meta[key], mapping)
    if meta.get("exports"):
        meta["exports"] = {k: _map_path(v, mapping) for k, v in meta["exports"].items()}
    out = _map_path(run["output_path"], mapping)
    db.update_run(run_id, output_path=out, meta=meta)
    if out and out != run["output_path"]:
        db.x("UPDATE scores SET path=? WHERE stage_run_id=?", (out, run_id))
    return out
