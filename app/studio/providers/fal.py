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


def data_uri(path):
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def upload(path, key_headers):
    """Upload a local file to fal storage (free) and return its public URL.
    Needed because some endpoints (lipsync) reject data: URIs."""
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    r = requests.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                      json={"content_type": mime, "file_name": Path(path).name},
                      headers=key_headers, timeout=60)
    if r.status_code >= 400:
        raise ProviderError(f"fal upload initiate {r.status_code}: {r.text[:200]}")
    j = r.json()
    put = requests.put(j["upload_url"], data=Path(path).read_bytes(), headers={"Content-Type": mime}, timeout=300)
    if put.status_code >= 400:
        raise ProviderError(f"fal upload PUT {put.status_code}: {put.text[:200]}")
    return j["file_url"]


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

    def supports_durable_poll(self):
        return True

    def poll(self, ticket):
        """One status check. No sleep. pending | completed (with payload) | failed."""
        s = requests.get(ticket["status_url"], headers=self._headers(), timeout=60).json()
        st = s.get("status")
        if st in ("IN_QUEUE", "IN_PROGRESS"):
            return {"state": "pending", "provider_status": st}
        if st != "COMPLETED":
            return {"state": "failed", "retry": True, "error": f"fal status {st}: {str(s)[:300]}"}
        res = requests.get(ticket["response_url"], headers=self._headers(), timeout=120)
        if res.status_code in (400, 401, 403, 422):
            return {"state": "failed", "retry": False,
                    "error": f"fal result {res.status_code} (validation/auth; not retried): {res.text[:300]}"}
        if res.status_code >= 400:
            return {"state": "failed", "retry": True, "error": f"fal result {res.status_code}: {res.text[:300]}"}
        return {"state": "completed", "payload": res.json()}

    def _wait(self, rid, status_url, resp_url, timeout=None):
        """Blocking poll used by the local thread/CLI path and by LoRA training."""
        ticket = {"request_id": rid, "status_url": status_url, "response_url": resp_url}
        deadline = time.time() + (timeout or self.cfg.get("timeout_seconds", 1200))
        while time.time() < deadline:
            got = self.poll(ticket)
            if got["state"] == "pending":
                time.sleep(self.cfg.get("poll_seconds", 5))
                continue
            if got["state"] == "failed":
                if got.get("retry", True):
                    raise ProviderError(got["error"])
                raise NonRetryable(got["error"])
            return got["payload"]
        raise ProviderError(f"fal timeout waiting for {rid} (still running on fal; resume with this request id, do not resubmit)")

    def _payload(self, kind, ctx):
        if kind == "image":
            p = dict(ctx["params"])
            if ctx.get("loras"):
                return {"prompt": ctx["prompt"], "loras": ctx["loras"], **p}, "lora"
            payload = {"prompt": ctx["prompt"], **p}
            refs = [r for r in (ctx.get("refs") or []) if Path(r).exists()]
            if refs:
                payload["image_urls"] = [upload(r, self._headers()) for r in refs]
            return payload, "images"
        if kind == "video":
            p = dict(ctx["params"])
            p["duration"] = str(ctx["video_seconds"])
            return {"prompt": ctx["prompt"], "start_image_url": self.ref(ctx, "still"), **p}, "video"
        if kind == "lipsync":
            return {"video_url": self.ref(ctx, "video"), "audio_url": self.ref(ctx, "voice"), **ctx["params"]}, "lipsync"
        raise NonRetryable(f"fal cannot submit stage kind '{kind}'")

    def submit(self, kind, ctx):
        """Queue a fal request and return immediately. The caller polls `poll`."""
        payload, mode = self._payload(kind, ctx)
        rid, status_url, resp_url = self._submit(ctx["model"], payload)
        return {"request_id": rid, "status_url": status_url, "response_url": resp_url,
                "poll_seconds": float(self.cfg.get("poll_seconds", 5)),
                "timeout_seconds": float(self.cfg.get("timeout_seconds", 1200)),
                "kind": kind, "mode": mode}

    def materialize(self, kind, ctx, request_id, payload, mode=None):
        """Download a completed fal payload into the stage directory."""
        mode = mode or ("lora" if kind == "image" and ctx.get("loras") else kind)
        if kind == "image" and mode == "lora":
            img = payload.get("image") or (payload.get("images") or [None])[0]
            if not img:
                raise ProviderError(f"fal returned no image: {str(payload)[:300]}")
            out = self._download(img["url"], Path(ctx["out_dir"]) / "still.png")
            return {"output_path": out, "meta": {"url": img["url"], "seed": payload.get("seed"), "lora": True},
                    "units_actual": 1, "request_id": request_id}
        if kind == "image":
            imgs = payload.get("images") or []
            if not imgs:
                raise ProviderError(f"fal returned no images: {str(payload)[:300]}")
            out = self._download(imgs[0]["url"], Path(ctx["out_dir"]) / "still.png")
            return {"output_path": out, "meta": {"url": imgs[0]["url"], "n": len(imgs)},
                    "units_actual": len(imgs), "request_id": request_id}
        if kind == "video":
            url = (payload.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"fal returned no video: {str(payload)[:300]}")
            out = self._download(url, Path(ctx["out_dir"]) / "video.mp4")
            return {"output_path": out, "meta": {"url": url, "seconds": duration(out)},
                    "units_actual": float(ctx["video_seconds"]), "request_id": request_id}
        if kind == "lipsync":
            url = (payload.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"fal returned no video: {str(payload)[:300]}")
            out = self._download(url, Path(ctx["out_dir"]) / "lipsync.mp4")
            d = duration(out)
            return {"output_path": out, "meta": {"url": url, "seconds": d},
                    "units_actual": d, "request_id": request_id}
        raise NonRetryable(f"fal cannot materialize stage kind '{kind}'")

    def _download(self, url, out):
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return str(out)

    def do_image(self, ctx):
        ticket = self.submit("image", ctx)
        res = self._wait(ticket["request_id"], ticket["status_url"], ticket["response_url"])
        return self.materialize("image", ctx, ticket["request_id"], res, ticket.get("mode"))

    def do_video(self, ctx):
        ticket = self.submit("video", ctx)
        res = self._wait(ticket["request_id"], ticket["status_url"], ticket["response_url"])
        return self.materialize("video", ctx, ticket["request_id"], res, ticket.get("mode"))

    def do_lipsync(self, ctx):
        ticket = self.submit("lipsync", ctx)
        res = self._wait(ticket["request_id"], ticket["status_url"], ticket["response_url"])
        return self.materialize("lipsync", ctx, ticket["request_id"], res, ticket.get("mode"))
