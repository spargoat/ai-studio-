"""Stage engine: dependency-aware, versioned, rerunnable, spend-guarded."""
import os
import datetime as dt
import json
import random
import shutil
import string
import time
from pathlib import Path

from .config import key_status, load_template
from .db import DB, now
from . import pricing
from .media import duration, poster
from .providers import make, ProviderError, NonRetryable
from .providers.fal import FalPending

READY = "succeeded"


class SpendGuardError(Exception):
    pass


class BriefError(Exception):
    pass


def new_job_id(prefix="j"):
    return f"{prefix}-{dt.datetime.now():%m%d-%H%M%S}-" + "".join(random.choices(string.ascii_lowercase, k=3))


class Runner:
    def __init__(self, cfg, live=False, auto_approve=False, fail_inject=None, provider_override=None, log=print,
                 auto_check=False):
        self.cfg = cfg
        self.live = live
        self.mode = "live" if live else "dry"
        self.auto_approve = auto_approve
        self.auto_check = auto_check   # live: gates pass only if automated blank/corrupt QC passes
        self.fail_inject = fail_inject or {}
        self.provider_override = provider_override or {}
        self.async_fal = False          # advance(): one fal queue poll per request, no blocking wait
        self.db = DB(cfg["_db_file"])
        self.log_fn = log
        if live:
            try:
                self._check_live_flags(auto_approve)
                self.preflight_live()
            except Exception:
                self.db.close(); raise

    def _check_live_flags(self, auto_approve):
        if True:
            if auto_approve:
                raise SpendGuardError("--auto-approve is dry-run only; live jobs stop at review gates")
            if self.fail_inject:
                raise SpendGuardError("--fail-stage is a mock-only test hook")

    # ---------- guards
    def caps(self):
        b = self.cfg.get("budget") or {}
        return b.get("global_cap_usd"), b.get("per_job_cap_usd"), b.get("daily_cap_usd")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def preflight_live(self):
        g, j, d = self.caps()
        if not (g and g > 0 and j and j > 0):
            raise SpendGuardError("live mode refused: set budget.global_cap_usd and budget.per_job_cap_usd "
                                  "(> 0) in config/config.yaml first")
        for stage, sc in self.cfg["stages"].items():
            pcfg = self.cfg["providers"][sc["provider"]]
            if pcfg.get("key_env") and not pcfg.get("optional_key"):
                name, ok = key_status(pcfg)
                if not ok:
                    raise SpendGuardError(f"live mode refused: {stage} needs env var {name} (not set)")
        lock = self.db.fal_lock()
        fal_stages = [k for k, v in self.cfg["stages"].items() if v["provider"] == "fal"]
        routed = all(self.fallback_route(k, self.cfg["stages"][k]) for k in fal_stages) if lock else False
        if lock and routed:
            self.log_fn("fal locked: fal stages will route to the configured fallback provider")
        if lock and not routed and os.environ.get("STUDIO_FAL_TOPPED_UP") != "1":
            raise SpendGuardError(f"live mode refused: fal account was locked at {lock['started_at']} "
                                  f"({lock['job_id']}/{lock['stage']}: exhausted balance). Top up at fal.ai, then "
                                  "rerun with STUDIO_FAL_TOPPED_UP=1 (or price the fallback routes in config)")

    def check_budget(self, job_id, est, live=None):
        if live is None:
            live = self.live
        if not live:
            return
        g, j, d = self.caps()
        spent_job = self.db.live_spend(job_id)
        spent_all = self.db.effective_spend()
        if spent_job + est > j:
            raise SpendGuardError(f"per-job cap ${j:.2f} would be exceeded: spent ${spent_job:.4f} + est ${est:.4f}")
        if spent_all + est > g:
            raise SpendGuardError(f"global cap ${g:.2f} would be exceeded: spent ${spent_all:.4f} + est ${est:.4f}")
        if d:
            today = dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            spent_day = self.db.live_spend(since=today)
            if spent_day + est > d:
                raise SpendGuardError(f"daily cap ${d:.2f} would be exceeded: spent ${spent_day:.4f} + est ${est:.4f}")

    # ---------- helpers
    def log(self, job_id, stage, level, msg):
        self.db.log(job_id, stage, level, msg)
        self.log_fn(f"[{self.mode}] {job_id} {stage or '-'} {level}: {msg}")

    def fallback_route(self, stage, sc, live=None):
        """fal locked + a priced fallback route -> that route, else None. Never used in dry-run."""
        if live is None:
            live = self.live
        fb = self.cfg.get("fallback") or {}
        route = (fb.get("routes") or {}).get(stage)
        if not (live and fb.get("enabled") and route and sc["provider"] == "fal"):
            return None
        if os.environ.get("STUDIO_FAL_TOPPED_UP") == "1" or not self.db.fal_lock():
            return None
        row = (self.cfg.get("prices") or {}).get(route["price_key"]) or {}
        if not row.get("usd_per_unit"):
            return None
        return {**sc, **route}

    def provider_for(self, stage, live=None):
        if live is None:
            live = self.live
        sc = self.cfg["stages"][stage]
        fb = self.fallback_route(stage, sc, live=live)
        if fb:
            sc = fb
        name = self.provider_override.get(stage, sc["provider"])
        p = make(name, self.cfg)
        if not live and p.paid:
            p = make("mock", self.cfg)   # DRY_RUN: every paid adapter is swapped for the mock
        return p, sc

    def job_dir(self, job_id):
        return self.cfg["_data_dir"] / "jobs" / job_id

    def template(self, job):
        return load_template(job["template"])

    def stage_defs(self, job):
        t = self.template(job)
        defs = []
        for s in t["stages"]:
            s = dict(s)
            s.setdefault("needs", [] if s["id"] == "brief" else ["brief"])
            if s["id"] != "brief" and "brief" not in s["needs"]:
                s["needs"] = ["brief", *s["needs"]]
            ov = (job["brief"].get("overrides") or {}).get(s["id"]) or {}
            if "prompt" in ov:
                s["prompt"] = ov["prompt"]
            for k in ("model", "price_key"):
                if k in ov:
                    s[k] = ov[k]
            s["params"] = {**(s.get("params") or {}), **(ov.get("params") or {})}
            defs.append(s)
        return defs

    def is_ready(self, run, sdef):
        if not run or run["status"] != READY:
            return False
        if sdef.get("review") and run["review_status"] != "approved":
            return False
        return True

    def is_stale(self, job_id, run):
        """Upstream was rerun after this output was made."""
        for name, rid in json.loads(run["inputs_json"] or "{}").items():
            cur = self.db.latest(job_id, name)
            if cur and cur["id"] != rid:
                return True
        return False

    # ---------- job lifecycle
    def validate_brief(self, brief):
        pers = self.cfg["_personas"]
        if brief.get("persona") not in pers:
            raise BriefError(f"unknown persona '{brief.get('persona')}', expected one of {list(pers)}")
        script = (brief.get("script") or "").strip()
        kinds = {s["kind"] for s in load_template(brief.get("template", "talking_head"))["stages"]}
        if not script and "voice" in kinds and "script" not in kinds:
            raise BriefError("brief.script is empty")
        if not script:
            script = f"(no script: {brief.get('topic') or brief.get('headline') or 'template generates it'})"
        if len(script) > 2500:
            raise BriefError("brief.script > 2500 chars; split it (Kling max 15 s per call)")
        brief["script"] = script
        brief.setdefault("setting", "a bright, lived-in room")
        brief.setdefault("template", "talking_head")
        return brief

    def create_job(self, brief, batch_id=None):
        brief = self.validate_brief(dict(brief))
        job_id = brief.get("id") or new_job_id()
        if self.db.job(job_id):
            raise BriefError(f"job id {job_id} already exists")
        self.db.create_job(job_id, brief["template"], brief["persona"], brief, self.mode, batch_id)
        self.log(job_id, None, "info", f"created ({brief['persona']}, template {brief['template']})")
        return job_id

    def estimate_job(self, brief):
        """Pure estimate, no calls: one clean pass per stage (no retries)."""
        brief = self.validate_brief(dict(brief))
        fake = {"brief": brief, "template": brief["template"], "id": "estimate"}
        rows = []
        for sdef in self.stage_defs(fake):
            if sdef["kind"] == "brief":
                continue
            ctx = self.build_ctx(fake, sdef, inputs={}, dry_estimate=True)
            sc = self.cfg["stages"][sdef.get("uses", sdef["id"])]
            units = pricing.units_for(sdef["kind"], sdef["params"], ctx)
            est = pricing.cost(self.cfg, sc["price_key"], sdef["kind"], units, sdef["params"])
            row = pricing.price_row(self.cfg, sc["price_key"])
            rows.append({"stage": sdef["id"], "provider": sc["provider"], "model": sc["model"],
                         "units": round(units, 3), "unit": row["unit"], "usd_per_unit": row["usd_per_unit"],
                         "est_usd": est, "source": row.get("source"), "checked": str(row.get("checked"))})
        return rows

    def run_job(self, job_id, only=None):
        """Run every stage that is due. `only` restricts to one stage (rerun-only)."""
        job = self.db.job(job_id)
        defs = self.stage_defs(job)
        self.db.set_job_status(job_id, "running")
        blocked, waiting, failed = [], [], []
        for sdef in defs:
            sid = sdef["id"]
            if only and sid != only:
                continue
            latest = self.db.latest(job_id, sid)
            deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
            if not deps_ok:
                blocked.append(sid)
                if only:
                    self.log(job_id, sid, "warn", "cannot rerun: upstream not ready/approved")
                continue
            due = (only is not None or latest is None or latest["status"] != READY
                   or self.is_stale(job_id, latest))
            if latest and latest["status"] == READY and latest["review_status"] == "rejected" and not only:
                due = False
                waiting.append(sid)
                self.log(job_id, sid, "warn", "rejected: needs a rerun (`rerun --stage`) after reviewing")
                continue
            if due:
                ok = self.run_stage(job, sdef)
                if not ok:
                    failed.append(sid)
                    continue
                latest = self.db.latest(job_id, sid)
            if sdef.get("review") and latest["review_status"] not in ("approved", "rejected"):
                if self.auto_approve:
                    self.review(job_id, sid, "approved", "auto-approved (dry-run test)")
                elif self.auto_check:
                    from .qc import check
                    ok, why = check(sdef["kind"], latest["output_path"])
                    if ok:
                        self.review(job_id, sid, "approved", f"auto-check passed: {why}; Aaron reviews final")
                    else:
                        self.review(job_id, sid, "rejected", f"auto-check FAILED: {why}")
                        waiting.append(sid)
                else:
                    if latest["review_status"] != "pending":
                        self.db.update_run(latest["id"], review_status="pending")
                    waiting.append(sid)
        if failed:
            st = "failed"
        elif waiting:
            st = "awaiting_review"
        elif blocked and not only:
            st = "blocked"
        elif only:
            st = "partial" if not self.is_complete(job_id, defs) else "done"
        else:
            st = "done"
        self.db.set_job_status(job_id, st)
        if st == "done":
            from . import library
            for sid in library.FINAL:
                lr = self.db.latest(job_id, sid)
                if lr and lr["status"] == READY:
                    library.add(self.db, lr, "final output of a done job")
        self.log(job_id, None, "info", f"job status -> {st}"
                 + (f" (waiting: {', '.join(waiting)})" if waiting else "")
                 + (f" (failed: {', '.join(failed)})" if failed else ""))
        return st

    def run_jobs(self, job_ids, parallel=1):
        """Run several jobs; parallel>1 uses threads (DB access is serialised by the DB lock)."""
        if parallel <= 1:
            return [self.run_job(j) for j in job_ids]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            return list(ex.map(self.run_job, job_ids))

    def def_of(self, defs, sid):
        return next(d for d in defs if d["id"] == sid)

    def is_complete(self, job_id, defs):
        return all(self.is_ready(self.db.latest(job_id, d["id"]), d)
                   and not self.is_stale(job_id, self.db.latest(job_id, d["id"])) for d in defs)

    def rerun(self, job_id, stage, cascade=False):
        st = self.run_job(job_id, only=stage)
        if cascade and st != "failed":
            st = self.run_job(job_id)
        return st

    def settle(self, job_id):
        """Set job status from what is already stored. Does not run a stage."""
        job = self.db.job(job_id)
        defs = self.stage_defs(job)
        due_any = waiting = failed = running = False
        for sdef in defs:
            sid = sdef["id"]
            latest = self.db.latest(job_id, sid)
            if latest and latest["status"] == "running":
                running = True
                continue
            deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
            if not deps_ok:
                continue
            if latest and latest["status"] in ("failed", "blocked_budget"):
                failed = True
                continue
            if latest and latest["status"] == READY and latest["review_status"] == "rejected":
                waiting = True
                continue
            if latest is None or latest["status"] != READY or self.is_stale(job_id, latest):
                due_any = True
                continue
            if sdef.get("review") and latest["review_status"] != "approved":
                if latest["review_status"] != "pending":
                    self.db.update_run(latest["id"], review_status="pending")
                waiting = True
        if running:
            st = "running"
        elif failed:
            st = "failed"
        elif due_any:
            st = "partial"
        elif waiting:
            st = "awaiting_review"
        elif self.is_complete(job_id, defs):
            st = "done"
        else:
            st = "blocked"
        self.db.set_job_status(job_id, st)
        if st == "done":
            from . import library
            for sid in library.FINAL:
                lr = self.db.latest(job_id, sid)
                if lr and lr["status"] == READY:
                    library.add(self.db, lr, "final output of a done job")
        return st

    def advance(self, job_id, only=None):
        """Run one due stage, or poll one in-flight fal request. Safe to call again."""
        job = self.db.job(job_id)
        if not job:
            raise BriefError(f"no job {job_id}")
        defs = self.stage_defs(job)
        running = [r for r in self.db.runs(job_id) if r["status"] == "running"]
        if running:
            run = running[-1]
            meta = json.loads(run["meta_json"] or "{}")
            if meta.get("fal"):
                self._poll_fal(job, run)
                fresh = self.db.run(run["id"])
                body = {"action": "poll", "stage": run["stage"], "status": self.settle(job_id)}
                if fresh and fresh["status"] == "failed" and fresh.get("error"):
                    body["ok"] = False
                    body["error"] = fresh["error"]
                return body
            return {"action": "busy", "stage": run["stage"], "status": "running"}
        if not only:
            bad = self._failed_stage(job_id)
            if bad:
                message = bad.get("error") or f"{bad['stage']} {bad['status']}"
                if job["status"] != "failed":
                    self.log(job_id, bad["stage"], "error", f"not advancing: {message}")
                return {"action": "stopped", "stage": bad["stage"], "ok": False, "pending": False,
                        "error": message, "status": self.settle(job_id)}
        self.async_fal = True
        try:
            for sdef in defs:
                sid = sdef["id"]
                if only and sid != only:
                    continue
                latest = self.db.latest(job_id, sid)
                deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
                if not deps_ok:
                    if only:
                        self.log(job_id, sid, "warn", "cannot rerun: upstream not ready/approved")
                    continue
                if latest and latest["status"] == READY and latest["review_status"] == "rejected" and not only:
                    continue
                due = only is not None or latest is None or latest["status"] != READY or self.is_stale(job_id, latest)
                if not due:
                    continue
                self.db.set_job_status(job_id, "running")
                try:
                    result = self.run_stage(job, sdef)
                except (BriefError, SpendGuardError):
                    raise
                except Exception as e:
                    message = str(e)
                    latest = self.db.latest(job_id, sid)
                    if latest and latest["status"] == "running":
                        self.db.update_run(latest["id"], status="failed", error=message, finished_at=now())
                    self.log(job_id, sid, "error", message)
                    return {"action": "stage", "stage": sid, "ok": False, "pending": False,
                            "error": message, "status": self.settle(job_id)}
                body = {"action": "stage", "stage": sid, "ok": result is True or result == "pending",
                        "pending": result == "pending", "status": self.settle(job_id)}
                if result is False:
                    latest = self.db.latest(job_id, sid)
                    if latest and latest.get("error"):
                        body["error"] = latest["error"]
                return body
        finally:
            self.async_fal = False
        return {"action": "idle", "status": self.settle(job_id)}

    def _failed_stage(self, job_id):
        """Latest run of a stage that already failed. Advance does not start another version."""
        job = self.db.job(job_id)
        for sdef in self.stage_defs(job):
            latest = self.db.latest(job_id, sdef["id"])
            if latest and latest["status"] in ("failed", "blocked_budget"):
                return latest
        return None

    def _poll_fal(self, job, run):
        """One status check for a submitted fal request. Downloads the output when it is done."""
        from .providers.fal import FalProvider
        meta = json.loads(run["meta_json"] or "{}")
        falm = meta.get("fal") or {}
        provider, sc = self.provider_for(run["stage"], live=True)
        if not isinstance(provider, FalProvider):
            provider = FalProvider("fal", self.cfg["providers"]["fal"])
        provider.cfg = {**provider.cfg, "max_polls": 1, "poll_seconds": 0}
        try:
            result = provider._wait(falm["request_id"], falm["status_url"], falm["response_url"], timeout=90)
        except FalPending as e:
            falm["status"] = e.status
            meta["fal"] = falm
            self.db.update_run(run["id"], meta=meta)
            self.log(job["id"], run["stage"], "info", f"fal {falm['request_id']} still {e.status}")
            return False
        except Exception as e:
            if falm.get("aid"):
                self.db.update_attempt(falm["aid"], status="failed", error=str(e), finished_at=now())
            self.db.update_run(run["id"], status="failed", error=str(e), finished_at=now())
            self.log(job["id"], run["stage"], "error", f"fal poll failed: {e}")
            return False
        out_dir = self.job_dir(job["id"]) / run["stage"] / f"v{run['version']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            res = provider.save_result(falm["kind"], result, out_dir)
        except Exception as e:
            self.db.update_run(run["id"], status="failed", error=str(e), finished_at=now())
            self.log(job["id"], run["stage"], "error", f"fal download failed: {e}")
            return False
        res["request_id"] = falm["request_id"]
        job_live = True
        actual = pricing.cost(self.cfg, falm.get("price_key") or sc["price_key"], falm["kind"],
                              float(res["units_actual"]), {}) if job_live else 0.0
        if falm.get("aid"):
            self.db.update_attempt(falm["aid"], status="succeeded", actual_cost_usd=actual,
                                   provider_request_id=falm["request_id"], finished_at=now())
        out_meta = res.get("meta") or {}
        self._thumb(res["output_path"], out_dir, out_meta)
        (out_dir / "meta.json").write_text(json.dumps(out_meta, indent=2))
        sdef = next((d for d in self.stage_defs(job) if d["id"] == run["stage"]), {})
        self.db.update_run(run["id"], status=READY, output_path=res["output_path"], meta=out_meta,
                           actual_cost_usd=actual, attempts=falm.get("attempt") or run["attempts"],
                           finished_at=now(), review_status="pending" if sdef.get("review") else None)
        self.score_run(run["id"])
        if not self._publish(run["id"], out_dir):
            return False
        self.log(job["id"], run["stage"], "info", f"v{run['version']} fal {falm['request_id']} completed")
        return True

    def review(self, job_id, stage, verdict, note=""):
        run = self.db.latest(job_id, stage)
        if not run or run["status"] != READY:
            raise BriefError(f"{stage} has no successful output to review")
        self.db.update_run(run["id"], review_status=verdict, review_note=note, reviewed_at=now())
        self.log(job_id, stage, "info", f"review v{run['version']}: {verdict}" + (f" ({note})" if note else ""))
        if verdict == "approved":
            from . import library
            library.add(self.db, self.db.run(run["id"]), "review approved")

    # ---------- one stage
    def build_ctx(self, job, sdef, inputs, dry_estimate=False):
        brief = job["brief"]
        persona = self.cfg["_personas"][brief["persona"]]
        cps = (next((d for d in self.stage_defs(job) if d["kind"] == "voice"), {}).get("params") or {}).get("chars_per_second", 15)
        audio_s = pricing.est_audio_seconds(brief["script"], cps)
        if not dry_estimate:
            from .storage import local
            v = self.db.latest(job["id"], "voice")
            if v and v["status"] == READY and v["output_path"]:
                audio_s = duration(local(v["output_path"])) or audio_s
        vid_param = sdef["params"].get("duration", "auto") if sdef["kind"] == "video" else "auto"
        video_s = pricing.auto_video_seconds(audio_s) if vid_param in (None, "auto") else int(vid_param)
        fmt = {"look": persona.get("look", ""), "setting": brief.get("setting", ""),
               "persona": persona.get("display", brief["persona"]), "script": brief["script"]}
        fmt.update({k: v for k, v in brief.items() if isinstance(v, str) and k not in fmt})
        script_txt = brief["script"]
        from .storage import local
        for d, pth in (inputs or {}).items():
            if pth and str(pth).endswith(".json") and d != "brief":
                try:
                    t = json.loads(Path(local(pth)).read_text())
                except Exception:
                    continue
                fmt[d] = t.get("text", ""); fmt[f"{d}_first"] = (t.get("items") or [""])[0]
                if t.get("script"):
                    script_txt = t["script"]
        if not dry_estimate:
            for sd in self.stage_defs(job):
                if sd["kind"] == "script":
                    lr = self.db.latest(job["id"], sd["id"])
                    if lr and lr["status"] == READY and lr["output_path"]:
                        script_txt = json.loads(Path(local(lr["output_path"])).read_text()).get("script") or script_txt

        class _Keep(dict):
            def __missing__(self, k):
                return "{" + k + "}"
        prompt = (sdef.get("prompt") or "").format_map(_Keep(fmt)) if sdef.get("prompt") else ""
        if sdef["kind"] == "script":
            from .frameworks import prompt as fw_prompt
            prompt = (prompt + " " if prompt else "") + fw_prompt(sdef.get("params") or {}, persona, brief)
        from .persona import face_lock_entries, location, lock_text
        lt = lock_text(persona, sdef["kind"])
        if lt and prompt:
            prompt = f"{prompt} {lt}"
        params = {k: v for k, v in sdef["params"].items() if k not in ("duration", "chars_per_second")} \
            if sdef["kind"] in ("video", "voice") else dict(sdef["params"])
        locked = face_lock_entries(persona.get("refs"))
        ref_sha = {location(e): e.get("sha256") for e in locked if location(e) and e.get("sha256")}
        return {"stage": sdef["id"], "kind": sdef["kind"], "prompt": prompt, "params": params,
                "inputs": inputs, "persona": brief["persona"], "persona_display": persona.get("display"),
                "refs": [location(e) for e in locked if location(e)], "ref_sha256": ref_sha,
                "voice_id": persona.get("voice_id"),
                "voice_settings": persona.get("voice_settings"), "script": script_txt, "brief": brief,
                "audio_seconds": audio_s, "video_seconds": video_s, "fail_inject": self.fail_inject}

    def run_stage(self, job, sdef):
        job_id, sid, kind = job["id"], sdef["id"], sdef["kind"]
        if kind == "brief":
            return self._run_brief(job, sdef)
        from .storage import local
        job_live = self.live or job.get("mode") == "live"
        mode = "live" if job_live else "dry"
        provider, sc = self.provider_for(sdef.get("uses", sid), live=job_live)
        if self.async_fal and provider.name == "fal":
            provider.cfg = {**provider.cfg, "max_polls": 1, "poll_seconds": 0}
        inputs_ids, inputs = {}, {}
        for d in sdef["needs"]:
            r = self.db.latest(job_id, d)
            inputs_ids[d] = r["id"]
            inputs[d] = local(r["output_path"])
        ctx = self.build_ctx(job, sdef, inputs)
        sc = self.apply_lora_route(job, sdef, sc, ctx)
        if sdef.get("model"):
            sc = {**sc, "model": sdef["model"], "price_key": sdef.get("price_key", sc["price_key"])}
        lead = ctx["params"].pop("audio_lead_in_s", None) if kind == "lipsync" else None
        # reuse provider-hosted copies of upstream outputs (avoids re-uploading big videos)
        ctx["input_urls"] = {d: json.loads(self.db.latest(job_id, d)["meta_json"] or "{}").get("url")
                             for d in sdef["needs"]}
        ctx["model"] = sc["model"]
        if kind == "compose":     # exact caption timings when the voice result stored an ElevenLabs alignment
            v = self.db.latest(job_id, "voice")
            ap = v and json.loads(v["meta_json"] or "{}").get("alignment_path")
            if ap:
                ap = local(ap)
            if ap and Path(ap).exists():
                ls_def = next((d for d in self.stage_defs(job) if d["kind"] == "lipsync"), None)
                al_off = float(((ls_def or {}).get("params") or {}).get("audio_lead_in_s") or 0)
                ctx["params"]["_alignment"], ctx["params"]["_alignment_offset"] = ap, al_off
        ctx["live"] = job_live
        if lead:
            ctx["audio_seconds"] = round(ctx["audio_seconds"] + float(lead), 2)
        units = pricing.units_for(kind, ctx["params"] if kind != "video" else sdef["params"], ctx)
        est = pricing.cost(self.cfg, sc["price_key"], kind, units, ctx["params"])
        rid, ver = self.db.new_run(job_id, sid, mode, provider.name if job_live else
                                   f"mock (dry-run of {sc['provider']})" if provider.name == "mock" else provider.name,
                                   sc["model"], sc["price_key"], ctx["prompt"],
                                   {**ctx["params"], **({"duration": ctx["video_seconds"]} if kind == "video" else {})},
                                   inputs_ids, est)
        out_dir = self.job_dir(job_id) / sid / f"v{ver}"
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx["out_dir"] = str(out_dir)
        if lead:   # local, free: pad silence before speech so the mouth model has a run-up
            from .media import ff as _ff
            padded = out_dir / "voice_leadin.wav"
            ms = int(float(lead) * 1000)
            _ff(["-i", inputs["voice"], "-af", f"adelay={ms}|{ms}", "-ar", "44100", str(padded)])
            ctx["inputs"]["voice"] = str(padded); ctx["input_urls"]["voice"] = None
        (out_dir / "request.json").write_text(json.dumps(
            {k: v for k, v in ctx.items() if k not in ("fail_inject",)}, indent=2, default=str))
        self.log(job_id, sid, "info", f"v{ver} est ${est:.4f} ({units:.3g} x {pricing.describe(self.cfg, sc['price_key'])})"
                 + ("" if job_live or not provider.paid and provider.name != "mock" else " [DRY_RUN: mock, $0 charged]"))
        rcfg = self.cfg.get("retries") or {}
        max_try = 1 + int(rcfg.get("max_retries", 2))
        backoff = rcfg.get("backoff_seconds", [2, 8]) if job_live else [rcfg.get("dry_run_backoff_seconds", 0.1)] * 5
        last_err = None
        for attempt in range(1, max_try + 1):
            ctx["attempt"] = attempt
            att_est = est if (job_live and provider.paid) else 0.0
            try:
                self.check_budget(job_id, att_est, live=job_live)
            except SpendGuardError as e:
                self.db.update_run(rid, status="blocked_budget", error=str(e), attempts=attempt - 1, finished_at=now())
                self.log(job_id, sid, "error", f"SPEND GUARD: {e} - no call made")
                return False
            aid = self.db.add_attempt(stage_run_id=rid, job_id=job_id, stage=sid, attempt=attempt, mode=mode,
                                      provider=provider.name, model=sc["model"], status="running",
                                      est_cost_usd=att_est, started_at=now())
            t0 = time.time()
            try:
                res = provider.run(kind, ctx)
                actual = pricing.cost(self.cfg, sc["price_key"], kind, float(res["units_actual"]), ctx["params"]) \
                    if (job_live and provider.paid) else 0.0
                self.db.update_attempt(aid, status="succeeded", actual_cost_usd=actual,
                                       provider_request_id=res.get("request_id"), finished_at=now(),
                                       latency_s=round(time.time() - t0, 2))
                basis = ("computed from output units x price table; reconcile with provider dashboard"
                         if job_live and provider.paid else
                         "DRY_RUN mock: $0 charged" if provider.name == "mock" else "local: $0")
                meta = res.get("meta") or {}
                self._thumb(res["output_path"], out_dir, meta)
                (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
                self.db.update_run(rid, status=READY, output_path=res["output_path"], meta=meta,
                                   actual_cost_usd=actual, cost_basis=basis, attempts=attempt,
                                   error=last_err, finished_at=now(),
                                   review_status="pending" if sdef.get("review") else None)
                self.log(job_id, sid, "info", f"v{ver} ok in {time.time()-t0:.1f}s, actual ${actual:.4f}, attempt {attempt}/{max_try}")
                self.score_run(rid)
                if not self._publish(rid, out_dir):
                    return False
                return True
            except FalPending as e:
                meta = {"fal": {"request_id": e.request_id, "status_url": e.status_url,
                                "response_url": e.response_url, "status": e.status, "kind": kind,
                                "model": sc["model"], "attempt": attempt, "aid": aid,
                                "price_key": sc["price_key"], "version": ver}}
                self.db.update_attempt(aid, status="running", provider_request_id=e.request_id)
                self.db.update_run(rid, status="running", meta=meta, attempts=attempt)
                self.log(job_id, sid, "info", f"v{ver} submitted to fal ({e.request_id}); poll on the next advance")
                return "pending"
            except NonRetryable as e:
                last_err = f"attempt {attempt}: {e}"
                # provider rejected the request (4xx validation/auth): no output, logged as $0; reconcile on dashboard
                self.db.update_attempt(aid, status="failed", error=str(e), actual_cost_usd=0.0, finished_at=now(),
                                       latency_s=round(time.time()-t0, 2))
                self.log(job_id, sid, "error", f"non-retryable: {e}")
                break
            except Exception as e:  # provider failure, network, ffmpeg...
                last_err = f"attempt {attempt}: {type(e).__name__}: {e}"
                self.db.update_attempt(aid, status="failed", error=str(e), finished_at=now(), latency_s=round(time.time()-t0, 2))
                self.log(job_id, sid, "error", last_err + (f"; retrying in {backoff[min(attempt-1, len(backoff)-1)]}s" if attempt < max_try else "; giving up"))
                if attempt < max_try:
                    time.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
        self.db.update_run(rid, status="failed", error=last_err, attempts=attempt, finished_at=now())
        return False

    def score_run(self, run_id, n_frames=None):
        """Local quality gate (identity + visual). Never blocks the pipeline: it flags, review decides."""
        q = self.cfg.get("quality") or {}
        run = self.db.run(run_id)
        if not q.get("enabled", True) or run["stage"] not in q.get("stages", ["still", "video", "lipsync", "finish"]):
            return None
        from . import score
        if not score.scorable(run["output_path"]):
            return None
        persona = self.db.job(run["job_id"])["persona"]
        if not self.live and os.environ.get("STUDIO_MOCK_FAST") == "1":
            n_frames = n_frames or 1               # tests: fewer sampled frames, same code path
        try:
            s = score.score_file(self.cfg, persona, run["output_path"], n_frames or q.get("frames", 5), run["stage"])
        except Exception as e:
            self.log(run["job_id"], run["stage"], "warn", f"quality score failed: {e}")
            s = score.unscored(self.cfg, str(e))
        if s.get("error"):
            s = score.unscored(self.cfg, s["error"])
        self.db.save_score(run, persona, run["output_path"], s)
        meta = json.loads(run["meta_json"] or "{}")
        if len(meta.get("exports") or {}) > 1:      # compose: score every aspect export, keep in run meta
            from . import score as _sc
            meta["export_scores"] = {}
            for asp, pth in meta["exports"].items():
                es = s if pth == run["output_path"] else _sc.score_file(self.cfg, persona, pth, n_frames or q.get("frames", 5), run["stage"])
                if es.get("error"):
                    es = _sc.unscored(self.cfg, es["error"])
                meta["export_scores"][asp] = {k: es.get(k) for k in ("identity", "passed", "flags", "visual")}
            self.db.update_run(run_id, meta=meta)
            bad = [a for a, v in meta["export_scores"].items() if v.get("passed") is False]
            if bad:
                self.log(run["job_id"], run["stage"], "warn", f"compose exports flagged: {', '.join(bad)}")
        if s.get("identity") is None and str(s.get("identity_note") or "").startswith("not scored"):
            idt = "not scored"
        else:
            idt = "n/a" if s["identity"] is None else f"{s['identity']:.3f}"
        if s.get("passed") is True:
            verdict = "PASS"
        elif s.get("passed") is False:
            verdict = "FLAGGED: " + "; ".join(s["flags"])
        else:
            verdict = "not scored"
        self.log(run["job_id"], run["stage"], "warn" if s.get("passed") is False else "info",
                 f"v{run['version']} quality: identity {idt} " + verdict)
        return s

    def apply_lora_route(self, job, sdef, sc, ctx):
        """If the stage asks for use_lora and the persona has an active LoRA, switch the stage to
        its lora_route model and pass the LoRA weights + trigger. Otherwise unchanged."""
        use = ctx["params"].pop("use_lora", False)
        route = sc.get("lora_route")
        if not (use and route):
            return sc
        from .lora import active_lora, lora_weights
        entry = active_lora(job["brief"]["persona"])
        if not entry:
            self.log(job["id"], sdef["id"], "warn", "use_lora set but persona has no active LoRA; using default model")
            return sc
        ctx["loras"] = lora_weights(entry, route.get("scale", 1.0))
        ctx["lora_version"] = entry["version"]
        ctx["prompt"] = f"{entry['trigger']}, " + ctx["prompt"]
        keep = {k: v for k, v in ctx["params"].items() if k in (route.get("pass_params") or [])}
        ctx["params"] = {**(route.get("params") or {}), **keep}
        return {**sc, **{k: route[k] for k in ("model", "price_key", "provider") if k in route}}

    def _thumb(self, path, out_dir, meta):
        try:
            if str(path).endswith(".mp4"):
                poster(path, out_dir / "poster.jpg")
                meta["poster"] = str(out_dir / "poster.jpg")
        except Exception as e:
            meta["poster_error"] = str(e)

    def _run_brief(self, job, sdef):
        rid, ver = self.db.new_run(job["id"], "brief", self.mode, "local", "brief-resolver", "local_ffmpeg",
                                   "", {}, {}, 0.0)
        out_dir = self.job_dir(job["id"]) / "brief" / f"v{ver}"
        out_dir.mkdir(parents=True, exist_ok=True)
        resolved = {"brief": job["brief"], "persona": self.cfg["_personas"][job["brief"]["persona"]],
                    "template": self.template(job).get("_path"),
                    "stages": {d["id"]: {"prompt": self.build_ctx(job, d, {}, dry_estimate=True)["prompt"],
                                         "params": d["params"], "needs": d["needs"], "review": d.get("review", False)}
                               for d in self.stage_defs(job) if d["kind"] != "brief"}}
        p = out_dir / "brief.json"
        p.write_text(json.dumps(resolved, indent=2, default=str))
        self.db.update_run(rid, status=READY, output_path=str(p), actual_cost_usd=0.0, cost_basis="local: $0",
                           attempts=1, finished_at=now())
        self.log(job["id"], "brief", "info", f"v{ver} resolved prompts saved")
        if not self._publish(rid, out_dir):
            return False
        return True

    def _publish(self, run_id, out_dir):
        """Upload the stage directory. A Blob rejection fails this run instead of raising."""
        from .storage import publish_run
        try:
            publish_run(self.db, run_id, out_dir)
        except Exception as e:
            message = str(e)
            self.db.update_run(run_id, status="failed", error=message, finished_at=now())
            run = self.db.run(run_id)
            self.log(run["job_id"] if run else "?", run["stage"] if run else None, "error", message)
            return False
        return True

    def set_override(self, job_id, stage, params=None, model=None, price_key=None, prompt=None):
        job = self.db.job(job_id)
        b = job["brief"]; ov = b.setdefault("overrides", {}).setdefault(stage, {})
        if params:
            ov.setdefault("params", {}).update(params)
        if model:
            ov["model"] = model
        if price_key:
            ov["price_key"] = price_key
        if prompt:
            ov["prompt"] = prompt
        self.db.x("UPDATE jobs SET brief_json=? WHERE id=?", (json.dumps(b), job_id))
        self.log(job_id, stage, "info", f"override set: {json.dumps(ov)}")

    # ---------- templates
    def save_template(self, job_id, name):
        from . import APP_DIR
        import yaml
        job = self.db.job(job_id)
        t = self.template(job)
        t = {k: v for k, v in t.items() if not k.startswith("_")}
        defs = {d["id"]: d for d in self.stage_defs(job)}
        for s in t["stages"]:
            d = defs[s["id"]]
            if d.get("prompt"):
                s["prompt"] = d["prompt"]
            if d.get("params"):
                s["params"] = d["params"]
        t["name"] = name
        t["derived_from"] = {"job": job_id, "template": job["template"], "saved": now()}
        out = APP_DIR / "templates" / f"{name}.yaml"
        out.write_text(yaml.safe_dump(t, sort_keys=False))
        return out
