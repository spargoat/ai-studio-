"""Persona LoRA support: dataset build + auto-captions, training on fal, versioned registry,
and test generation. Registry: config/loras.yaml (persona -> versions, active version)."""
import hashlib
import json
import zipfile
from pathlib import Path

import yaml

from . import APP_DIR
from .db import now
from .media import ff
from .providers.fal import FalProvider, upload
from .providers.base import ProviderError

REGISTRY = APP_DIR / "config/loras.yaml"


def load_registry():
    return yaml.safe_load(REGISTRY.read_text()) if REGISTRY.exists() else {}


def save_registry(reg):
    REGISTRY.write_text(yaml.safe_dump(reg, sort_keys=False))


def active_lora(persona):
    p = load_registry().get(persona) or {}
    v = p.get("active")
    return next((x for x in p.get("versions", []) if x["version"] == v), None) if v else None


def caption(trigger, tags):
    # rule-based auto-caption: trigger + shot/angle/outfit tags from the dataset manifest.
    # Identity traits are NOT described, so the LoRA (not the caption) carries the face.
    return f"photo of {trigger}, " + ", ".join(tags)


def build_dataset(cfg, persona, out_dir):
    spec = cfg["_personas"][persona].get("lora") or {}
    import glob as _g
    items = []
    for it in spec.get("dataset") or []:
        if "glob" in it:
            items += [{"path": f, "tags": it.get("tags", [])} for f in sorted(_g.glob(it["glob"]))]
        else:
            items.append(it)
    trigger = spec["trigger"]
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    rows, h = [], hashlib.sha256()
    for i, it in enumerate(items, 1):
        src = Path(it["path"])
        if not src.exists():
            rows.append({"src": str(src), "skipped": "missing"}); continue
        dst = out_dir / f"{i:03d}.jpg"
        ff(["-i", str(src), "-vf", "scale='if(gt(iw,ih),-2,min(1536,iw))':'if(gt(iw,ih),min(1536,ih),-2)'",
            "-q:v", "2", str(dst)])
        cap = caption(trigger, it.get("tags", []))
        (out_dir / f"{i:03d}.txt").write_text(cap)
        h.update(dst.read_bytes())
        rows.append({"file": dst.name, "src": str(src), "caption": cap})
    zp = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zp, "w") as z:
        for f in sorted(out_dir.iterdir()):
            z.write(f, f.name)
    (out_dir / "manifest.json").write_text(json.dumps(rows, indent=2))
    return zp, rows, h.hexdigest()[:12]


def train(runner, persona, steps=None, dry=False, resume=None):
    cfg = runner.cfg
    spec = cfg["_personas"][persona]["lora"]
    tcfg = cfg["lora"]["trainer"]
    steps = steps or tcfg.get("steps", 1000)
    reg = load_registry(); pr = reg.setdefault(persona, {"active": None, "versions": []})
    ver = 1 + max([v["version"] for v in pr["versions"]] or [0])
    job_id = f"lora-{persona}-v{ver}"
    work = cfg["_data_dir"] / "loras" / persona / f"v{ver}"
    zp, rows, dh = build_dataset(cfg, persona, work / "dataset")
    row = cfg["prices"][tcfg["price_key"]]
    est = round(row["usd_per_unit"] * max(steps, row.get("min_units", 0)), 4)
    n = sum(1 for r in rows if "file" in r)
    runner.log(job_id, "train", "info", f"dataset {n} images (hash {dh}), {steps} steps, est ${est:.2f}"
               + (" [DRY_RUN: no call]" if dry or not runner.live else ""))
    if dry or not runner.live:
        return {"version": ver, "dataset": str(zp), "images": n, "est_usd": est, "dry": True}
    fal = FalProvider("fal", cfg["providers"]["fal"])
    tmo = tcfg.get("timeout_seconds", 5400)
    if resume:
        # Re-attach to a training request that is already running/finished on fal: no new submit, no new charge.
        row = runner.db.conn.execute("SELECT id FROM attempts WHERE job_id=? AND stage='train' ORDER BY id DESC LIMIT 1",
                                     (job_id,)).fetchone()
        aid = row[0] if row else runner.db.add_attempt(job_id=job_id, stage="train", attempt=1, mode="live",
                                                       provider="fal", model=tcfg["model"], status="running",
                                                       est_cost_usd=est, started_at=now())
        runner.db.update_attempt(aid, status="running", error=None, provider_request_id=resume)
        runner.log(job_id, "train", "info", f"resuming fal request {resume} (no resubmit)")
        try:
            su, ru = fal.request_urls(tcfg["model"], resume)
            rid, res = resume, fal._wait(resume, su, ru, tmo)
        except Exception as e:
            runner.db.update_attempt(aid, status="failed", error=str(e), finished_at=now())
            runner.log(job_id, "train", "error", str(e))
            raise
    else:
        runner.check_budget(job_id, est)
        if not runner.db.job(job_id):
            runner.db.create_job(job_id, "lora_train", persona, {"persona": persona, "steps": steps}, "live")
        aid = runner.db.add_attempt(job_id=job_id, stage="train", attempt=1, mode="live", provider="fal",
                                    model=tcfg["model"], status="running", est_cost_usd=est, started_at=now())
        try:
            url = upload(zp, fal._headers())
            payload = {"training_data_url": url, "trigger_phrase": spec["trigger"], "steps": steps,
                       **(tcfg.get("params") or {})}
            rid, su, ru = fal._submit(tcfg["model"], payload)
            runner.db.update_attempt(aid, provider_request_id=rid)
            runner.log(job_id, "train", "info", f"submitted fal request {rid}")
            res = fal._wait(rid, su, ru, tmo)
        except Exception as e:
            runner.db.update_attempt(aid, status="failed", error=str(e), finished_at=now())
            runner.db.set_job_status(job_id, "failed")
            runner.log(job_id, "train", "error", str(e))
            raise
    runner.db.update_attempt(aid, status="succeeded", actual_cost_usd=est, provider_request_id=rid, finished_at=now())
    runner.db.set_job_status(job_id, "done")
    entry = {"version": ver, "trainer": tcfg["model"], "trigger": spec["trigger"], "steps": steps,
             "high_noise_lora": (res.get("high_noise_lora") or {}).get("url"),
             "low_noise_lora": (res.get("diffusers_lora_file") or {}).get("url"),
             "config_file": (res.get("config_file") or {}).get("url"), "dataset_hash": dh, "images": n,
             "cost_usd": est, "request_id": rid, "created": now(), "status": "trained (unreviewed)"}
    pr["versions"].append(entry)
    pr["active"] = pr["active"] or ver
    save_registry(reg)
    (work / "result.json").write_text(json.dumps(res, indent=2))
    runner.log(job_id, "train", "info", f"trained v{ver}; actual ${est:.2f}; registry updated")
    return entry


def lora_weights(entry, scale=1.0):
    w = []
    if entry.get("high_noise_lora"):
        w.append({"path": entry["high_noise_lora"], "scale": scale, "transformer": "high"})
    if entry.get("low_noise_lora"):
        w.append({"path": entry["low_noise_lora"], "scale": scale, "transformer": "low"})
    return w


TEST_PROMPTS = [
    "selfie-style vertical photo, sitting in a calm home study, soft side window light, bookshelves out of focus, grey crew-neck knit, looking at the lens, phone-camera realism",
    "candid photo walking through a London street in light rain, navy overcoat, natural daylight, 35mm documentary look",
    "close-up portrait at a cafe table by a window, overcast light, olive wax jacket, neutral expression, natural skin texture",
    "seated at a desk with a laptop, evening lamp light, grey knit, three-quarter view, realistic photo",
]


def test_stills(runner, persona, n=4, version=None):
    cfg = runner.cfg
    reg = load_registry().get(persona) or {}
    v = version or reg.get("active")
    entry = next(x for x in reg["versions"] if x["version"] == v)
    route = cfg["stages"]["still"]["lora_route"]
    job_id = f"lora-{persona}-v{v}-test"
    if not runner.db.job(job_id):
        runner.db.create_job(job_id, "lora_test", persona, {"persona": persona, "lora_version": v}, runner.mode)
    fal = FalProvider("fal", cfg["providers"]["fal"])
    out_dir = cfg["_data_dir"] / "loras" / persona / f"v{v}" / "test"
    out_dir.mkdir(parents=True, exist_ok=True)
    price = cfg["prices"][route["price_key"]]["usd_per_unit"]
    paths = []
    for i, pr in enumerate(TEST_PROMPTS[:n], 1):
        runner.check_budget(job_id, price)
        aid = runner.db.add_attempt(job_id=job_id, stage=f"test{i}", attempt=1, mode="live", provider="fal",
                                    model=route["model"], status="running", est_cost_usd=price, started_at=now())
        d = out_dir / f"t{i}"; d.mkdir(exist_ok=True)
        ctx = {"prompt": f"{entry['trigger']}, {pr}", "params": dict(route["params"]), "out_dir": str(d),
               "loras": lora_weights(entry, route.get("scale", 1.0)), "model": route["model"]}
        try:
            res = fal.do_image(ctx)
        except Exception as e:
            runner.db.update_attempt(aid, status="failed", error=str(e), actual_cost_usd=0.0, finished_at=now())
            runner.log(job_id, f"test{i}", "error", str(e)); continue
        runner.db.update_attempt(aid, status="succeeded", actual_cost_usd=price, provider_request_id=res["request_id"],
                                 finished_at=now())
        paths.append(res["output_path"])
        runner.log(job_id, f"test{i}", "info", f"ok ${price:.3f} -> {res['output_path']}")
    runner.db.set_job_status(job_id, "done")
    return paths
