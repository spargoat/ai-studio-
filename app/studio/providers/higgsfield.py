"""Higgsfield adapter behind the same Provider interface (fallback when fal is locked).
There is no Higgsfield HTTP API key on the box (Higgsfield is used through its MCP/app), so the live transport is
NOT wired: a live call stops with a clean NonRetryable and nothing is sent. Tests inject a mock transport.
transport(kind, payload, out_dir) -> {"output_path", "units_actual", "request_id", "meta"}"""
from .base import Provider, NonRetryable

KINDS = ("image", "video", "lipsync")


def _no_transport(kind, payload, out_dir):
    raise NonRetryable("higgsfield live transport not configured (no HIGGSFIELD_API_KEY / API client); "
                       "nothing was sent. Use the Higgsfield app/MCP manually or wire a transport.")


class HiggsfieldProvider(Provider):
    paid = True
    transport = staticmethod(_no_transport)

    def _run(self, kind, ctx):
        payload = {"model": ctx["model"], "prompt": ctx.get("prompt", ""), "params": ctx.get("params") or {},
                   "inputs": ctx.get("inputs") or {}, "refs": ctx.get("refs") or [],
                   "duration": ctx.get("video_seconds") if kind == "video" else None}
        res = type(self).transport(kind, payload, ctx["out_dir"])
        res.setdefault("meta", {})["provider"] = "higgsfield"
        return res

    def do_image(self, ctx):
        return self._run("image", ctx)

    def do_video(self, ctx):
        return self._run("video", ctx)

    def do_lipsync(self, ctx):
        return self._run("lipsync", ctx)
