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
from .providers.higgsfield import HiggsfieldPending, HiggsfieldProvider

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
        self.async_poll = False          # advance(): one higgsfield status check per request, no blocking wait
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

    def check_budget(self, job_id, est):
        if not self.live:
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

    def fallback_route(self, stage, sc):
        """fal locked + a priced fallback route -> that route, else None. Never used in dry-run."""
        fb = self.cfg.get("fallback") or {}
        route = (fb.get("routes") or {}).get(stage)
        if not (self.live and fb.get("enabled") and route and sc["provider"] == "fal"):
            return None
        if os.environ.get("STUDIO_FAL_TOPPED_UP") == "1" or not self.db.fal_lock():
            return None
        row = (self.cfg.get("prices") or {}).get(route["price_key"]) or {}
        if not row.get("usd_per_unit"):
            return None
        return {**sc, **route}

    def provider_for(self, stage):
        sc = self.cfg["stages"][stage]
        fb = self.fallback_route(stage, sc)
        if fb:
            sc = fb
        name = self.provider_override.get(stage, sc["provider"])
        p = make(name, self.cfg)
        if not self.live and p.paid:
            p = make("mock", self.cfg)   # DRY_RUN: every paid adapter is swapped for the mock
        return p, sc

    def advance(self, job_id, only=None):
        """Run one due stage, or poll one in-flight Higgsfield request. Safe to call again.

        A paid Higgsfield stage submits once and returns ``pending``. The next call checks that
        request and downloads the output when it is completed. It does not submit a second job.
        """
        job = self.db.job(job_id)
        if not job:
            raise BriefError(f"no job {job_id}")
        running = [r for r in self.db.runs(job_id) if r["status"] == "running"]
        if running:
            run = running[-1]
            meta = json.loads(run["meta_json"] or "{}")
            if meta.get("higgsfield"):
                self._poll_higgsfield(job, run)
                fresh = self.db.run(run["id"])
                pending = fresh["status"] == "running"
                body = {"action": "poll", "stage": run["stage"], "ok": fresh["status"] == READY or pending,
                        "pending": pending, "status": self.db.job(job_id)["status"]}
                if fresh["status"] == "failed" and fresh.get("error"):
                    body["ok"] = False
                    body["error"] = fresh["error"]
                return body
            return {"action": "busy", "stage": run["stage"], "status": "running"}
        self.async_poll = True
        try:
            defs = self.stage_defs(job)
            for sdef in defs:
                sid = sdef["id"]
                if only and sid != only:
                    continue
                latest = self.db.latest(job_id, sid)
                deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
                if not deps_ok:
                    continue
                if latest and latest["status"] == READY and latest["review_status"] == "rejected" and not only:
                    continue
                due = only is not None or latest is None or latest["status"] != READY or self.is_stale(job_id, latest)
                if not due:
                    continue
                self.db.set_job_status(job_id, "running")
                result = self.run_stage(job, sdef)
                body = {"action": "stage", "stage": sid, "ok": result is True or result == "pending",
                        "pending": result == "pending", "status": self.db.job(job_id)["status"]}
                if result is False:
                    latest = self.db.latest(job_id, sid)
                    if latest and latest.get("error"):
                        body["error"] = latest["error"]
                    if self.db.job(job_id)["status"] == "running":
                        self.db.set_job_status(job_id, "failed")
                        body["status"] = "failed"
                return body
        finally:
            self.async_poll = False
        return {"action": "idle", "status": self.db.job(job_id)["status"]}

    def _poll_higgsfield(self, job, run):
        """One status check for a submitted Higgsfield request. Downloads the output when it is done."""
        meta = json.loads(run["meta_json"] or "{}")
        hfm = meta.get("higgsfield") or {}
        provider = HiggsfieldProvider("higgsfield", {**self.cfg["providers"]["higgsfield"],
                                                      "max_polls": 1, "poll_seconds": 0})
        try:
            result = provider.poll(hfm["request_id"])
        except HiggsfieldPending as e:
            hfm["status"] = e.status
            meta["higgsfield"] = hfm
            self.db.update_run(run["id"], meta=meta)
            self.log(job["id"], run["stage"], "info", f"higgsfield {hfm['request_id']} still {e.status}")
            return False
        except Exception as e:
            if hfm.get("aid"):
                self.db.update_attempt(hfm["aid"], status="failed", error=str(e), finished_at=now())
            self.db.update_run(run["id"], status="failed", error=str(e), finished_at=now())
            self.db.set_job_status(job["id"], "failed")
            self.log(job["id"], run["stage"], "error", f"higgsfield poll failed: {e}")
            return False
        out_dir = self.job_dir(job["id"]) / run["stage"] / f"v{run['version']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            res = provider.save_result(hfm["kind"], result, out_dir)
        except Exception as e:
            self.db.update_run(run["id"], status="failed", error=str(e), finished_at=now())
            self.db.set_job_status(job["id"], "failed")
            self.log(job["id"], run["stage"], "error", f"higgsfield download failed: {e}")
            return False
        res["request_id"] = hfm["request_id"]
        actual = pricing.cost(self.cfg, hfm.get("price_key") or run["price_key"], hfm["kind"],
                              float(res["units_actual"]), {})
        if hfm.get("aid"):
            self.db.update_attempt(hfm["aid"], status="succeeded", actual_cost_usd=actual,
                                   provider_request_id=hfm["request_id"], finished_at=now())
        out_meta = res.get("meta") or {}
        self._thumb(res["output_path"], out_dir, out_meta)
        (out_dir / "meta.json").write_text(json.dumps(out_meta, indent=2))
        sdef = next((d for d in self.stage_defs(job) if d["id"] == run["stage"]), {})
        self.db.update_run(run["id"], status=READY, output_path=res["output_path"], meta=out_meta,
                           actual_cost_usd=actual, attempts=hfm.get("attempt") or run["attempts"],
                           finished_at=now(), review_status="pending" if sdef.get("review") else None)
        self.score_run(run["id"])
        self.log(job["id"], run["stage"], "info", f"v{run['version']} higgsfield {hfm['request_id']} completed")
        return True

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
        """Run every stage that is due. `only` restricts to one stage (rerun-only).

        Local/CLI entry point. The Inngest workflow calls the same decide / stage /
        gate / settle steps, one durable step at a time, instead of this loop.
        """
        self.mark_running(job_id)
        job = self.db.job(job_id)
        defs = self.stage_defs(job)
        blocked, waiting, failed = [], [], []
        for sdef in defs:
            sid = sdef["id"]
            decision = self.decide_stage(job_id, sid, only)
            action = decision["action"]
            if action == "skip":
                continue
            if action == "blocked":
                blocked.append(sid)
                continue
            if action == "waiting":
                waiting.append(sid)
                continue
            if action == "run":
                ok = self.run_stage(job, sdef)
                if not ok:
                    failed.append(sid)
                    continue
            gate = self.apply_review_gate(job_id, sid)
            if gate["action"] == "waiting":
                waiting.append(sid)
        return self.settle_job(job_id, only, failed, waiting, blocked)

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
            v = self.db.latest(job["id"], "voice")
            if v and v["status"] == READY and v["output_path"]:
                audio_s = duration(v["output_path"]) or audio_s
        vid_param = sdef["params"].get("duration", "auto") if sdef["kind"] == "video" else "auto"
        video_s = pricing.auto_video_seconds(audio_s) if vid_param in (None, "auto") else int(vid_param)
        fmt = {"look": persona.get("look", ""), "setting": brief.get("setting", ""),
               "persona": persona.get("display", brief["persona"]), "script": brief["script"]}
        fmt.update({k: v for k, v in brief.items() if isinstance(v, str) and k not in fmt})
        script_txt = brief["script"]
        for d, pth in (inputs or {}).items():
            if pth and str(pth).endswith(".json") and d != "brief":
                try:
                    t = json.loads(Path(pth).read_text())
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
                        script_txt = json.loads(Path(lr["output_path"]).read_text()).get("script") or script_txt

        class _Keep(dict):
            def __missing__(self, k):
                return "{" + k + "}"
        prompt = (sdef.get("prompt") or "").format_map(_Keep(fmt)) if sdef.get("prompt") else ""
        if sdef["kind"] == "script":
            from .frameworks import prompt as fw_prompt
            prompt = (prompt + " " if prompt else "") + fw_prompt(sdef.get("params") or {}, persona, brief)
        from .persona import lock_text
        lt = lock_text(persona, sdef["kind"])
        if lt and prompt:
            prompt = f"{prompt} {lt}"
        params = {k: v for k, v in sdef["params"].items() if k not in ("duration", "chars_per_second")} \
            if sdef["kind"] in ("video", "voice") else dict(sdef["params"])
        return {"stage": sdef["id"], "kind": sdef["kind"], "prompt": prompt, "params": params,
                "inputs": inputs, "persona": brief["persona"], "persona_display": persona.get("display"),
                "refs": persona.get("refs") or [], "voice_id": persona.get("voice_id"),
                "voice_settings": persona.get("voice_settings"), "script": script_txt, "brief": brief,
                "audio_seconds": audio_s, "video_seconds": video_s, "fail_inject": self.fail_inject}

    def run_stage(self, job, sdef):
        """Run one stage to completion, including retries. Blocking; used by the CLI and the thread fallback.

        The durable path uses open_stage / budget_step / attempt_stage / poll_stage instead,
        so a serverless timeout cannot sit inside this loop.
        """
        job_id, sid, kind = job["id"], sdef["id"], sdef["kind"]
        if kind == "brief":
            return self._run_brief(job, sdef)
        self.open_stage(job_id, sid, reuse_running=False)
        max_try, _backoff = self.retry_policy()
        for attempt in range(1, max_try + 1):
            budget = self.budget_step(job_id, sid, attempt)
            if budget["state"] == "blocked_budget":
                return False
            outcome = self.attempt_stage(job_id, sid, attempt, durable=False)
            if outcome["state"] == "succeeded":
                return True
            if outcome["state"] == "pending":
                return "pending"
            if not outcome.get("retry"):
                return False
            if attempt < max_try:
                time.sleep(outcome["backoff"])
        return False

    # ---------- durable steps (Inngest calls these; run_job/run_stage call the same ones)
    def stage_ids(self, job_id):
        return [d["id"] for d in self.stage_defs(self.db.job(job_id))]

    def mark_running(self, job_id):
        self.db.set_job_status(job_id, "running")
        return {"status": "running"}

    def retry_policy(self):
        rcfg = self.cfg.get("retries") or {}
        max_try = 1 + int(rcfg.get("max_retries", 2))
        backoff = rcfg.get("backoff_seconds", [2, 8]) if self.live else [rcfg.get("dry_run_backoff_seconds", 0.1)] * 5
        return max_try, [float(x) for x in backoff]

    def _backoff_seconds(self, attempt, backoff):
        return float(backoff[min(attempt - 1, len(backoff) - 1)])

    def decide_stage(self, job_id, stage_id, only=None):
        """Read-only decision for one stage, plus the same warn logs run_job used to emit."""
        job = self.db.job(job_id)
        defs = self.stage_defs(job)
        sdef = self.def_of(defs, stage_id)
        sid = sdef["id"]
        if only and sid != only:
            return {"action": "skip", "id": sid}
        latest = self.db.latest(job_id, sid)
        deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
        if not deps_ok:
            if only:
                self.log(job_id, sid, "warn", "cannot rerun: upstream not ready/approved")
            return {"action": "blocked", "id": sid}
        due = (only is not None or latest is None or latest["status"] != READY or self.is_stale(job_id, latest))
        if latest and latest["status"] == READY and latest["review_status"] == "rejected" and not only:
            self.log(job_id, sid, "warn", "rejected: needs a rerun (`rerun --stage`) after reviewing")
            return {"action": "waiting", "id": sid}
        if due:
            return {"action": "run", "id": sid}
        if sdef.get("review") and latest and latest["review_status"] not in ("approved", "rejected"):
            return {"action": "gate", "id": sid}
        return {"action": "skip", "id": sid}

    def apply_review_gate(self, job_id, stage_id):
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        latest = self.db.latest(job_id, stage_id)
        if not sdef.get("review") or not latest or latest["review_status"] in ("approved", "rejected"):
            return {"action": "ok"}
        if latest["status"] != READY:
            return {"action": "ok"}
        if self.auto_approve:
            self.review(job_id, stage_id, "approved", "auto-approved (dry-run test)")
            return {"action": "ok"}
        if self.auto_check:
            from .qc import check
            ok, why = check(sdef["kind"], latest["output_path"])
            if ok:
                self.review(job_id, stage_id, "approved", f"auto-check passed: {why}; Aaron reviews final")
                return {"action": "ok"}
            self.review(job_id, stage_id, "rejected", f"auto-check FAILED: {why}")
            return {"action": "waiting"}
        if latest["review_status"] != "pending":
            self.db.update_run(latest["id"], review_status="pending")
        return {"action": "waiting"}

    def settle_job(self, job_id, only, failed, waiting, blocked):
        defs = self.stage_defs(self.db.job(job_id))
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

    def _assemble(self, job, sdef, existing=None):
        """Build provider + ctx for a stage. Creates the stage_run row unless `existing` is that row."""
        job_id, sid, kind = job["id"], sdef["id"], sdef["kind"]
        provider, sc = self.provider_for(sdef.get("uses", sid))
        if self.async_poll and provider.name == "higgsfield":
            provider.cfg = {**provider.cfg, "max_polls": 1, "poll_seconds": 0}
        inputs_ids, inputs = {}, {}
        for d in sdef["needs"]:
            r = self.db.latest(job_id, d)
            inputs_ids[d] = r["id"]
            inputs[d] = r["output_path"]
        ctx = self.build_ctx(job, sdef, inputs)
        sc = self.apply_lora_route(job, sdef, sc, ctx)
        if sdef.get("model"):
            sc = {**sc, "model": sdef["model"], "price_key": sdef.get("price_key", sc["price_key"])}
        lead = ctx["params"].pop("audio_lead_in_s", None) if kind == "lipsync" else None
        ctx["input_urls"] = {d: json.loads(self.db.latest(job_id, d)["meta_json"] or "{}").get("url")
                             for d in sdef["needs"]}
        ctx["model"] = sc["model"]
        if kind == "compose":
            v = self.db.latest(job_id, "voice")
            ap = v and json.loads(v["meta_json"] or "{}").get("alignment_path")
            if ap and Path(ap).exists():
                ls_def = next((d for d in self.stage_defs(job) if d["kind"] == "lipsync"), None)
                al_off = float(((ls_def or {}).get("params") or {}).get("audio_lead_in_s") or 0)
                ctx["params"]["_alignment"], ctx["params"]["_alignment_offset"] = ap, al_off
        ctx["live"] = self.live
        if lead:
            ctx["audio_seconds"] = round(ctx["audio_seconds"] + float(lead), 2)
        units = pricing.units_for(kind, ctx["params"] if kind != "video" else sdef["params"], ctx)
        est = pricing.cost(self.cfg, sc["price_key"], kind, units, ctx["params"])
        created = existing is None
        if created:
            label = provider.name if self.live else (
                f"mock (dry-run of {sc['provider']})" if provider.name == "mock" else provider.name)
            rid, ver = self.db.new_run(job_id, sid, self.mode, label, sc["model"], sc["price_key"], ctx["prompt"],
                                       {**ctx["params"], **({"duration": ctx["video_seconds"]} if kind == "video" else {})},
                                       inputs_ids, est)
        else:
            rid, ver = existing["id"], existing["version"]
        out_dir = self.job_dir(job_id) / sid / f"v{ver}"
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx["out_dir"] = str(out_dir)
        if lead:
            from .media import ff as _ff
            padded = out_dir / "voice_leadin.wav"
            ms = int(float(lead) * 1000)
            _ff(["-i", inputs["voice"], "-af", f"adelay={ms}|{ms}", "-ar", "44100", str(padded)])
            ctx["inputs"]["voice"] = str(padded); ctx["input_urls"]["voice"] = None
        (out_dir / "request.json").write_text(json.dumps(
            {k: v for k, v in ctx.items() if k not in ("fail_inject",)}, indent=2, default=str))
        if created:
            self.log(job_id, sid, "info", f"v{ver} est ${est:.4f} ({units:.3g} x {pricing.describe(self.cfg, sc['price_key'])})"
                     + ("" if self.live or not provider.paid and provider.name != "mock" else " [DRY_RUN: mock, $0 charged]"))
        return {"rid": rid, "ver": ver, "ctx": ctx, "provider": provider, "sc": sc, "est": est,
                "units": units, "out_dir": out_dir, "job": job, "sdef": sdef, "inputs": inputs}

    def _open_payload(self, prep):
        max_try, backoff = self.retry_policy()
        provider = prep["provider"]
        return {"state": "open", "run_id": prep["rid"], "version": prep["ver"], "kind": prep["sdef"]["kind"],
                "max_try": max_try, "backoff": backoff,
                "poll": bool(getattr(provider, "supports_durable_poll", lambda: False)())}

    def open_stage(self, job_id, stage_id, reuse_running=True):
        """Create (or resume) the stage_run row. Does not call the provider."""
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        if sdef["kind"] == "brief":
            ok = self._run_brief(job, sdef, reuse_running=reuse_running)
            latest = self.db.latest(job_id, "brief")
            return {"state": "succeeded" if ok else "failed", "kind": "brief", "retry": False,
                    "run_id": latest["id"] if latest else None, "version": latest["version"] if latest else None}
        latest = self.db.latest(job_id, stage_id)
        if reuse_running and latest and latest["status"] == "running":
            prep = self._assemble(job, sdef, existing=latest)
        else:
            prep = self._assemble(job, sdef, existing=None)
        return self._open_payload(prep)

    def _pending_payload(self, ticket):
        poll_seconds = float(ticket.get("poll_seconds") or 5)
        timeout_seconds = float(ticket.get("timeout_seconds") or 1200)
        max_polls = max(1, int(timeout_seconds / max(poll_seconds, 0.001)) + 1)
        return {"state": "pending", "retry": True, "poll_seconds": poll_seconds,
                "timeout_seconds": timeout_seconds, "max_polls": max_polls,
                "request_id": ticket.get("request_id"), "backoff": 0}

    def budget_step(self, job_id, stage_id, attempt):
        """One budget-cap check. On failure the stage is blocked and no provider call is made."""
        run = self.db.latest(job_id, stage_id)
        if run and run["status"] == "blocked_budget":
            return {"state": "blocked_budget", "error": run.get("error"), "retry": False}
        if not self.live:
            return {"state": "ok"}
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        provider, _sc = self.provider_for(sdef.get("uses", stage_id))
        est = float(run["est_cost_usd"] or 0) if run else 0.0
        att_est = est if provider.paid else 0.0
        try:
            self.check_budget(job_id, att_est)
        except SpendGuardError as e:
            if run:
                self.db.update_run(run["id"], status="blocked_budget", error=str(e), attempts=attempt - 1, finished_at=now())
            self.log(job_id, stage_id, "error", f"SPEND GUARD: {e} - no call made")
            return {"state": "blocked_budget", "error": str(e), "retry": False}
        return {"state": "ok"}

    def _attempt_row(self, run_id, attempt):
        rows = [a for a in self.db.attempts(run_id) if a["attempt"] == attempt]
        return rows[-1] if rows else None

    def _fail_message(self, attempt, exc, nonretry=False):
        if nonretry:
            return f"attempt {attempt}: {exc}"
        return f"attempt {attempt}: {type(exc).__name__}: {exc}"

    def _mark_attempt_failed(self, prep, aid, attempt, exc, *, nonretry, t0):
        max_try, backoff = self.retry_policy()
        last_err = self._fail_message(attempt, exc, nonretry=nonretry)
        self.db.update_attempt(aid, status="failed", error=str(exc), actual_cost_usd=0.0 if nonretry else None,
                               finished_at=now(), latency_s=round(time.time() - t0, 2))
        if nonretry:
            self.log(prep["job"]["id"], prep["sdef"]["id"], "error", f"non-retryable: {exc}")
        else:
            delay = self._backoff_seconds(attempt, backoff)
            self.log(prep["job"]["id"], prep["sdef"]["id"], "error", last_err + (
                f"; retrying in {delay}s" if attempt < max_try else "; giving up"))
        retry = (not nonretry) and attempt < max_try
        if not retry:
            self.db.update_run(prep["rid"], status="failed", error=last_err, attempts=attempt, finished_at=now())
        return {"state": "failed", "retry": retry, "error": last_err,
                "backoff": self._backoff_seconds(attempt, backoff)}

    def _previous_error(self, run_id, attempt):
        last = None
        for a in self.db.attempts(run_id):
            if a["attempt"] < attempt and a["status"] == "failed" and a.get("error"):
                last = a["error"]
        return last

    def _record_success(self, prep, aid, res, attempt, t0, last_err):
        job_id, sid = prep["job"]["id"], prep["sdef"]["id"]
        provider, sc, ctx = prep["provider"], prep["sc"], prep["ctx"]
        max_try, _backoff = self.retry_policy()
        kind = prep["sdef"]["kind"]
        actual = pricing.cost(self.cfg, sc["price_key"], kind, float(res["units_actual"]), ctx["params"]) \
            if (self.live and provider.paid) else 0.0
        self.db.update_attempt(aid, status="succeeded", actual_cost_usd=actual,
                               provider_request_id=res.get("request_id"), finished_at=now(),
                               latency_s=round(time.time() - t0, 2))
        basis = ("computed from output units x price table; reconcile with provider dashboard"
                 if self.live and provider.paid else
                 "DRY_RUN mock: $0 charged" if provider.name == "mock" else "local: $0")
        meta = res.get("meta") or {}
        self._thumb(res["output_path"], prep["out_dir"], meta)
        (prep["out_dir"] / "meta.json").write_text(json.dumps(meta, indent=2))
        self.db.update_run(prep["rid"], status=READY, output_path=res["output_path"], meta=meta,
                           actual_cost_usd=actual, cost_basis=basis, attempts=attempt,
                           error=last_err, finished_at=now(),
                           review_status="pending" if prep["sdef"].get("review") else None)
        self.log(job_id, sid, "info",
                 f"v{prep['ver']} ok in {time.time()-t0:.1f}s, actual ${actual:.4f}, attempt {attempt}/{max_try}")
        self.score_run(prep["rid"])
        return {"state": "succeeded", "retry": False, "run_id": prep["rid"]}

    def _terminal_attempt(self, run, prev, attempt):
        """Return a finished outcome if this attempt was already recorded, else None."""
        if prev and prev["status"] == "succeeded":
            return {"state": "succeeded", "retry": False, "run_id": run["id"]}
        if run["status"] == READY:
            return {"state": "succeeded", "retry": False, "run_id": run["id"]}
        if run["status"] == "blocked_budget":
            return {"state": "blocked_budget", "retry": False, "error": run.get("error")}
        if prev and prev["status"] == "failed":
            max_try, backoff = self.retry_policy()
            text = prev.get("error") or ""
            nonretry = text.startswith("non-retryable") or "not retried" in text or "needs key" in text
            # The attempt row stores the provider message. Non-retryable failures are also
            # written onto the stage_run immediately, so a failed run means "do not retry".
            if run["status"] == "failed":
                nonretry = True
            retry = (not nonretry) and attempt < max_try and run["status"] == "running"
            return {"state": "failed", "retry": retry, "error": text,
                    "backoff": self._backoff_seconds(attempt, backoff)}
        if run["status"] == "failed":
            return {"state": "failed", "retry": False, "error": run.get("error")}
        return None

    def attempt_stage(self, job_id, stage_id, attempt, durable=False):
        """One provider attempt. No sleep. With durable=True, fal returns pending instead of polling."""
        run = self.db.latest(job_id, stage_id)
        if not run:
            return {"state": "failed", "retry": False, "error": f"{stage_id} has no open run"}
        prev = self._attempt_row(run["id"], attempt)
        done = self._terminal_attempt(run, prev, attempt)
        if done:
            return done
        if prev and prev["status"] == "running" and prev.get("provider_request_id") and durable:
            meta = json.loads(run["meta_json"] or "{}")
            ticket = meta.get("ticket") or {"request_id": prev["provider_request_id"]}
            out = self._pending_payload(ticket)
            _max, backoff = self.retry_policy()
            out["backoff"] = self._backoff_seconds(attempt, backoff)
            return out
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        prep = self._assemble(job, sdef, existing=run)
        provider, sc, ctx = prep["provider"], prep["sc"], prep["ctx"]
        ctx["attempt"] = attempt
        att_est = prep["est"] if (self.live and provider.paid) else 0.0
        aid = prev["id"] if prev else self.db.add_attempt(
            stage_run_id=prep["rid"], job_id=job_id, stage=stage_id, attempt=attempt, mode=self.mode,
            provider=provider.name, model=sc["model"], status="running", est_cost_usd=att_est, started_at=now())
        t0 = time.time()
        try:
            if durable and getattr(provider, "supports_durable_poll", lambda: False)():
                ticket = provider.submit(sdef["kind"], ctx)
                self.db.update_attempt(aid, provider_request_id=ticket["request_id"])
                meta = json.loads(self.db.run(prep["rid"])["meta_json"] or "{}")
                meta["ticket"] = ticket
                meta["ticket_attempt"] = attempt
                self.db.update_run(prep["rid"], meta=meta)
                out = self._pending_payload(ticket)
                _max, backoff = self.retry_policy()
                out["backoff"] = self._backoff_seconds(attempt, backoff)
                return out
            res = provider.run(sdef["kind"], ctx)
            return self._record_success(prep, aid, res, attempt, t0, self._previous_error(prep["rid"], attempt))
        except HiggsfieldPending as e:
            meta = json.loads(self.db.run(prep["rid"])["meta_json"] or "{}")
            meta["higgsfield"] = {"request_id": e.request_id, "status_url": e.status_url,
                                  "response_url": e.response_url, "cancel_url": e.cancel_url,
                                  "status": e.status, "kind": sdef["kind"], "model": sc["model"],
                                  "attempt": attempt, "aid": aid, "price_key": sc["price_key"],
                                  "version": prep["ver"]}
            self.db.update_attempt(aid, status="running", provider_request_id=e.request_id)
            self.db.update_run(prep["rid"], status="running", meta=meta, attempts=attempt)
            self.log(job_id, stage_id, "info",
                     f"v{prep['ver']} submitted to higgsfield ({e.request_id}); poll on the next advance")
            return {"state": "pending", "retry": False, "request_id": e.request_id}
        except NonRetryable as e:
            return self._mark_attempt_failed(prep, aid, attempt, e, nonretry=True, t0=t0)
        except Exception as e:
            return self._mark_attempt_failed(prep, aid, attempt, e, nonretry=False, t0=t0)

    def _ticket_for(self, provider, run, prev):
        meta = json.loads(run["meta_json"] or "{}")
        ticket = meta.get("ticket") or {}
        if ticket.get("status_url"):
            return ticket
        rid = prev.get("provider_request_id")
        if rid and hasattr(provider, "request_urls"):
            model = prev.get("model") or (json.loads(run["meta_json"] or "{}").get("ticket") or {}).get("model")
            sc_model = model
            if not sc_model:
                return ticket
            status_url, resp_url = provider.request_urls(sc_model, rid)
            return {"request_id": rid, "status_url": status_url, "response_url": resp_url,
                    "poll_seconds": float(getattr(provider, "cfg", {}).get("poll_seconds", 5)),
                    "timeout_seconds": float(getattr(provider, "cfg", {}).get("timeout_seconds", 1200)),
                    "kind": run["stage"], "mode": None}
        return ticket

    def _elapsed(self, started_at):
        try:
            started = dt.datetime.fromisoformat(started_at)
            if started.tzinfo is None:
                started = started.astimezone()
            return (dt.datetime.now().astimezone() - started).total_seconds()
        except Exception:
            return 0

    def poll_stage(self, job_id, stage_id, attempt):
        """One provider status check. No sleep. Completes the attempt when the provider is done."""
        run = self.db.latest(job_id, stage_id)
        if not run:
            return {"state": "failed", "retry": False, "error": f"{stage_id} has no open run"}
        prev = self._attempt_row(run["id"], attempt)
        done = self._terminal_attempt(run, prev, attempt)
        if done:
            return done
        if not prev or not prev.get("provider_request_id"):
            return {"state": "failed", "retry": True, "error": "poll without a provider request id",
                    "backoff": self._backoff_seconds(attempt, self.retry_policy()[1])}
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        prep = self._assemble(job, sdef, existing=run)
        provider, ctx = prep["provider"], prep["ctx"]
        ctx["attempt"] = attempt
        ticket = self._ticket_for(provider, self.db.run(run["id"]), prev)
        timeout_s = float(ticket.get("timeout_seconds") or 1200)
        if prev.get("started_at") and self._elapsed(prev["started_at"]) > timeout_s:
            err = ProviderError(
                f"fal timeout waiting for {prev['provider_request_id']} "
                "(still running on fal; resume with this request id, do not resubmit)")
            return self._mark_attempt_failed(prep, prev["id"], attempt, err, nonretry=False, t0=time.time())
        t0 = time.time()
        try:
            got = provider.poll(ticket)
        except NonRetryable as e:
            return self._mark_attempt_failed(prep, prev["id"], attempt, e, nonretry=True, t0=t0)
        # A dropped connection is an Inngest step retry of this poll, not a new paid submit.
        if got["state"] == "pending":
            out = self._pending_payload(ticket)
            out["backoff"] = self._backoff_seconds(attempt, self.retry_policy()[1])
            return out
        if got["state"] == "failed":
            exc = NonRetryable(got["error"]) if not got.get("retry", True) else ProviderError(got["error"])
            return self._mark_attempt_failed(prep, prev["id"], attempt, exc, nonretry=not got.get("retry", True), t0=t0)
        try:
            res = provider.materialize(sdef["kind"], ctx, ticket.get("request_id") or prev["provider_request_id"],
                                       got["payload"], got.get("mode") or ticket.get("mode"))
        except NonRetryable as e:
            return self._mark_attempt_failed(prep, prev["id"], attempt, e, nonretry=True, t0=t0)
        return self._record_success(prep, prev["id"], res, attempt, t0, self._previous_error(run["id"], attempt))

    def timeout_attempt(self, job_id, stage_id, attempt):
        """Stop polling. The provider request id stays on the attempt so a later run can resume."""
        run = self.db.latest(job_id, stage_id)
        prev = run and self._attempt_row(run["id"], attempt)
        if not run or not prev:
            return {"state": "failed", "retry": False, "error": "nothing to time out"}
        done = self._terminal_attempt(run, prev, attempt)
        if done and done["state"] != "pending":
            return done
        job = self.db.job(job_id)
        sdef = self.def_of(self.stage_defs(job), stage_id)
        prep = self._assemble(job, sdef, existing=run)
        rid = prev.get("provider_request_id") or "?"
        err = ProviderError(
            f"fal timeout waiting for {rid} (still running on fal; resume with this request id, do not resubmit)")
        return self._mark_attempt_failed(prep, prev["id"], attempt, err, nonretry=False, t0=time.time())

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
            self.log(run["job_id"], run["stage"], "warn", f"quality score failed: {e}"); return None
        if "error" in s:
            return None
        self.db.save_score(run, persona, run["output_path"], s)
        meta = json.loads(run["meta_json"] or "{}")
        if len(meta.get("exports") or {}) > 1:      # compose: score every aspect export, keep in run meta
            from . import score as _sc
            meta["export_scores"] = {}
            for asp, pth in meta["exports"].items():
                es = s if pth == run["output_path"] else _sc.score_file(self.cfg, persona, pth, n_frames or q.get("frames", 5), run["stage"])
                meta["export_scores"][asp] = {k: es.get(k) for k in ("identity", "passed", "flags", "visual")}
            self.db.update_run(run_id, meta=meta)
            bad = [a for a, v in meta["export_scores"].items() if not v["passed"]]
            if bad:
                self.log(run["job_id"], run["stage"], "warn", f"compose exports flagged: {', '.join(bad)}")
        idt = "n/a" if s["identity"] is None else f"{s['identity']:.3f}"
        self.log(run["job_id"], run["stage"], "warn" if s["flags"] else "info",
                 f"v{run['version']} quality: identity {idt} " + ("PASS" if s["passed"] else "FLAGGED: " + "; ".join(s["flags"])))
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

    def _run_brief(self, job, sdef, reuse_running=False):
        latest = self.db.latest(job["id"], "brief") if reuse_running else None
        if latest and latest["status"] == READY and reuse_running:
            return True
        if latest and latest["status"] == "running" and reuse_running:
            rid, ver = latest["id"], latest["version"]
        else:
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
