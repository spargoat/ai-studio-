"""Higgsfield adapter (fallback when fal is locked).

Uses the official SDK (higgsfield-client): submit a generation, then poll its status.
``runner.advance()`` sets ``max_polls`` to 1 so a call submits once and raises
``HiggsfieldPending``; the next advance polls that request id and does not resubmit.

Credentials, first match wins:
- ``HIGGSFIELD_API_KEY`` as ``key_id:key_secret``, or ``HIGGSFIELD_API_KEY`` plus ``HIGGSFIELD_API_SECRET``
- the SDK's own ``HF_KEY`` or ``HF_API_KEY`` plus ``HF_API_SECRET``

A missing key raises ``NonRetryable`` and nothing is sent. Tests replace ``transport``
or ``client_factory``; the default transport calls the SDK.
"""
import time
from pathlib import Path

import requests

from .base import Provider, ProviderError, NonRetryable

# Fal stage params that Higgsfield endpoints reject. Everything else is forwarded.
_DROP_PARAMS = {
    "num_images", "output_format", "cfg_scale", "image_size", "num_inference_steps",
    "guidance_scale", "image_format", "loras", "use_lora", "num_frames",
    "frames_per_second", "sync_mode", "audio_lead_in_s",
}

_IMAGE_RES = {"0.5K": "1k", "0.5k": "1k", "1K": "1k", "1k": "1k", "2K": "2k", "2k": "2k",
              "4K": "2k", "4k": "2k", "720p": "720p", "1080p": "1080p"}


class HiggsfieldPending(Exception):
    """Submitted; the result is not ready. Do not resubmit."""

    def __init__(self, request_id, status_url, response_url, status, cancel_url=None):
        super().__init__(f"higgsfield {request_id} still {status}")
        self.request_id = request_id
        self.status_url = status_url
        self.response_url = response_url
        self.cancel_url = cancel_url
        self.status = status


def _no_transport(kind, payload, out_dir):
    raise NonRetryable("higgsfield live transport not configured (no HIGGSFIELD_API_KEY / API client); "
                       "nothing was sent. Use the Higgsfield app/MCP manually or wire a transport.")


def _sdk_transport(kind, payload, out_dir):
    return payload["_provider"]._generate(kind, payload, out_dir)


def _import_sdk():
    try:
        import higgsfield_client as hf
    except ImportError as e:
        raise NonRetryable("higgsfield-client is not installed (pip install higgsfield-client); nothing was sent.") from e
    return hf


def credentials_from_env():
    """SDK key string ``key_id:key_secret``, or raise NonRetryable. Never logs the value."""
    import os
    key = (os.environ.get("HIGGSFIELD_API_KEY") or "").strip()
    secret = (os.environ.get("HIGGSFIELD_API_SECRET") or "").strip()
    hf_key = (os.environ.get("HF_KEY") or "").strip()
    hf_id = (os.environ.get("HF_API_KEY") or "").strip()
    hf_secret = (os.environ.get("HF_API_SECRET") or "").strip()
    if key and ":" in key:
        return key
    if key and secret:
        return f"{key}:{secret}"
    if hf_key:
        return hf_key
    if hf_id and hf_secret:
        return f"{hf_id}:{hf_secret}"
    if key and not secret:
        raise NonRetryable(
            "higgsfield credentials incomplete: HIGGSFIELD_API_KEY is set but the SDK also needs the API secret. "
            "Set HIGGSFIELD_API_SECRET, or set HIGGSFIELD_API_KEY to 'key_id:key_secret', "
            "or set HF_KEY (or HF_API_KEY and HF_API_SECRET). Nothing was sent.")
    raise NonRetryable(
        "higgsfield live transport not configured: set HIGGSFIELD_API_KEY and HIGGSFIELD_API_SECRET "
        "(or HF_KEY='key_id:key_secret'). Nothing was sent.")


class HiggsfieldProvider(Provider):
    paid = True
    transport = staticmethod(_sdk_transport)
    client_factory = None

    def _require_model(self, model):
        text = str(model or "").strip()
        if "/" not in text or "to confirm" in text.lower() or text == "unconfigured":
            raise NonRetryable(
                f"higgsfield model '{text or '(none)'}' is not a catalog endpoint id; nothing was sent. "
                "Set fallback.routes.<stage>.model to an id such as alibaba/qwen-image-3/edit "
                "or alibaba/wan-3.0/image-to-video.")
        return text

    def client(self):
        factory = type(self).client_factory
        if factory is not None:
            return factory(self)
        if getattr(self, "_client", None) is None:
            hf = _import_sdk()
            self._client = hf.SyncClient(
                api_key=credentials_from_env(),
                timeout=float(self.cfg.get("timeout_seconds", 120) or 120),
                base_url=self.cfg.get("base_url") or "https://api.higgsfield.ai")
        return self._client

    def _params(self, payload):
        out = {}
        for k, v in (payload.get("params") or {}).items():
            if k in _DROP_PARAMS or str(k).startswith("_") or v is None:
                continue
            out[k] = v
        return out

    def _upload(self, path):
        return self.client().upload_file(path)

    def _ref_urls(self, payload, limit=3):
        urls = []
        for ref in payload.get("refs") or []:
            if len(urls) >= limit:
                break
            if ref and Path(ref).exists():
                urls.append(self._upload(ref))
        return urls

    def _input_url(self, payload, name):
        url = (payload.get("input_urls") or {}).get(name)
        if url:
            return url
        path = (payload.get("inputs") or {}).get(name)
        if not path or not Path(path).exists():
            raise NonRetryable(f"higgsfield {name} input is missing; nothing was sent.")
        return self._upload(path)

    def _arguments(self, kind, payload):
        args = self._params(payload)
        args["prompt"] = payload.get("prompt") or ""
        if "resolution" in args:
            args["resolution"] = _IMAGE_RES.get(str(args["resolution"]), args["resolution"])
        if kind == "image":
            urls = self._ref_urls(payload)
            model = str(payload.get("model") or "")
            if urls:
                args["image_urls"] = urls
            elif model.rstrip("/").endswith("/edit"):
                raise NonRetryable("higgsfield image edit needs at least one reference image; nothing was sent.")
        elif kind == "video":
            args["image_url"] = self._input_url(payload, "still")
            seconds = payload.get("duration")
            if seconds is None:
                seconds = args.get("duration")
            if seconds not in (None, "auto"):
                args["duration"] = int(seconds)
        elif kind == "lipsync":
            args["video_url"] = self._input_url(payload, "video")
            args["audio_url"] = self._input_url(payload, "voice")
        else:
            raise NonRetryable(f"higgsfield cannot run stage kind '{kind}'")
        return args

    def _submit(self, model, arguments):
        try:
            return self.client().submit(model, arguments)
        except NonRetryable:
            raise
        except Exception as e:
            _reraise(e, "submit")

    def _wait(self, controller, timeout=None):
        """One or more status checks. ``max_polls`` makes advance() return after a single check."""
        hf = _import_sdk()
        names = {hf.Queued: "queued", hf.InProgress: "in_progress", hf.Completed: "completed",
                 hf.Failed: "failed", hf.NSFW: "nsfw", hf.Cancelled: "canceled"}
        deadline = time.time() + float(timeout or self.cfg.get("timeout_seconds", 1200) or 1200)
        limit = self.cfg.get("max_polls")
        delay = float(self.cfg.get("poll_seconds", 2) or 0)
        polls = 0
        while True:
            try:
                status = controller.status()
            except Exception as e:
                _reraise(e, "status")
            label = names.get(type(status), type(status).__name__.lower())
            if isinstance(status, hf.Completed):
                try:
                    return controller.get()
                except Exception as e:
                    _reraise(e, "result")
            if isinstance(status, hf.NSFW):
                raise NonRetryable(f"higgsfield {controller.request_id} flagged nsfw; not charged, not retried")
            if isinstance(status, hf.Cancelled):
                raise NonRetryable(f"higgsfield {controller.request_id} was canceled; nothing to download")
            if isinstance(status, hf.Failed):
                raise ProviderError(f"higgsfield {controller.request_id} failed (not charged)")
            if not isinstance(status, (hf.Queued, hf.InProgress)):
                raise ProviderError(f"higgsfield status {label} for {controller.request_id}")
            polls += 1
            if limit and polls >= int(limit):
                raise HiggsfieldPending(controller.request_id, controller.status_url,
                                        getattr(controller, "response_url", None) or controller.status_url,
                                        label, getattr(controller, "cancel_url", None))
            if time.time() >= deadline:
                raise ProviderError(
                    f"higgsfield timeout waiting for {controller.request_id} "
                    "(still running; resume with this request id, do not resubmit)")
            if delay:
                time.sleep(delay)
                delay = min(delay * 1.5, 10.0)

    def poll(self, request_id):
        """One status check (or a full wait) for a request that was already submitted."""
        credentials_from_env()
        try:
            controller = self.client().get_request_controller(request_id)
        except Exception as e:
            _reraise(e, "status")
        return self._wait(controller)

    def _generate(self, kind, payload, out_dir):
        credentials_from_env()
        model = self._require_model(payload.get("model"))
        arguments = self._arguments(kind, payload)
        controller = self._submit(model, arguments)
        result = self._wait(controller)
        saved = self.save_result(kind, result, out_dir)
        saved["request_id"] = controller.request_id
        return saved

    def _download(self, url, out):
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(out, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        except Exception as e:
            raise ProviderError(f"higgsfield download failed: {e}") from e
        return str(out)

    def save_result(self, kind, res, out_dir):
        """Write a completed result JSON into out_dir. Same shape as fal.save_result."""
        from ..media import duration
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if kind == "image":
            imgs = res.get("images") or ([res["image"]] if res.get("image") else [])
            if not imgs or not (imgs[0] or {}).get("url"):
                raise ProviderError(f"higgsfield returned no image: {str(res)[:300]}")
            out = self._download(imgs[0]["url"], out_dir / "still.png")
            return {"output_path": out,
                    "meta": {"provider": "higgsfield", "url": imgs[0]["url"], "n": len(imgs)},
                    "units_actual": len(imgs), "request_id": res.get("request_id")}
        if kind in ("video", "lipsync"):
            url = (res.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"higgsfield returned no video: {str(res)[:300]}")
            name = "video.mp4" if kind == "video" else "lipsync.mp4"
            out = self._download(url, out_dir / name)
            d = duration(out)
            units = float(d if d else (res.get("duration") or res.get("seconds") or 1))
            return {"output_path": out,
                    "meta": {"provider": "higgsfield", "url": url, "seconds": d if d is not None else units},
                    "units_actual": units, "request_id": res.get("request_id")}
        raise ProviderError(f"cannot finish higgsfield kind {kind}")

    def _run(self, kind, ctx):
        payload = {"model": ctx.get("model"), "prompt": ctx.get("prompt", ""), "params": ctx.get("params") or {},
                   "inputs": ctx.get("inputs") or {}, "input_urls": ctx.get("input_urls") or {},
                   "refs": ctx.get("refs") or [],
                   "duration": ctx.get("video_seconds") if kind == "video" else None,
                   "_provider": self}
        res = type(self).transport(kind, payload, ctx["out_dir"])
        res.setdefault("meta", {})["provider"] = "higgsfield"
        return res

    def do_image(self, ctx):
        return self._run("image", ctx)

    def do_video(self, ctx):
        return self._run("video", ctx)

    def do_lipsync(self, ctx):
        return self._run("lipsync", ctx)


def _reraise(exc, what):
    if isinstance(exc, (ProviderError, HiggsfieldPending)):
        raise exc
    code = None
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, "response", None)
        code = getattr(response, "status_code", None)
        if code:
            break
        cause = getattr(cause, "__cause__", None)
    msg = str(exc)[:300]
    text = f"higgsfield {what} {code}: {msg}" if code else f"higgsfield {what}: {msg}"
    if code in (401, 403, 404, 422):
        raise NonRetryable(text) from exc
    raise ProviderError(text) from exc
