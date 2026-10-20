"""Job files.

Local paths stay local when Blob is not configured (CLI and tests).
On Vercel, a stage pulls its inputs into /tmp, runs, uploads the outputs,
and stores the Blob URL. /file redirects only to those Blob hosts.
"""
import hashlib
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import requests

BLOB_PUT = "https://vercel.com/api/blob/"


def enabled():
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def _token():
    return os.environ.get("BLOB_READ_WRITE_TOKEN") or ""


def cache_dir():
    p = Path(os.environ.get("STUDIO_TMP") or "/tmp/studio-cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_url(path):
    return isinstance(path, str) and path.startswith(("https://", "http://"))


def allowed_redirect(url):
    """True only for Blob URLs this app stored. Rejects open redirects."""
    if not is_url(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    return host == "blob.vercel-storage.com" or host.endswith(".blob.vercel-storage.com")


def local(path, getter=None):
    """Filesystem path for a stored output. Blob URLs are downloaded under /tmp."""
    if not path or not is_url(str(path)):
        return path
    url = str(path)
    suffix = Path(urlparse(url).path).suffix
    dest = cache_dir() / (hashlib.sha256(url.encode()).hexdigest() + suffix)
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    http = getter or requests
    res = http.get(url, timeout=120)
    res.raise_for_status()
    dest.write_bytes(res.content)
    return str(dest)


def _headers():
    return {"authorization": f"Bearer {_token()}", "x-api-version": "7"}


def blob_url(pathname, session=None):
    """Public URL of an existing Blob object with this exact pathname, or None."""
    if not enabled() or not pathname:
        return None
    http = session or requests
    try:
        res = http.get(BLOB_PUT, params={"prefix": pathname, "limit": "20"}, headers=_headers(), timeout=60)
        res.raise_for_status()
        body = res.json()
    except Exception:
        return None
    for blob in body.get("blobs") or []:
        if blob.get("pathname") == pathname and blob.get("url"):
            return blob["url"]
    return None


def download(url, dest, session=None):
    """Write a URL to dest. Used for Blob cache hits and public runtime assets."""
    http = session or requests
    res = http.get(url, timeout=300)
    res.raise_for_status()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(res.content)
    tmp.replace(dest)
    return dest


def put_file(pathname, data, content_type, session=None):
    """Upload bytes to Vercel Blob. Returns the public URL."""
    http = session or requests
    res = http.put(
        BLOB_PUT,
        params={"pathname": pathname},
        data=data,
        headers={
            "authorization": f"Bearer {_token()}",
            "x-api-version": "7",
            "x-content-type": content_type or "application/octet-stream",
            "x-vercel-blob-access": "public",
            "x-add-random-suffix": "0",
        },
        timeout=300,
    )
    res.raise_for_status()
    body = res.json()
    if "url" not in body:
        raise RuntimeError("Blob upload returned no url")
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
        url = put_file(f"{prefix}/{rel}", path.read_bytes(), mimetypes.guess_type(path.name)[0], session=session)
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
