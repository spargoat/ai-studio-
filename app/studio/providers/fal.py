"""fal.ai queue adapter: submit -> poll -> fetch result -> download (pattern harvested
from amn-production-os GenerationService). Only reached in --live mode."""
import base64
import mimetypes
import time
from pathlib import Path
import requests
from .base import Provider, ProviderError, NonRetryable
from ..config import get_key
from ..media import duration


def _local_file(path, sha256=None):
    """A filesystem path. Blob URLs are cached under /tmp by storage.local."""
    from ..storage import is_url, local
    if is_url(str(path)):
        path = local(path, sha256=sha256)
    path = Path(path)
    if not path.exists():
        return None
    return path


def data_uri(path, sha256=None):
    path = _local_file(path, sha256)
    if path is None:
        raise ProviderError(f"reference file missing: {path}")
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def upload(path, key_headers, sha256=None):
    """Upload a local file or Blob URL to fal storage (free) and return its public URL.
    Needed because some endpoints (lipsync) reject data: URIs. Large images are
    downscaled before the PUT."""
    from ..images import prepare_for_fal
    path = _local_file(path, sha256)
    if path is None:
        raise ProviderError("reference file missing")
    path = Path(prepare_for_fal(path))
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    r = requests.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                      json={"content_type": mime, "file_name": path.name},
                      headers=key_headers, timeout=60)
    if r.status_code >= 400:
        raise ProviderError(f"fal upload initiate {r.status_code}: {r.text[:200]}")
    j = r.json()
    put = requests.put(j["upload_url"], data=path.read_bytes(), headers={"Content-Type": mime}, timeout=300)
    if put.status_code >= 400:
        raise ProviderError(f"fal upload PUT {put.status_code}: {put.text[:200]}")
    return j["file_url"]


class FalPending(Exception):
    """Submitted to the fal queue; the result is not ready yet. Do not resubmit."""

    def __init__(self, request_id, status_url, response_url, status):
        super().__init__(f"fal {request_id} still {status}")
        self.request_id = request_id
        self.status_url = status_url
        self.response_url = response_url
        self.status = status


class FalProvider(Provider):
    paid = True

    def ref(self, ctx, name):
        url = (ctx.get("input_urls") or {}).get(name)
        return url or upload(ctx["inputs"][name], self._headers())

    def _headers(self):
        return {"Authorization": f"Key {get_key(self.cfg)}", "Content-Type": "application/json"}

    def _call(self, model, payload, timeout=None):
        rid, status_url, resp_url = self._submit(model, payload)
        return rid, self._wait(rid, status_url, resp_url, timeout)

    def _submit(self, model, payload):
        base = self.cfg.get("queue_base", "https://queue.fal.run")
        r = requests.post(f"{base}/{model}", json=payload, headers=self._headers(), timeout=120)
        if r.status_code in (401, 403, 422):
            raise NonRetryable(f"fal {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise ProviderError(f"fal submit {r.status_code}: {r.text[:300]}")
        sub = r.json()
        return sub["request_id"], sub["status_url"], sub["response_url"]

    def request_urls(self, model, rid):
        """Status/response URLs for an already-submitted request (resume without resubmitting/paying again)."""
        base = self.cfg.get("queue_base", "https://queue.fal.run")
        app = "/".join(model.split("/")[:2])
        return f"{base}/{app}/requests/{rid}/status", f"{base}/{app}/requests/{rid}"

    def _wait(self, rid, status_url, resp_url, timeout=None):
        deadline = time.time() + (timeout or self.cfg.get("timeout_seconds", 1200))
        polls = 0
        limit = self.cfg.get("max_polls")
        while time.time() < deadline:
            s = requests.get(status_url, headers=self._headers(), timeout=60).json()
            st = s.get("status")
            if st == "COMPLETED":
                break
            if st not in ("IN_QUEUE", "IN_PROGRESS"):
                raise ProviderError(f"fal status {st}: {str(s)[:300]}")
            polls += 1
            if limit and polls >= limit:
                raise FalPending(rid, status_url, resp_url, st)
            time.sleep(self.cfg.get("poll_seconds", 5))
        else:
            raise ProviderError(f"fal timeout waiting for {rid} (still running on fal; resume with this request id, do not resubmit)")
        res = requests.get(resp_url, headers=self._headers(), timeout=120)
        if res.status_code in (400, 401, 403, 422):
            raise NonRetryable(f"fal result {res.status_code} (validation/auth; not retried): {res.text[:300]}")
        if res.status_code >= 400:
            raise ProviderError(f"fal result {res.status_code}: {res.text[:300]}")
        return res.json()

    def _download(self, url, out):
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return str(out)

    def save_result(self, kind, res, out_dir):
        """Write a completed fal result JSON to out_dir. Used when a later request finishes a poll."""
        out_dir = Path(out_dir)
        if kind == "image":
            img = res.get("image") or (res.get("images") or [None])[0]
            if not img:
                raise ProviderError(f"fal returned no image: {str(res)[:300]}")
            out = self._download(img["url"], out_dir / "still.png")
            n = len(res.get("images") or [img])
            return {"output_path": out, "meta": {"url": img["url"], "n": n, "seed": res.get("seed")},
                    "units_actual": n, "request_id": None}
        if kind in ("video", "lipsync"):
            url = (res.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"fal returned no video: {str(res)[:300]}")
            name = "video.mp4" if kind == "video" else "lipsync.mp4"
            out = self._download(url, out_dir / name)
            d = duration(out)
            return {"output_path": out, "meta": {"url": url, "seconds": d},
                    "units_actual": d, "request_id": None}
        raise ProviderError(f"cannot finish fal kind {kind}")

    def do_image(self, ctx):
        p = dict(ctx["params"])
        if ctx.get("loras"):   # LoRA route (e.g. Wan 2.2 T2I): no reference images, trigger in prompt
            payload = {"prompt": ctx["prompt"], "loras": ctx["loras"], **p}
            rid, res = self._call(ctx["model"], payload)
            img = res.get("image") or (res.get("images") or [None])[0]
            if not img:
                raise ProviderError(f"fal returned no image: {str(res)[:300]}")
            out = self._download(img["url"], Path(ctx["out_dir"]) / "still.png")
            return {"output_path": out, "meta": {"url": img["url"], "seed": res.get("seed"), "lora": True},
                    "units_actual": 1, "request_id": rid}
        payload = {"prompt": ctx["prompt"], **p}
        sha = ctx.get("ref_sha256") or {}
        refs = []
        for r in ctx.get("refs") or []:
            if not isinstance(r, str):
                continue
            if _local_file(r, sha.get(r) if sha else None):
                refs.append(r)
        if refs:
            payload["image_urls"] = [upload(r, self._headers(), sha.get(r)) for r in refs]
        rid, res = self._call(ctx["model"], payload)
        imgs = res.get("images") or []
        if not imgs:
            raise ProviderError(f"fal returned no images: {str(res)[:300]}")
        out = self._download(imgs[0]["url"], Path(ctx["out_dir"]) / "still.png")
        return {"output_path": out, "meta": {"url": imgs[0]["url"], "n": len(imgs)},
                "units_actual": len(imgs), "request_id": rid}

    def do_video(self, ctx):
        p = dict(ctx["params"])
        p["duration"] = str(ctx["video_seconds"])
        payload = {"prompt": ctx["prompt"], "start_image_url": self.ref(ctx, "still"), **p}
        rid, res = self._call(ctx["model"], payload)
        url = (res.get("video") or {}).get("url")
        if not url:
            raise ProviderError(f"fal returned no video: {str(res)[:300]}")
        out = self._download(url, Path(ctx["out_dir"]) / "video.mp4")
        return {"output_path": out, "meta": {"url": url, "seconds": duration(out)},
                "units_actual": float(p["duration"]), "request_id": rid}

    def do_lipsync(self, ctx):
        payload = {"video_url": self.ref(ctx, "video"), "audio_url": self.ref(ctx, "voice"), **ctx["params"]}
        rid, res = self._call(ctx["model"], payload)
        url = (res.get("video") or {}).get("url")
        if not url:
            raise ProviderError(f"fal returned no video: {str(res)[:300]}")
        out = self._download(url, Path(ctx["out_dir"]) / "lipsync.mp4")
        d = duration(out)
        return {"output_path": out, "meta": {"url": url, "seconds": d},
                "units_actual": d, "request_id": rid}
