"""Stage engine: dependency-aware, versioned, rerunnable, spend-guarded."""
import os
import datetime as dt
import json
import random
import shutil
import string
import time
import uuid
from pathlib import Path

from .config import key_status, load_template
from .db import DB, now
from . import pricing
from .media import duration, poster
from .providers import make, ProviderError, NonRetryable
from .providers.higgsfield import HiggsfieldPending, HiggsfieldProvider
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
        self.async_poll = False          # advance(): one higgsfield status check per request, no blocking wait
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

    def _still_provider_choice(self, job):
        """brief.still_provider, or None when the job keeps the default still adapter."""
        if not job:
            return None
        choice = (job.get("brief") or {}).get("still_provider")
        if choice in (None, ""):
            return None
        if choice not in ("fal", "higgsfield"):
            raise BriefError("brief.still_provider must be 'higgsfield' or 'fal'")
        return choice

    def _stage_config(self, stage, live, job=None):
        """Stage adapter for this job. A still_provider replaces stages.still for that job only."""
        sc = dict(self.cfg["stages"][stage])
        choice = self._still_provider_choice(job) if stage == "still" else None
        if choice == "higgsfield":
            route = ((self.cfg.get("fallback") or {}).get("routes") or {}).get("still") or {}
            return {**sc, "provider": "higgsfield",
                    "model": route.get("model") or sc["model"],
                    "price_key": route.get("price_key") or "hf_image"}
        if choice == "fal":
            sc["provider"] = "fal"          # explicit fal: do not reroute this job's still on a fal lock
            return sc
        fb = self.fallback_route(stage, sc, live=live)
        return fb or sc

    def provider_for(self, stage, live=None, job=None):
        if live is None:
            live = self.live
        sc = self._stage_config(stage, live, job)
        name = self.provider_override.get(stage, sc["provider"])
        p = make(name, self.cfg)
        if not live and p.paid:
            p = make("mock", self.cfg)   # DRY_RUN: every paid adapter is swapped for the mock
        return p, sc

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
        brief = job.get("brief") or {}
        if brief.get("board_stages"):
            return {"name": brief.get("template") or "board", "stages": brief["board_stages"],
                    "_board": True, "_path": "board"}
        return load_template(job["template"])

    def stage_defs(self, job):
        t = self.template(job)
        board = bool(t.get("_board"))
        defs = []
        for s in t["stages"]:
            s = dict(s)
            if board:
                # Wires already became needs. Don't force every stage to wait on brief:
                # intel nodes run before the brief exists.
                s.setdefault("needs", [])
            else:
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
        kinds |= {s.get("kind") for s in (brief.get("board_stages") or []) if s.get("kind")}
        # A board script block writes the words. The template alone has voice and no script stage.
        if not script and "voice" in kinds and "script" not in kinds:
            raise BriefError("brief.script is empty")
        if not script:
            script = f"(no script: {brief.get('topic') or brief.get('headline') or 'template generates it'})"
        if len(script) > 2500:
            raise BriefError("brief.script > 2500 chars; split it (Kling max 15 s per call)")
        self._still_provider_choice({"brief": brief})
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
            if sdef["kind"] in ("brief", "intel") or sdef.get("disabled"):
                continue
            key = sdef.get("uses", sdef["id"])
            if key not in self.cfg["stages"]:
                continue
            ctx = self.build_ctx(fake, sdef, inputs={}, dry_estimate=True)
            if key == "still" and brief.get("still_provider"):
                _provider, sc = self.provider_for(key, job=fake)
            else:
                sc = self.cfg["stages"][key]
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
        return self._run_job(job_id, only)

    def _run_job(self, job_id, only=None):
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

    def _block_name(self, sdef):
        if not sdef:
            return "A block"
        key = sdef.get("board_node") or sdef.get("kind") or sdef.get("id") or "block"
        labels = {
            "video": "Video", "voice": "Voiceover", "script": "Script", "brief": "Brief",
            "landing": "Landing page", "still": "Still", "image": "Still",
            "funnel_step": "Funnel step", "ad_feed": "Ad Feed", "funnel_route": "Funnel Map",
            "intel": "Intel",
        }
        return labels.get(key, str(key).replace("_", " ").capitalize())

    def stall_reason(self, job_id):
        """Why a dry run stopped with blocks that never started. Named, so the canvas can show it."""
        job = self.db.job(job_id)
        if not job:
            return "Dry run stopped before every block finished"
        defs = self.stage_defs(job)
        by_id = {d["id"]: d for d in defs}
        lines = []
        for sdef in defs:
            if sdef.get("disabled"):
                continue
            latest = self.db.latest(job_id, sdef["id"])
            if latest and latest["status"] == READY and self.is_ready(latest, sdef) and not self.is_stale(job_id, latest):
                continue
            name = self._block_name(sdef)
            if latest and latest["status"] in ("failed", "blocked_budget"):
                text = latest.get("error") or f"{name} failed"
                lines.append(text if name in text else f"{name}: {text}")
                continue
            unmet = []
            for dep in sdef.get("needs") or []:
                ddef = by_id.get(dep)
                if ddef is None:
                    unmet.append(f"{dep} (missing)")
                    continue
                if ddef.get("disabled"):
                    unmet.append(f"{self._block_name(ddef)} (turned off)")
                    continue
                if not self.is_ready(self.db.latest(job_id, dep), ddef):
                    unmet.append(self._block_name(ddef))
            if unmet:
                lines.append(f"{name} is waiting on {', '.join(unmet)}")
            elif latest is None or latest["status"] != READY:
                lines.append(f"{name} did not finish")
        if not lines:
            return "Dry run stopped before every block finished"
        shown = lines[:4]
        text = "Dry run stopped. " + "; ".join(shown)
        extra = len(lines) - len(shown)
        if extra:
            text += f"; and {extra} more"
        return text

    def is_complete(self, job_id, defs):
        active = [d for d in defs if not d.get("disabled")]
        return all(self.is_ready(self.db.latest(job_id, d["id"]), d)
                   and not self.is_stale(job_id, self.db.latest(job_id, d["id"])) for d in active)

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
            if sdef.get("disabled"):
                continue
            latest = self.db.latest(job_id, sid)
            if latest and latest["status"] == "running":
                running = True
                continue
            if latest and latest["status"] in ("failed", "blocked_budget"):
                failed = True
                continue
            deps_ok = all(self.is_ready(self.db.latest(job_id, d), self.def_of(defs, d)) for d in sdef["needs"])
            if not deps_ok:
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

    def _run_timeout_s(self):
        """Past the 300s function limit, so a stage that is still writing is not failed underneath itself."""
        try:
            return max(1, int(os.environ.get("STUDIO_RUN_TIMEOUT_S") or 360))
        except ValueError:
            return 360

    def _lease_ttl_s(self):
        try:
            raw = os.environ.get("STUDIO_LEASE_TTL_S")
            return max(1, int(raw)) if raw else self._run_timeout_s()
        except ValueError:
            return self._run_timeout_s()

    def _run_age_s(self, started_at):
        if not started_at:
            return None
        try:
            started = dt.datetime.fromisoformat(started_at)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.astimezone()
        return (dt.datetime.now().astimezone() - started.astimezone()).total_seconds()

    def _stale_reason(self, run):
        age = self._run_age_s(run.get("started_at"))
        waited = f" after {int(age)}s" if age is not None else ""
        return f"instance was killed or the run timed out{waited} (left running with no result)"

    def _meta(self, raw):
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _run_is_stale(self, run):
        """A fal or Higgsfield poll stays running between requests. Any other row still running past the timeout died with the instance."""
        meta = self._meta(run.get("meta_json"))
        if meta.get("fal") or meta.get("higgsfield"):
            return False
        age = self._run_age_s(run.get("started_at"))
        if age is None:
            return True
        return age >= self._run_timeout_s()

    def _attempt_should_close(self, parent_status, meta, started_at):
        """The spend page lists attempts. stage_run_id does not always find them.

        Compose killed mid-encode left the attempt `running` after the stage_run
        was already failed. Close the ledger row unless a stage run is still in
        progress: a fresh run, or a fal / Higgsfield poll.
        """
        meta = meta or {}
        if parent_status == "running" and (meta.get("fal") or meta.get("higgsfield")):
            return False
        age = self._run_age_s(started_at)
        fresh = age is not None and age < self._run_timeout_s()
        if parent_status == "running" or not parent_status:
            return not fresh
        return True

    def _close_attempts_for_run(self, run, reason, stamp):
        """Close the ledger row for this run even when stage_run_id was not stored."""
        self.db.x(
            """UPDATE attempts SET status='failed', error=?, finished_at=?
               WHERE status='running' AND job_id=? AND stage=?
                 AND (stage_run_id=? OR CAST(stage_run_id AS TEXT)=? OR started_at=?)""",
            (reason, stamp, run["job_id"], run["stage"], run["id"], str(run["id"]), run.get("started_at")))

    def reconcile(self, job_id=None):
        """Fail stage runs left running past the timeout, then attempts the spend page still shows.

        Cron calls this across every job. A killed function cannot record its own failure,
        so the row stays `running` until this path marks it failed. Re-run starts a new version.
        Inside a job scope with nothing running, skip the scan: each remote round trip is paid for.
        """
        scope = self.db._scope
        if scope is not None and (job_id is None or scope.job_id == job_id):
            if not any(row["status"] == "running" for row in scope.rows):
                return {"failed": [], "leases_released": []}
        sql = "SELECT * FROM stage_runs WHERE status='running'"
        args = ()
        if job_id:
            sql += " AND job_id=?"
            args = (job_id,)
        failed = []
        for run in self.db.q(sql, args):
            if not self._run_is_stale(run):
                continue
            reason = self._stale_reason(run)
            stamp = now()
            self.db.update_run(run["id"], status="failed", error=reason, finished_at=stamp)
            self._close_attempts_for_run(run, reason, stamp)
            if run["stage"] == "compose" and self.db.has_table("leases"):
                self.db.x("DELETE FROM leases WHERE name='compose' AND job_id=?", (run["job_id"],))
            self.log(run["job_id"], run["stage"], "error", reason)
            self.settle(run["job_id"])
            failed.append({"id": run["id"], "job_id": run["job_id"], "stage": run["stage"], "error": reason})
        attempt_sql = """SELECT a.id, a.job_id, a.stage, a.started_at, r.status AS run_status, r.meta_json
                         FROM attempts a LEFT JOIN stage_runs r ON r.id = a.stage_run_id
                         WHERE a.status='running'"""
        attempt_args = ()
        if job_id:
            attempt_sql += " AND a.job_id=?"
            attempt_args = (job_id,)
        touched = set()
        for att in self.db.q(attempt_sql, attempt_args):
            meta = self._meta(att.get("meta_json"))
            if not self._attempt_should_close(att.get("run_status"), meta, att.get("started_at")):
                continue
            age = self._run_age_s(att.get("started_at"))
            waited = f" after {int(age)}s" if age is not None else ""
            parent = att.get("run_status")
            if parent and parent != "running":
                reason = f"stage run already {parent}; the attempt was still marked running{waited}"
            else:
                reason = self._stale_reason({"started_at": att.get("started_at")})
            stamp = now()
            self.db.x("UPDATE attempts SET status='failed', error=?, finished_at=? WHERE id=? AND status='running'",
                      (reason, stamp, att["id"]))
            self.log(att["job_id"], att["stage"], "error", reason)
            touched.add(att["job_id"])
        for jid in touched:
            self.settle(jid)
        return {"failed": failed, "leases_released": self.db.release_expired_leases()}

    def advance(self, job_id, only=None):
        """Run one due stage, or poll one in-flight provider request. Safe to call again."""
        with self.db.job_scope(job_id) as scope:
            try:
                body = self._advance_body(job_id, only)
            finally:
                scope.flush()
            if isinstance(body, dict):
                body["stages"] = scope.statuses()
                if body.get("status") == "blocked" and not body.get("error"):
                    body["ok"] = False
                    body["error"] = self.stall_reason(job_id)
            return body

    def _advance_body(self, job_id, only=None):
        job = self.db.job(job_id)
        if not job:
            raise BriefError(f"no job {job_id}")
        self.reconcile(job_id)
        job = self.db.job(job_id)
        defs = self.stage_defs(job)
        running = [r for r in self.db.runs(job_id) if r["status"] == "running"]
        if running:
            run = running[-1]
            meta = json.loads(run["meta_json"] or "{}")
            if meta.get("higgsfield"):
                self._poll_higgsfield(job, run)
                fresh = self.db.run(run["id"])
                pending = fresh["status"] == "running"
                body = {"action": "poll", "stage": run["stage"], "ok": fresh["status"] == READY or pending,
                        "pending": pending, "status": self.settle(job_id)}
                if fresh["status"] == "failed" and fresh.get("error"):
                    body["ok"] = False
                    body["error"] = fresh["error"]
                return body
            if meta.get("fal"):
                self._poll_fal(job, run)
                fresh = self.db.run(run["id"])
                body = {"action": "poll", "stage": run["stage"], "status": self.settle(job_id)}
                if fresh and fresh["status"] == "failed" and fresh.get("error"):
                    body["ok"] = False
                    body["error"] = fresh["error"]
                return body
            # A template job may still be writing on another request. A board row left
            # running means the previous request died, and waiting out the timeout
            # left the canvas on Running with no error.
            if not (job.get("brief") or {}).get("board_stages"):
                return {"action": "busy", "stage": run["stage"], "status": "running"}
            sdef = next((d for d in defs if d["id"] == run["stage"]), None)
            reason = f"{self._block_name(sdef)} was left running with no result"
            stamp = now()
            self.db.update_run(run["id"], status="failed", error=reason, finished_at=stamp)
            self._close_attempts_for_run(run, reason, stamp)
            self.log(job_id, run["stage"], "error", reason)
            return {"action": "stage", "stage": run["stage"], "ok": False, "pending": False,
                    "error": reason, "status": self.settle(job_id)}
        if not only:
            bad = self._failed_stage(job_id)
            if bad:
                message = bad.get("error") or f"{bad['stage']} {bad['status']}"
                if job["status"] != "failed":
                    self.log(job_id, bad["stage"], "error", f"not advancing: {message}")
                return {"action": "stopped", "stage": bad["stage"], "ok": False, "pending": False,
                        "error": message, "status": self.settle(job_id)}
        self.async_fal = True
        self.async_poll = True
        try:
            for sdef in defs:
                sid = sdef["id"]
                if sdef.get("disabled"):
                    continue
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
                lease = None
                if sdef["kind"] == "compose":
                    lease = f"{job_id}:{uuid.uuid4().hex}"
                    if not self.db.acquire_lease("compose", lease, job_id, self._lease_ttl_s()):
                        held = self.db.lease("compose") or {}
                        whose = held.get("job_id")
                        return {"action": "busy", "stage": sid, "status": job["status"],
                                "reason": "compose lease held" + (f" by {whose}" if whose else "")}
                self.db.set_job_status(job_id, "running")
                try:
                    try:
                        result = self.run_stage(job, sdef)
                    except (BriefError, SpendGuardError):
                        raise
                    except Exception as e:
                        from .storage import space_note
                        message = space_note(e)
                        latest = self.db.latest(job_id, sid)
                        if latest and latest["status"] == "running":
                            stamp = now()
                            self.db.update_run(latest["id"], status="failed", error=message, finished_at=stamp)
                            self._close_attempts_for_run(latest, message, stamp)
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
                    if lease:
                        self.db.release_lease("compose", lease)
        finally:
            self.async_fal = False
            self.async_poll = False
        body = {"action": "idle", "status": self.settle(job_id)}
        if body["status"] == "blocked":
            body["ok"] = False
            body["error"] = self.stall_reason(job_id)
        return body

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
        provider, sc = self.provider_for(run["stage"], live=True, job=job)
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
            from .storage import space_note
            message = space_note(e)
            self.db.update_run(run["id"], status="failed", error=message, finished_at=now())
            self.log(job["id"], run["stage"], "error", f"fal poll failed: {message}")
            return False
        from .assets import reclaim_tmp
        reclaim_tmp()
        out_dir = self.job_dir(job["id"]) / run["stage"] / f"v{run['version']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            res = provider.save_result(falm["kind"], result, out_dir)
        except Exception as e:
            from .storage import space_note
            message = space_note(e)
            self.db.update_run(run["id"], status="failed", error=message, finished_at=now())
            self.log(job["id"], run["stage"], "error", f"fal download failed: {message}")
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
        # A fast dry run must not ffprobe the voice file. That probe has no timeout
        # and ran before the video row existed, so a hang left Video on "waiting".
        if not dry_estimate and os.environ.get("STUDIO_MOCK_FAST", "1") == "0":
            from .storage import local
            voice_id = next((d["id"] for d in self.stage_defs(job) if d["kind"] == "voice"), "voice")
            v = self.db.latest(job["id"], voice_id)
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
        from .persona import location, lock_text, refs_for_stage
        lt = lock_text(persona, sdef["kind"])
        if lt and prompt:
            prompt = f"{prompt} {lt}"
        params = {k: v for k, v in sdef["params"].items() if k not in ("duration", "chars_per_second")} \
            if sdef["kind"] in ("video", "voice") else dict(sdef["params"])
        from .board import resolve_voice_id
        locked = refs_for_stage(persona.get("refs"), sdef["kind"])
        ref_sha = {location(e): e.get("sha256") for e in locked if location(e) and e.get("sha256")}
        voice_id = resolve_voice_id(self.cfg.get("_personas") or {}, brief["persona"], brief.get("voice_name"))
        return {"stage": sdef["id"], "kind": sdef["kind"], "prompt": prompt, "params": params,
                "inputs": inputs, "persona": brief["persona"], "persona_display": persona.get("display"),
                "refs": [location(e) for e in locked if location(e)], "ref_sha256": ref_sha,
                "voice_id": voice_id,
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
        if kind == "intel":
            return self._run_intel(job, sdef)
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
        if sdef.get("disabled"):
            return {"action": "blocked", "id": sid}
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
        from .assets import reclaim_tmp
        from .storage import is_url, local
        reclaim_tmp()
        job_live = self.live or job.get("mode") == "live"
        mode = "live" if job_live else "dry"
        provider, sc = self.provider_for(sdef.get("uses", sid), live=job_live, job=job)
        if self.async_poll and provider.name == "higgsfield":
            provider.cfg = {**provider.cfg, "max_polls": 1, "poll_seconds": 0}
        if self.async_fal and provider.name == "fal":
            provider.cfg = {**provider.cfg, "max_polls": 1, "poll_seconds": 0}
        inputs_ids, inputs, downloaded = {}, {}, []
        for d in sdef["needs"]:
            r = self.db.latest(job_id, d)
            inputs_ids[d] = r["id"]
            src = r["output_path"]
            inputs[d] = local(src)
            if is_url(src):
                downloaded.append(inputs[d])
        for key, src_name in (sdef.get("input_alias") or {}).items():
            if src_name in inputs:
                inputs[key] = inputs[src_name]
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
        created = existing is None
        if created:
            label = provider.name if job_live else (
                f"mock (dry-run of {sc['provider']})" if provider.name == "mock" else provider.name)
            rid, ver = self.db.new_run(job_id, sid, mode, label, sc["model"], sc["price_key"], ctx["prompt"],
                                       {**ctx["params"], **({"duration": ctx["video_seconds"]} if kind == "video" else {})},
                                       inputs_ids, est)
        else:
            rid, ver = existing["id"], existing["version"]
        out_dir = self.job_dir(job_id) / sid / f"v{ver}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            from .storage import NoSpace
            if getattr(e, "errno", None) == 28:
                raise NoSpace(out_dir) from e
            raise
        ctx["out_dir"] = str(out_dir)
        if kind == "video" and sdef.get("board_node") and not ctx["inputs"].get("still"):
            # Slice 1 has no Still node. The existing image-to-video adapter still wants a frame.
            ctx["inputs"]["still"] = self._placeholder_still(out_dir)
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
                     + ("" if job_live or not provider.paid and provider.name != "mock" else " [DRY_RUN: mock, $0 charged]"))
        return {"rid": rid, "ver": ver, "ctx": ctx, "provider": provider, "sc": sc, "est": est,
                "units": units, "out_dir": out_dir, "job": job, "sdef": sdef, "inputs": inputs,
                "downloaded": downloaded, "job_live": job_live, "mode": mode}

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
            latest = self.db.latest(job_id, sdef["id"])
            return {"state": "succeeded" if ok else "failed", "kind": "brief", "retry": False,
                    "run_id": latest["id"] if latest else None, "version": latest["version"] if latest else None}
        if sdef["kind"] == "intel":
            ok = self._run_intel(job, sdef)
            latest = self.db.latest(job_id, sdef["id"])
            return {"state": "succeeded" if ok else "failed", "kind": "intel", "retry": False,
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
        provider, _sc = self.provider_for(sdef.get("uses", stage_id), job=job)
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
        from .storage import space_note
        max_try, backoff = self.retry_policy()
        last_err = self._fail_message(attempt, exc, nonretry=nonretry)
        self.db.update_attempt(aid, status="failed", error=str(exc), actual_cost_usd=0.0 if nonretry else None,
                               finished_at=now(), latency_s=round(time.time() - t0, 2))
        if nonretry:
            self.log(prep["job"]["id"], prep["sdef"]["id"], "error", f"non-retryable: {exc}")
        else:
            # Same note the one-stage path puts on a crashed stage: how much /tmp was left.
            last_err = space_note(last_err)
            delay = self._backoff_seconds(attempt, backoff)
            self.log(prep["job"]["id"], prep["sdef"]["id"], "error", last_err + (
                f"; retrying in {delay}s" if attempt < max_try else "; giving up"))
        retry = (not nonretry) and attempt < max_try
        if not retry:
            last_err = space_note(last_err)
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
        usage = ((res.get("meta") or {}).get("usage") or {})
        price_params = ctx["params"]
        price_key = res.get("price_key") or sc["price_key"]
        if "input_tokens" in usage or "output_tokens" in usage:
            price_params = {**ctx["params"], "input_tokens": usage.get("input_tokens") or 0,
                            "output_tokens": usage.get("output_tokens") or 0}
        actual = pricing.cost(self.cfg, price_key, kind, float(res["units_actual"]), price_params) \
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
                           price_key=price_key, error=last_err, finished_at=now(),
                           review_status="pending" if prep["sdef"].get("review") else None)
        self.log(job_id, sid, "info",
                 f"v{prep['ver']} ok in {time.time()-t0:.1f}s, actual ${actual:.4f}, attempt {attempt}/{max_try}")
        self.score_run(prep["rid"])
        if not self._publish(prep["rid"], prep["out_dir"], prep.get("downloaded")):
            err = (self.db.run(prep["rid"]) or {}).get("error") or "publish failed"
            return {"state": "failed", "retry": False, "error": err}
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
        except FalPending as e:
            meta = json.loads(self.db.run(prep["rid"])["meta_json"] or "{}")
            meta["fal"] = {"request_id": e.request_id, "status_url": e.status_url,
                           "response_url": e.response_url, "status": e.status, "kind": sdef["kind"],
                           "model": sc["model"], "attempt": attempt, "aid": aid,
                           "price_key": sc["price_key"], "version": prep["ver"]}
            self.db.update_attempt(aid, status="running", provider_request_id=e.request_id)
            self.db.update_run(prep["rid"], status="running", meta=meta, attempts=attempt)
            self.log(job_id, stage_id, "info",
                     f"v{prep['ver']} submitted to fal ({e.request_id}); poll on the next advance")
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
        if self._meta(run.get("meta_json")).get("placeholder"):
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
        if run["stage"] == "still" and run.get("mode") == "dry":
            s = score.mark_dry_still(s)
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
        note = str(s.get("identity_note") or "")
        if note == score.DRY_RUN_IDENTITY:
            idt = note
        elif s.get("identity") is None and note.startswith("not scored"):
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

    def _placeholder_still(self, out_dir):
        path = Path(out_dir) / "board_still.png"
        from .providers.mock import byte_media, write_placeholder
        if byte_media():
            write_placeholder("png", path)
            return str(path)
        from .media import ff
        ff(["-f", "lavfi", "-i", "color=c=0x1d2b3a:s=270x480", "-frames:v", "1", str(path)])
        return str(path)

    def _load_inputs(self, job, sdef):
        from .storage import local
        blobs = {}
        for dep in sdef.get("needs") or []:
            run = self.db.latest(job["id"], dep)
            if not run or not run.get("output_path"):
                continue
            try:
                blobs[dep] = json.loads(Path(local(run["output_path"])).read_text())
            except Exception:
                continue
        return blobs

    def _run_intel(self, job, sdef):
        from .board import artefact_summary, intel_artefact
        sid = sdef["id"]
        inputs_ids = {}
        for dep in sdef.get("needs") or []:
            run = self.db.latest(job["id"], dep)
            if run:
                inputs_ids[dep] = run["id"]
        rid, ver = self.db.new_run(job["id"], sid, self.mode, "local", "adintel", "local_ffmpeg",
                                   "", sdef.get("params") or {}, inputs_ids, 0.0)
        out_dir = self.job_dir(job["id"]) / sid / f"v{ver}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            artefact = intel_artefact(sdef, self._load_inputs(job, sdef))
        except Exception as e:
            self.db.update_run(rid, status="failed", error=str(e), finished_at=now())
            self.log(job["id"], sid, "error", str(e))
            return False
        path = out_dir / "out.json"
        path.write_text(json.dumps(artefact, indent=2))
        self.db.update_run(rid, status=READY, output_path=str(path), meta={"summary": artefact_summary(artefact)},
                           actual_cost_usd=0.0, cost_basis="local: $0", attempts=1, finished_at=now())
        self.log(job["id"], sid, "info", f"v{ver} {sdef.get('task')} read ({(artefact.get('evidence') or {}).get('origin')})")
        if not self._publish(rid, out_dir):
            return False
        return True

    def _run_brief(self, job, sdef, reuse_running=False):
        from .assets import reclaim_tmp
        reclaim_tmp()
        sid = sdef["id"]
        latest = self.db.latest(job["id"], sid) if reuse_running else None
        if latest and latest["status"] == READY and reuse_running:
            return True
        if latest and latest["status"] == "running" and reuse_running:
            rid, ver = latest["id"], latest["version"]
        else:
            rid, ver = self.db.new_run(job["id"], sid, self.mode, "local", "brief-resolver", "local_ffmpeg",
                                       "", {}, {}, 0.0)
        out_dir = self.job_dir(job["id"]) / sid / f"v{ver}"
        out_dir.mkdir(parents=True, exist_ok=True)
        merged = dict(job["brief"])
        for blob in self._load_inputs(job, sdef).values():
            if not isinstance(blob, dict):
                continue
            for key in ("pattern_summary", "interpretation", "decode", "label", "topic"):
                if blob.get(key):
                    merged[key] = blob[key]
        if any(k in merged for k in ("pattern_summary", "interpretation", "decode", "label")):
            self.db.x("UPDATE jobs SET brief_json=? WHERE id=?", (json.dumps(merged), job["id"]))
            job["brief"] = merged
            scope = self.db._scope
            if scope is not None and scope.job_id == job["id"] and scope.job is not None:
                scope.job["brief"] = merged
                scope.job["brief_json"] = json.dumps(merged)
        resolved = {"brief": merged, "persona": self.cfg["_personas"][merged["persona"]],
                    "template": self.template(job).get("_path"),
                    "stages": {d["id"]: {"prompt": self.build_ctx(job, d, {}, dry_estimate=True)["prompt"],
                                         "params": d["params"], "needs": d["needs"], "review": d.get("review", False)}
                               for d in self.stage_defs(job) if d["kind"] != "brief"}}
        p = out_dir / "brief.json"
        p.write_text(json.dumps(resolved, indent=2, default=str))
        self.db.update_run(rid, status=READY, output_path=str(p), actual_cost_usd=0.0, cost_basis="local: $0",
                           attempts=1, finished_at=now())
        self.log(job["id"], sid, "info", f"v{ver} resolved prompts saved")
        if not self._publish(rid, out_dir):
            return False
        return True

    def _free_scratch(self, out_dir, downloaded=None):
        """Drop a stage directory and its downloaded inputs once Blob has them.

        Local runs keep the files: the DB path is the file itself.
        """
        from . import storage
        if not storage.enabled():
            return
        out_dir = Path(out_dir)
        shutil.rmtree(out_dir, ignore_errors=True)
        for directory in (out_dir.parent, out_dir.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break
        for path in downloaded or []:
            storage.release_local(path)

    def _publish(self, run_id, out_dir, downloaded=None):
        """Upload the stage directory. A Blob rejection fails this run instead of raising."""
        from .storage import publish_run, space_note
        try:
            url = publish_run(self.db, run_id, out_dir)
        except Exception as e:
            message = space_note(e)
            self.db.update_run(run_id, status="failed", error=message, finished_at=now())
            run = self.db.run(run_id)
            self.log(run["job_id"] if run else "?", run["stage"] if run else None, "error", message)
            return False
        if url:
            self._free_scratch(out_dir, downloaded)
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
