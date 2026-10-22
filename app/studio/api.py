"""JSON API for the React UI. Thin layer over Runner / DB / library / export / persona. Dry runs only from the UI."""
import contextlib, datetime as dt, hmac, io, json, os, threading, time
from pathlib import Path
from flask import Blueprint, abort, jsonify, redirect, request, send_file
from . import library, persona as P
from .adintel import configured
from .adintel import accounts as intel_accounts
from .board import (BoardError, compile_board, ensure_presets, feed_artefact, job_brief, preset_intact,
                    preview_config, preview_pack)
from .config import connection_status, key_status, load_template
from .db import DatabaseBusy
from .gates import show_review
from .names import purpose_name
from .runner import BriefError, SpendGuardError

UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_REF_ROOTS = (
    Path("/workspace/personas"),
    Path(__file__).resolve().parents[2] / "personas",
    Path(__file__).resolve().parents[1] / "refs",
)


def _resolve_ref(raw):
    """Use the stored path when the file is there. Otherwise the same filename under the persona store."""
    path = Path(raw)
    if path.is_file():
        return path
    name = path.name
    if not name:
        return path
    for root in _REF_ROOTS:
        if not root.is_dir():
            continue
        hit = next((p for p in root.rglob(name) if p.is_file()), None)
        if hit:
            return hit
    return path
_doctor_lock = threading.Lock()


def one_stage():
    """Vercel cannot keep a background thread alive after the response. One stage per request."""
    return os.environ.get("VERCEL") == "1" or os.environ.get("STUDIO_ONE_STAGE") == "1"


class _NoFfmpegFetch:
    """A dry board writes placeholder bytes even when ffmpeg is already on disk."""

    def __enter__(self):
        self.previous = os.environ.get("STUDIO_NO_FFMPEG_FETCH")
        os.environ["STUDIO_NO_FFMPEG_FETCH"] = "1"
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.previous is None:
            os.environ.pop("STUDIO_NO_FFMPEG_FETCH", None)
        else:
            os.environ["STUDIO_NO_FFMPEG_FETCH"] = self.previous
        return False


class _LazyDB:
    """Connect on the first API call. Serving the shell or a hashed asset must not open Turso."""

    def __init__(self, runner):
        self._runner = runner
        self._db = None
        self._seeded = False

    def live(self):
        if self._db is None:
            self._db = self._runner.db
        if not self._seeded:
            ensure_presets(self._db)
            self._seeded = True
        return self._db

    def __getattr__(self, name):
        return getattr(self.live(), name)


def _stamp_age(stamp):
    if not stamp:
        return None
    try:
        started = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.astimezone()
    return (dt.datetime.now().astimezone() - started.astimezone()).total_seconds()


def register(app, cfg, runner, live, bg):
    db = _LazyDB(runner)
    bp = Blueprint("api", __name__)
    app.json.sort_keys = False

    @bp.before_request
    def _open_database():
        try:
            db.live()
        except DatabaseBusy as exc:
            return jsonify({"error": str(exc)}), 503

    @app.errorhandler(DatabaseBusy)
    def _database_busy(exc):
        return jsonify({"error": str(exc)}), 503

    active, alock = set(), threading.Lock()
    data_dir = Path(cfg["_data_dir"]).resolve()
    displays = {k: (v.get("display") or k) for k, v in (cfg.get("_personas") or {}).items()}

    def present(row):
        item = dict(row)
        item["purpose"] = purpose_name(displays.get(item.get("persona")) or item.get("persona") or "Asset",
                                       item.get("stage"), item.get("version"))
        item["library_id"] = item.get("id")
        return item

    def _guarded(jid, fn, a):
        with alock:
            if jid in active:
                raise BriefError(f"{jid} is already running")
            active.add(jid)

        def go():
            try:
                fn(*a)
            except Exception as e:                       # surfaced in the job's event log
                db.log(jid, None, "error", f"ui run failed: {e}")
            finally:
                with alock:
                    active.discard(jid)
        return go

    def run_bg(jid, fn, *a):
        bg(_guarded(jid, fn, a))

    def launch(jid, *, only=None, cascade=False):
        """Start a job. inngest sends an event; Vercel advances one stage; otherwise a thread."""
        from . import workflows
        mode = workflows.executor_mode()
        if mode == "inngest":
            workflows.send_job(jid, only=only, cascade=cascade)
            return None
        if one_stage():
            return runner.advance(jid, only=only)
        if only is not None:
            fn, args = runner.rerun, (jid, only, cascade)
        else:
            fn, args = runner.run_job, (jid,)
        if mode == "inline":
            _guarded(jid, fn, args)()
            return None
        run_bg(jid, fn, *args)
        return None

    def err(msg, code=400):
        return jsonify({"error": str(msg)}), code

    def private(payload, code=200):
        """Per-request reads. Vercel otherwise caches a public body that never saw the OIDC header."""
        resp = jsonify(payload)
        resp.status_code = code
        resp.cache_control.no_store = True
        resp.cache_control.no_cache = True
        resp.cache_control.private = True
        resp.cache_control.max_age = 0
        resp.cache_control.public = None
        resp.headers["CDN-Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"
        return resp

    def doctor_text():
        from .cli import doctor
        buf = io.StringIO()
        with _doctor_lock, contextlib.redirect_stdout(buf):
            rc = doctor(cfg)
        out = buf.getvalue()
        blockers = []
        for line in out.splitlines():
            if line.startswith("BLOCKERS: "):
                blockers = [b.strip() for b in line[10:].split(";") if b.strip()]
        return out, blockers, rc

    def job_row(j):
        c = db.job_costs(j["id"])
        return {k: j[k] for k in ("id", "persona", "template", "mode", "status", "created_at")} | {
            "est": sum((r["est_cost_usd"] or 0) for r in db.runs(j["id"])), "act": c["act"],
            "running": j["id"] in active, "batch_id": j.get("batch_id")}

    def pending_reviews():
        return [dict(r) for r in db.q(
            "SELECT j.id job_id, r.stage, r.version FROM jobs j JOIN stage_runs r ON r.job_id=j.id WHERE r.review_status='pending' "
            "AND r.version=(SELECT MAX(version) FROM stage_runs WHERE job_id=j.id AND stage=r.stage) ORDER BY j.created_at DESC")]

    def caps():
        g, j, d = runner.caps(); return {"global": g, "per_job": j, "daily": d}

    # ---------- reads
    @bp.get("/api/meta")
    def meta():
        pers = cfg["_personas"]
        return private({"live": live, "personas": [{"id": k, "display": v.get("display") or k, "voice_name": v.get("voice_name") or ""} for k, v in pers.items()],
                        "templates": sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml")),
                        "fal_lock": db.fal_lock(), "caps": caps(), "spend": db.effective_spend(),
                        "connections": connection_status(cfg),
                        "intel_origin": "live" if configured() else "fixture"})

    @bp.get("/api/overview")
    def overview():
        out, blockers, _ = doctor_text()
        jobs = [job_row(j) for j in db.jobs()]
        counts = {}
        for j in jobs:
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        return jsonify({"spend": db.effective_spend(), "caps": caps(), "jobs": jobs[:12], "job_count": len(jobs),
                        "counts": counts, "blockers": blockers, "doctor": out, "pending": pending_reviews(),
                        "fal_lock": db.fal_lock(), "library_count": len(library.rows(db, limit=100000))})

    @bp.get("/api/jobs")
    def jobs():
        runner.reconcile()
        q, st = (request.args.get("q") or "").lower(), request.args.get("status")
        rows = [job_row(j) for j in db.jobs()]
        rows = [r for r in rows if (not q or q in f"{r['id']} {r['persona']} {r['template']}".lower()) and (not st or r["status"] == st)]
        return jsonify(rows)

    @bp.get("/api/jobs/<jid>")
    def job(jid):
        runner.reconcile(jid)
        j = db.job(jid) or abort(404)
        all_runs = db.runs(jid)
        if any(r["status"] == "running" for r in all_runs):
            runner.reconcile(jid)
            j = db.job(jid) or abort(404)
            all_runs = db.runs(jid)
        by_stage = {}
        for r in all_runs:
            by_stage.setdefault(r["stage"], []).append(r)
        latest = {sid: rows[-1] for sid, rows in by_stage.items() if rows}

        def stale(run):
            if run["status"] != "succeeded":
                return False
            for name, rid in json.loads(run["inputs_json"] or "{}").items():
                cur = latest.get(name)
                if cur and cur["id"] != rid:
                    return True
            return False

        scores = {}
        for row in db.q("SELECT * FROM scores WHERE job_id=?", (jid,)):
            row["visual"] = json.loads(row["visual_json"] or "null")
            row["flags"] = json.loads(row["flags_json"] or "[]")
            scores[row["stage_run_id"]] = row
        stages = []
        for d in runner.stage_defs(j):
            key = d.get("uses", d["id"])
            base = cfg["stages"].get(key, {"provider": "local", "model": "brief-resolver"})
            sc = base
            if key in cfg.get("stages", {}):
                _prov, sc = runner.provider_for(key, job=j)
            runs = []
            for r in by_stage.get(d["id"], []):
                m = json.loads(r["meta_json"] or "{}")
                sco = scores.get(r["id"])
                runs.append({k: r[k] for k in ("id", "version", "status", "review_status", "review_note", "mode", "provider", "model",
                                                "output_path", "actual_cost_usd", "est_cost_usd", "attempts", "error", "started_at", "finished_at")}
                            | {"exports": m.get("exports"), "poster": m.get("poster"), "score": sco,
                               "summary": m.get("summary"),
                               "stale": stale(r)})
            last = runs[-1] if runs else {}
            stages.append({"id": d["id"], "kind": d["kind"], "node": d.get("board_node") or d["kind"],
                           "review": bool(d.get("review")),
                           "show_review": show_review(d["id"], d.get("review"), last.get("status"), last.get("review_status")),
                           "needs": d.get("needs") or [],
                           "provider": last.get("provider") or sc["provider"], "model": last.get("model") or sc["model"],
                           "default_model": base.get("model"), "runs": runs})
        return jsonify({"job": job_row(j) | {"brief": j["brief"]}, "stages": stages, "events": db.events(jid, 60),
                        "running": jid in active})

    @bp.get("/api/library")
    def lib():
        a = request.args
        rows = library.rows(db, a.get("q") or None, a.get("persona") or None, a.get("promoted") == "1", limit=int(a.get("limit", 300)))
        if a.get("stage"):
            rows = [r for r in rows if r["stage"] == a["stage"]]
        if a.get("final") == "1":
            rows = [r for r in rows if r["stage"] in library.FINAL]
        return jsonify([present(r) for r in rows])

    @bp.get("/api/library/pending")
    def lib_pending():
        return jsonify([present(r) for r in library.pending(db)])

    @bp.get("/api/personas")
    def personas():
        from .storage import allowed_redirect
        out = []
        for k, v in cfg["_personas"].items():
            refs = []
            for e in P.entries(v.get("refs")):
                url = e["url"] if allowed_redirect(e.get("url") or "") else None
                exists = bool(url) or bool(e.get("path") and Path(e["path"]).exists())
                if not exists and e.get("path"):
                    exists = _resolve_ref(e["path"]).is_file()
                refs.append({"i": e["i"], "name": e["name"], "role": e["role"], "exists": exists,
                             "url": url, "note": e.get("note"), "sha256": e.get("sha256"),
                             "image": P.is_image(e), "placeholder": not exists})
            out.append({"id": k, "display": v.get("display") or k, "look": v.get("look"), "voice_name": v.get("voice_name"),
                        "lora": (v.get("lora") or {}).get("status"), "lock": v.get("lock") or {},
                        "identity_ref_note": v.get("identity_ref_note"), "refs": refs,
                        "missing": P.missing(v), "lock_text": P.lock_text(v, "image")})
        return jsonify(out)

    @bp.get("/api/personas/<pid>/ref/<int:i>")
    def ref(pid, i):
        from .storage import allowed_redirect
        raw_refs = (cfg["_personas"].get(pid) or {}).get("refs") or []
        found = next((e for e in P.entries(raw_refs) if e["i"] == i), None)
        if not found:
            abort(404)
        if allowed_redirect(found.get("url") or ""):
            return redirect(found["url"])
        path = found.get("path")
        if path and Path(path).exists():
            return send_file(path)
        if path:
            resolved = _resolve_ref(path)
            if resolved.is_file():
                return send_file(resolved)
        if i < len(raw_refs) and isinstance(raw_refs[i], str):
            resolved = _resolve_ref(raw_refs[i])
            if resolved.is_file():
                return send_file(resolved)
        abort(404)

    @bp.get("/api/settings")
    def settings():
        keys = []
        for n, p in cfg["providers"].items():
            if p.get("key_env"):
                name, ok = key_status(p)
                keys.append({"provider": n, "env": name, "set": ok, "optional": bool(p.get("optional_key"))})
        # doctor() opens a second database and walks the ledger. The settings page
        # never renders that scan, and on a remote database it held the response for seconds.
        return jsonify({"caps": caps(), "spend": db.effective_spend(), "spend_by_provider": db.spend_summary(),
                        "quality": cfg.get("quality") or {}, "keys": keys, "connections": connection_status(cfg),
                        "fal_lock": db.fal_lock(), "blockers": [], "data_dir": str(data_dir)})

    @bp.get("/api/spend")
    def spend():
        runner.reconcile()
        rows = [dict(r) for r in db.q(
            "SELECT a.job_id, a.stage, a.mode, a.provider, a.model, a.status, a.est_cost_usd, a.actual_cost_usd, a.latency_s, a.started_at "
            "FROM attempts a ORDER BY a.id DESC LIMIT 200")]
        live = [r for r in rows if r["mode"] == "live"]
        by_p, by_m = {}, {}
        for r in db.q("SELECT provider, model, COALESCE(SUM(actual_cost_usd),0) c, COUNT(*) n FROM attempts WHERE mode='live' GROUP BY provider, model"):
            pk, mk = r["provider"] or "unknown", r["model"] or "unknown"
            by_p[pk] = by_p.get(pk, 0) + r["c"]; by_m[mk] = by_m.get(mk, 0) + r["c"]
        n_live = db.one("SELECT COUNT(*) n FROM attempts WHERE mode='live'")["n"]
        by_api = [{"provider": r["provider"] or "unknown", "api": r["model"] or "unknown",
                   "cost": r["c"], "calls": r["n"]} for r in db.q(
            "SELECT provider, model, COALESCE(SUM(actual_cost_usd),0) c, COUNT(*) n FROM attempts "
            "WHERE mode='live' GROUP BY provider, model ORDER BY c DESC")]
        fallback = [dict(r) for r in db.q(
            "SELECT job_id, stage, model, status, actual_cost_usd, started_at FROM attempts "
            "WHERE provider='higgsfield' ORDER BY id DESC LIMIT 20")]
        span = db.one("SELECT MIN(started_at) a, MAX(started_at) b, COALESCE(SUM(actual_cost_usd),0) c "
                      "FROM attempts WHERE mode='live'")
        if not n_live:
            forecast = {"month": 0.0, "note": "No live calls, so the month forecast stays $0."}
        else:
            forecast = {"month": round(float(span["c"] or 0), 4),
                        "note": "Forecast is the live spend already logged. There is not a longer window to project from."}
            try:
                start = dt.datetime.fromisoformat(span["a"])
                end = dt.datetime.fromisoformat(span["b"])
                days = max((end - start).total_seconds() / 86400, 1)
                if days >= 1 and start != end:
                    forecast = {"month": round(float(span["c"] or 0) / days * 30, 4),
                                "note": "Month forecast is logged live spend spread over the days those calls cover, times 30."}
            except (TypeError, ValueError):
                pass
        return jsonify({"logged": db.live_spend(), "counted": db.effective_spend(), "caps": caps(), "calls": n_live,
                        "by_provider": by_p, "by_model": by_m, "by_api": by_api, "top_apis": by_api[:5],
                        "forecast": forecast, "higgsfield_fallback": fallback,
                        "recent": rows, "reconciled": db.spend_summary(), "live_recent": len(live)})

    # ---------- actions
    def brief_from(b):
        brief = {k: v for k, v in (b or {}).items() if v not in (None, "") and k in
                 ("id", "persona", "template", "script", "setting", "topic", "headline", "cta", "caption", "hashtags",
                  "still_provider")}
        return brief

    @bp.post("/api/estimate")
    def estimate():
        try:
            rows = runner.estimate_job(brief_from(request.get_json(force=True).get("brief")))
        except (BriefError, KeyError, ValueError) as e:
            return err(e)
        return jsonify({"rows": rows, "total": sum(r["est_usd"] for r in rows)})

    @bp.post("/api/jobs")
    def create():
        body = request.get_json(force=True)
        if body.get("mode") == "live":
            if os.environ.get("STUDIO_ALLOW_LIVE") != "1":
                lock = db.fal_lock()
                if lock:
                    return err(f"Live runs are blocked: the fal account has been locked (exhausted balance) since {lock['started_at']} "
                               f"on {lock['job_id']}/{lock['stage']}. Top up at fal.ai, then run live from the CLI with STUDIO_FAL_TOPPED_UP=1.", 409)
                return err("Live runs are blocked. Set STUDIO_ALLOW_LIVE=1 to allow them from this app. "
                           "Otherwise start them from the CLI (studio --live new ...) so the spend guards stay explicit.", 409)
            try:
                from .runner import Runner
                with Runner(cfg, live=True, log=lambda m: None) as lr:
                    jid = lr.create_job(brief_from(body.get("brief")))
            except (BriefError, SpendGuardError, KeyError, ValueError) as e:
                return err(e, 400)
            return jsonify({"id": jid}), 201
        if live:
            return err("this server was started with --live; the UI only starts dry runs", 409)
        try:
            jid = runner.create_job(brief_from(body.get("brief")))
        except (BriefError, KeyError, ValueError) as e:
            return err(e)
        try:
            from . import workflows
            if workflows.executor_mode() == "inngest" or not one_stage():
                launch(jid)
        except Exception as e:
            from .workflows import QueueError
            if isinstance(e, QueueError):
                db.log(jid, None, "error", f"inngest enqueue failed: {e}")
                return err(e, 503)
            raise
        return jsonify({"id": jid}), 201

    @bp.post("/api/jobs/<jid>/advance")
    def advance(jid):
        job = db.job(jid) or abort(404)
        if job["mode"] == "live" and os.environ.get("STUDIO_ALLOW_LIVE") != "1":
            return err("Live runs are blocked. Set STUDIO_ALLOW_LIVE=1 to advance them.", 409)
        try:
            if job["mode"] == "live":
                body = runner.advance(jid)
            else:
                with _NoFfmpegFetch():
                    body = runner.advance(jid)
            return jsonify(body)
        except BriefError as e:
            return err(e, 409)
        except DatabaseBusy:
            raise
        except Exception as e:
            return err(e, 502)

    @bp.route("/api/cron/advance", methods=["GET", "POST"])
    def cron_advance():
        secret = os.environ.get("CRON_SECRET") or ""
        if not secret:
            return err("CRON_SECRET is not set", 503)
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}"):
            return err("unauthorized", 401)
        # A killed instance leaves its stage_run `running`. Recover those before picking the next three jobs.
        reconciled = runner.reconcile()
        # failed jobs stay failed. partial here means more stages are still due, not a recorded stage error.
        # One stale job. A client that is already pumping updates the row, and a scan of
        # every open job is what held a connection until Turso reported capacity exceeded.
        rows = db.q("SELECT id, mode, updated_at FROM jobs WHERE status IN ('created','running','partial') ORDER BY updated_at LIMIT 8")
        out = []
        for row in rows:
            age = _stamp_age(row.get("updated_at"))
            if age is not None and age < 45:
                continue
            if row["mode"] == "live" and os.environ.get("STUDIO_ALLOW_LIVE") != "1":
                out.append({"id": row["id"], "skipped": "live blocked"})
                break
            try:
                out.append({"id": row["id"], **runner.advance(row["id"])})
            except DatabaseBusy as e:
                return err(str(e), 503)
            except Exception as e:
                out.append({"id": row["id"], "error": str(e)})
            break
        return jsonify({"ok": True, "reconciled": reconciled, "advanced": out})

    @bp.post("/api/jobs/<jid>/resume")
    def resume(jid):
        db.job(jid) or abort(404)
        try:
            stepped = launch(jid)
            if stepped is not None:
                return jsonify(stepped)
        except BriefError as e:
            return err(e, 409)
        except Exception as e:
            from .workflows import QueueError
            if isinstance(e, QueueError):
                return err(e, 503)
            raise
        return jsonify({"ok": True})

    @bp.post("/api/jobs/<jid>/rerun/<stage>")
    def rerun(jid, stage):
        db.job(jid) or abort(404)
        try:
            stepped = launch(jid, only=stage)
            if stepped is not None:
                return jsonify(stepped)
        except BriefError as e:
            return err(e, 409)
        except Exception as e:
            from .workflows import QueueError
            if isinstance(e, QueueError):
                return err(e, 503)
            raise
        return jsonify({"ok": True})

    @bp.post("/api/jobs/<jid>/review/<stage>")
    def review(jid, stage):
        b = request.get_json(force=True)
        v, note = b.get("verdict"), (b.get("note") or "").strip()
        if v not in ("approved", "rejected"):
            return err("verdict must be approved or rejected")
        if v == "rejected" and not note:
            return err("a reason is required to reject")
        try:
            runner.review(jid, stage, v, note)
        except BriefError as e:
            return err(e)
        resumed = False
        if v == "approved" and jid not in active and db.job(jid)["status"] in ("awaiting_review", "partial"):
            try:
                if one_stage():
                    from . import workflows
                    if workflows.executor_mode() != "inngest":
                        runner.db.set_job_status(jid, "partial")
                launch(jid)
                resumed = True
            except BriefError:
                pass
            except Exception as e:
                from .workflows import QueueError
                if not isinstance(e, QueueError):
                    raise
                db.log(jid, None, "error", f"inngest enqueue failed: {e}")
        return jsonify({"ok": True, "resumed": resumed, "pending": [p for p in pending_reviews() if p["job_id"] == jid]})

    @bp.post("/api/library/<int:lid>/promote")
    def promote(lid):
        try:
            library.promote(db, str(lid), (request.get_json(silent=True) or {}).get("note", ""))
        except ValueError as e:
            return err(e, 404)
        return jsonify({"ok": True})

    @bp.post("/api/library/add")
    def lib_add():
        b = request.get_json(force=True)
        try:
            lid = library.promote(db, f"{b['job_id']}/{b['stage']}" + (f"/v{b['version']}" if b.get("version") else ""), b.get("note", ""))
        except (ValueError, KeyError) as e:
            return err(e)
        return jsonify({"ok": True, "id": lid})

    @bp.post("/api/export")
    def do_export():
        from .export import export
        b = request.get_json(force=True)
        ids = [int(i) for i in b.get("ids") or []]
        if not ids:
            return err("select at least one item")
        name = time.strftime("bundle-%Y%m%d-%H%M%S")
        man, z = export(db, data_dir / "exports" / name, ids=ids, stages=None if b.get("all_stages") else ("finish", "compose"))
        if not man["count"]:
            return err("nothing to export: the selected items are not final outputs (tick 'include intermediate stages') or their files are missing")
        from . import storage
        zpath = Path(z)
        url, stored = f"/file?p={z}", z
        size = zpath.stat().st_size
        if storage.enabled():
            try:
                blob = storage.put_file(f"exports/{zpath.name}", zpath.read_bytes(), "application/zip")
            except storage.BlobError as e:
                return err(str(e), 502)
            url = stored = blob
            zpath.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(zpath.with_suffix(""), ignore_errors=True)
        return jsonify({"zip": stored, "url": url, "name": zpath.name, "size": size, "manifest": man})

    @bp.get("/api/exports")
    def exports():
        from . import storage
        rows, seen = [], set()
        if storage.enabled():
            try:
                blobs = storage.list_prefix("exports/")
            except storage.BlobError:
                blobs = []
            for blob in blobs:
                name = Path(blob.get("pathname") or "").name
                if not name.endswith(".zip") or not blob.get("url") or name in seen:
                    continue
                seen.add(name)
                at = (blob.get("uploadedAt") or "").replace("T", " ")[:16]
                rows.append({"name": name, "url": blob["url"], "size": blob.get("size") or 0, "at": at})
        d = data_dir / "exports"
        if d.exists():
            for z in sorted(d.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
                if z.name in seen:
                    continue
                seen.add(z.name)
                rows.append({"name": z.name, "url": f"/file?p={z}", "size": z.stat().st_size,
                             "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(z.stat().st_mtime))})
        return jsonify(rows[:20])

    def board_public(row, estimates=None, estimate_error=None):
        body = {"id": row["id"], "name": row["name"], "preset": row["preset"], "graph": row["graph"],
                "last_job_id": row.get("last_job_id"), "updated_at": row.get("updated_at"),
                "estimates": estimates or {},
                "intel_origin": "live" if configured() else "fixture"}
        if estimate_error:
            body["estimate_error"] = estimate_error
        return body

    def estimates_for(graph):
        try:
            stages = compile_board(graph)
            rows = runner.estimate_job(job_brief(graph, stages))
        except (BoardError, BriefError, KeyError, ValueError) as e:
            return {}, str(e)
        return {r["stage"]: r["est_usd"] for r in rows}, None

    @bp.get("/api/boards")
    def board_list():
        ensure_presets(db)
        return jsonify(db.boards())

    @bp.post("/api/boards")
    def board_create():
        body = request.get_json(force=True)
        graph = body.get("graph") or {}
        name = (body.get("name") or "Untitled board").strip() or "Untitled board"
        import random, string
        bid = "b-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        db.save_board(bid, name, graph, preset=False)
        row = db.board(bid)
        est, err = estimates_for(row["graph"])
        return jsonify(board_public(row, est, err)), 201

    @bp.get("/api/boards/<bid>")
    def board_get(bid):
        ensure_presets(db)
        row = db.board(bid) or abort(404)
        est, err = estimates_for(row["graph"])
        body = board_public(row, est, err)
        # Fixture reads are local. Attach them so the canvas can paint evidence on the
        # first response. A configured tracker is fetched by /api/intel/preview instead,
        # so this route does not wait on that network.
        if not configured():
            body["preview"] = preview_pack(preview_config(row["graph"]))
        return jsonify(body)

    @bp.put("/api/boards/<bid>")
    def board_save(bid):
        row = db.board(bid) or abort(404)
        body = request.get_json(force=True)
        graph = body.get("graph") or row["graph"]
        if bid == "talking_head" and not preset_intact(graph):
            # `err` later in this function is the estimate string, so this cannot call the helper.
            return jsonify({"error": "The Talking head preset still needs its own blocks. Use New for another board."}), 409
        name = (body.get("name") or row["name"]).strip() or row["name"]
        db.save_board(bid, name, graph)
        saved = db.board(bid)
        est, err = estimates_for(saved["graph"])
        return jsonify(board_public(saved, est, err))

    @bp.post("/api/boards/<bid>/run")
    def board_run(bid):
        row = db.board(bid) or abort(404)
        body = request.get_json(silent=True) or {}
        mode = body.get("mode") or "dry"
        try:
            stages = compile_board(row["graph"])
            brief = job_brief(row["graph"], stages)
        except BoardError as e:
            return err(e)
        if mode == "live":
            if os.environ.get("STUDIO_ALLOW_LIVE") != "1":
                return err("Live runs are blocked. Set STUDIO_ALLOW_LIVE=1 to allow them. Dry run is the default.", 409)
            try:
                from .runner import Runner
                with Runner(cfg, live=True, log=lambda m: None) as lr:
                    total = sum(r["est_usd"] for r in lr.estimate_job(brief))
                    lr.check_budget(bid, total, live=True)
                    jid = lr.create_job(brief)
            except (BriefError, SpendGuardError, KeyError, ValueError) as e:
                return err(e, 409)
            db.set_board_job(bid, jid)
            return jsonify({"id": jid, "mode": "live"}), 201
        if mode != "dry":
            return err("mode must be dry or live")
        # A dry pass walks every block with mock output. Review gates stay on live runs.
        for stage in stages:
            stage["review"] = False
        if live:
            return err("this server was started with --live; the board only starts dry runs", 409)
        try:
            jid = runner.create_job(brief)
        except (BriefError, KeyError, ValueError) as e:
            return err(e)
        db.set_board_job(bid, jid)
        if not one_stage():
            run_bg(jid, runner.run_job, jid)
            return jsonify({"id": jid, "mode": "dry", "pump": False}), 201
        # One run_job held every node on idle until the request returned, and on
        # Vercel that request downloaded ffmpeg. Run a short burst, then let the
        # client pump and paint. A dry board does not fetch the ffmpeg archive.
        try:
            with _NoFfmpegFetch():
                try:
                    budget = float(os.environ.get("STUDIO_DRY_BUDGET_S") or 18)
                except ValueError:
                    budget = 18.0
                deadline = time.monotonic() + max(0.0, budget)
                status, error, stages = "created", None, None
                # One read of this job, then each stage is a single write batch.
                # Reloading latest() per node is what made Turso take 30–60s a stage.
                with runner.db.job_scope(jid):
                    while time.monotonic() < deadline:
                        step = runner.advance(jid)
                        status = step.get("status") or status
                        stages = step.get("stages", stages)
                        if step.get("ok") is False:
                            error = step.get("error") or "Dry run stopped"
                            break
                        if step.get("action") in ("idle", "stopped"):
                            break
                        if status not in ("created", "running", "partial"):
                            break
        except BriefError as e:
            return err(e, 409)
        except DatabaseBusy:
            raise
        except Exception as e:
            return jsonify({"error": str(e), "id": jid}), 502
        pump = status in ("created", "running", "partial") and not error
        body = {"id": jid, "mode": "dry", "pump": pump, "status": status, "stages": stages or []}
        if error:
            body["error"] = error
        return jsonify(body), 201

    @bp.get("/api/intel/accounts")
    def intel_accounts_route():
        try:
            return private(intel_accounts())
        except Exception as e:
            from .adintel import AdIntelError
            if isinstance(e, AdIntelError):
                return err(str(e), 502)
            raise

    @bp.get("/api/intel/funnels")
    def intel_funnels_route():
        from .adintel import AdIntelError, directory
        try:
            return private(directory())
        except AdIntelError as e:
            return err(str(e), 502)

    @bp.get("/api/intel/preview")
    def intel_preview():
        a = request.args
        config = {"advertiser": a.get("advertiser") or "", "page_id": a.get("page_id") or "",
                  "status": a.get("status") or "",
                  "date_from": a.get("date_from") or "", "date_to": a.get("date_to") or "",
                  "longevity_min": a.get("longevity_min") or "", "ad_id": a.get("ad_id") or "",
                  "event_id": a.get("event_id") or "", "limit": a.get("limit") or "40",
                  "offset": a.get("offset") or "0"}
        try:
            if (a.get("scope") or "") == "feed":
                art = feed_artefact({k: v for k, v in config.items() if k != "event_id"})
                return private({"origin": (art.get("evidence") or {}).get("origin"), "feed": art})
            return private(preview_pack(config))
        except Exception as e:
            return err(e, 502)

    @bp.get("/api/intel/ads/<ad_id>/funnel")
    def intel_ad_funnel(ad_id):
        from .adintel import AdIntelError, funnel_for
        try:
            return private({**funnel_for(ad_id), "origin": "live" if configured() else "fixture"})
        except AdIntelError as e:
            return err(str(e), 502)

    @bp.get("/api/intel/funnels/<path:funnel_id>/route")
    def intel_funnel_route(funnel_id):
        from .adintel import AdIntelError, route_for_funnel
        try:
            return private(route_for_funnel(funnel_id))
        except AdIntelError as e:
            return err(str(e), 502)

    # ---------- SPA. /assets is registered on the app so it stays available
    # when the database gate returns before the blueprint.

    def spa(**_):
        if not (UI_DIST / "index.html").exists():
            return ("UI not built: cd ui && npm install && npm run build. The classic pages are at /classic.", 503)
        resp = send_file(UI_DIST / "index.html", max_age=None)
        # send_file writes Cache-Control through its cache_control object. A later
        # raw header assignment is replaced when that object updates, which left
        # only "no-cache" on the wire. Set the directives on the object.
        # Hashed bundles 404 when a cached index.html still names the previous build.
        resp.cache_control.no_cache = True
        resp.cache_control.no_store = True
        resp.cache_control.must_revalidate = True
        resp.cache_control.max_age = None
        resp.cache_control.public = None
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    app.register_blueprint(bp)
    for rule in ("/", "/new", "/board", "/jobs", "/jobs/<path:x>", "/library", "/export", "/personas", "/settings", "/spend"):
        app.add_url_rule(rule, "spa_" + rule.strip("/").replace("/", "_").replace("<path:x>", "x") or "spa_root", spa)
