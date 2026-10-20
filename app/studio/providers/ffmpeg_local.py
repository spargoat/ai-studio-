"""Local finish/export. Realism pass adapted from aar58/tools/finish.sh plus a grade tuned toward our
best Higgsfield talking-heads (warmer, more contrast and saturation, crisper detail, light grain).
Writes final.mp4 (master) and final_send.mp4 (<20 MB send copy). Free, always real."""
import os
from pathlib import Path
from .base import Provider
from ..media import ff, duration


class FfmpegProvider(Provider):
    paid = False

    def do_compose(self, ctx):
        from ..compose import compose
        fast = not ctx.get("live") and os.environ.get("STUDIO_MOCK_FAST") == "1"
        src = ctx["inputs"].get("finish") or next(v for k, v in ctx["inputs"].items() if k != "brief")
        params = dict(ctx["params"])
        b = {k: v for k, v in (ctx.get("brief") or {}).items() if isinstance(v, str)}
        params["overlays"] = [dict(o, text=o["text"].format_map(type("K", (dict,), {"__missing__": lambda s, k: ""})(b)))
                              for o in params.get("overlays") or []]
        params["overlays"] = [o for o in params["overlays"] if o["text"].strip()]
        m = compose(src, ctx["out_dir"], params, script=ctx.get("script"), fast=fast)
        first = next(iter(m["exports"].values()))
        m["seconds"] = duration(first)
        return {"output_path": first, "meta": m, "units_actual": 1, "request_id": "local"}

    def do_finish(self, ctx):
        p = ctx["params"]
        w, h, fps = p.get("width", 1080), p.get("height", 1920), p.get("fps", 30)
        # Dry run + STUDIO_MOCK_FAST: inputs are tiny mock clips, so export at mock size. Never applies live.
        if not ctx.get("live") and os.environ.get("STUDIO_MOCK_FAST") == "1":
            from .mock import W, H, FPS
            w, h, fps = W, H, FPS
        out = Path(ctx["out_dir"]) / "final.mp4"
        grade = ""
        if p.get("grade", "hf_warm") == "hf_warm":
            grade = (f"eq=contrast={p.get('contrast', 1.08)}:saturation={p.get('saturation', 1.14)}:gamma={p.get('gamma', 0.97)},"
                     f"colorbalance=rs=0.03:gs=0.0:bs=-0.04:rm=0.03:bm=-0.03,"
                     f"unsharp=5:5:{p.get('sharpen', 0.55)}:5:5:0,")
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,{grade}"
              f"eq=brightness='0.008*sin(2*PI*t*0.7)+0.004*sin(2*PI*t*2.3)':eval=frame,"
              f"noise=c0s={p.get('grain', 5)}:c1s=2:allf=t,format=yuv420p")
        af = f"loudnorm=I={p.get('loudnorm_i', -16)}:TP=-1.5,highpass=f=70"
        if not ctx.get("live") and os.environ.get("STUDIO_MOCK_FAST") == "1":
            af = "highpass=f=70"                      # tests: loudnorm is the slowest filter on tiny mock clips
        ff(["-i", ctx["inputs"]["lipsync"], "-vf", vf, "-af", af, "-c:v", "libx264", "-crf", "19",
            "-preset", p.get("preset", os.environ.get("STUDIO_FFMPEG_PRESET", "medium")), "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", "-map_metadata", "-1", str(out)], timeout=900)
        d = duration(out) or 10
        send = Path(ctx["out_dir"]) / "final_send.mp4"
        kbps = int(min(12000, (p.get("send_mb", 18) * 8192) / d - 160))
        if not ctx.get("live") and os.environ.get("STUDIO_MOCK_FAST") == "1":
            import shutil; shutil.copyfile(out, send)        # tests: skip the second (send-copy) encode
        else:
            ff(["-i", str(out), "-c:v", "libx264", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2*kbps}k",
                "-preset", os.environ.get("STUDIO_FFMPEG_PRESET", "slow"), "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(send)], timeout=900)
        return {"output_path": str(out),
                "meta": {"seconds": d, "size": f"{w}x{h}", "send_copy": str(send),
                         "send_mb": round(send.stat().st_size / 1e6, 1), "grade": p.get("grade", "hf_warm")},
                "units_actual": 1, "request_id": "local"}
