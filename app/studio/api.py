"""JSON API for the React UI. Thin layer over Runner / DB / library / export / persona. Dry runs only from the UI."""
import contextlib, hmac, io, json, os, threading, time
from pathlib import Path
from flask import Blueprint, abort, jsonify, redirect, request, send_file
from . import library, persona as P
from .config import key_status, load_template
from .gates import show_review
from .runner import BriefError, SpendGuardError

UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_doctor_lock = threading.Lock()


def one_stage():
    """Vercel cannot keep a background thread alive after the response. One stage per request."""
    return os.environ.get("VERCEL") == "1" or os.environ.get("STUDIO_ONE_STAGE") == "1"


def register(app, cfg, runner, live, bg):
    db = runner.db
    bp = Blueprint("api", __name__)
    app.json.sort_keys = False
    active, alock = set(), threading.Lock()
    data_dir = Path(cfg["_data_dir"]).resolve()

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
        return jsonify({"live": live, "personas": [{"id": k, "display": v.get("display") or k} for k, v in pers.items()],
                        "templates": sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml")),
                        "fal_lock": db.fal_lock(), "caps": caps(), "spend": db.effective_spend()})

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
        q, st = (request.args.get("q") or "").lower(), request.args.get("status")
        rows = [job_row(j) for j in db.jobs()]
        rows = [r for r in rows if (not q or q in f"{r['id']} {r['persona']} {r['template']}".lower()) and (not st or r["status"] == st)]
        return jsonify(rows)

    @bp.get("/api/jobs/<jid>")
    def job(jid):
        j = db.job(jid) or abort(404)
        stages = []
        for d in runner.stage_defs(j):
            key = d.get("uses", d["id"])
            base = cfg["stages"].get(key, {"provider": "local", "model": "brief-resolver"})
            sc = base
            if key in cfg.get("stages", {}):
                _prov, sc = runner.provider_for(key, job=j)
            runs = []
            for r in db.runs(jid, d["id"]):
                m = json.loads(r["meta_json"] or "{}")
                sco = db.score(r["id"])
                runs.append({k: r[k] for k in ("id", "version", "status", "review_status", "review_note", "mode", "provider", "model",
                                                "output_path", "actual_cost_usd", "est_cost_usd", "attempts", "error", "started_at", "finished_at")}
                            | {"exports": m.get("exports"), "poster": m.get("poster"), "score": sco,
                               "stale": r["status"] == "succeeded" and runner.is_stale(jid, r)})
            last = runs[-1] if runs else {}
            stages.append({"id": d["id"], "kind": d["kind"], "review": bool(d.get("review")),
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
        return jsonify([dict(r) for r in rows])

    @bp.get("/api/personas")
    def personas():
        from .storage import allowed_redirect
        out = []
        for k, v in cfg["_personas"].items():
            refs = []
            for e in P.entries(v.get("refs")):
                url = e["url"] if allowed_redirect(e.get("url") or "") else None
                exists = bool(url) or bool(e.get("path") and Path(e["path"]).exists())
                refs.append({"i": e["i"], "name": e["name"], "role": e["role"], "exists": exists,
                             "url": url, "note": e.get("note"), "sha256": e.get("sha256"),
                             "image": P.is_image(e)})
            out.append({"id": k, "display": v.get("display") or k, "look": v.get("look"), "voice_name": v.get("voice_name"),
                        "lora": (v.get("lora") or {}).get("status"), "lock": v.get("lock") or {},
                        "identity_ref_note": v.get("identity_ref_note"), "refs": refs,
                        "missing": P.missing(v), "lock_text": P.lock_text(v, "image")})
        return jsonify(out)

    @bp.get("/api/personas/<pid>/ref/<int:i>")
    def ref(pid, i):
        from .storage import allowed_redirect
        found = next((e for e in P.entries((cfg["_personas"].get(pid) or {}).get("refs")) if e["i"] == i), None)
        if not found:
            abort(404)
        if allowed_redirect(found.get("url") or ""):
            return redirect(found["url"])
        path = found.get("path")
        if path and Path(path).exists():
            return send_file(path)
        abort(404)

    @bp.get("/api/settings")
    def settings():
        keys = []
        for n, p in cfg["providers"].items():
            if p.get("key_env"):
                name, ok = key_status(p)
                keys.append({"provider": n, "env": name, "set": ok, "optional": bool(p.get("optional_key"))})
        out, blockers, _ = doctor_text()
        return jsonify({"caps": caps(), "spend": db.effective_spend(), "spend_by_provider": db.spend_summary(),
                        "quality": cfg.get("quality") or {}, "keys": keys, "fal_lock": db.fal_lock(), "blockers": blockers,
                        "data_dir": str(data_dir)})

    @bp.get("/api/spend")
    def spend():
        rows = [dict(r) for r in db.q(
            "SELECT a.job_id, a.stage, a.mode, a.provider, a.model, a.status, a.est_cost_usd, a.actual_cost_usd, a.latency_s, a.started_at "
            "FROM attempts a ORDER BY a.id DESC LIMIT 200")]
        live = [r for r in rows if r["mode"] == "live"]
        by_p, by_m = {}, {}
        for r in db.q("SELECT provider, model, COALESCE(SUM(actual_cost_usd),0) c, COUNT(*) n FROM attempts WHERE mode='live' GROUP BY provider, model"):
            pk, mk = r["provider"] or "unknown", r["model"] or "unknown"
            by_p[pk] = by_p.get(pk, 0) + r["c"]; by_m[mk] = by_m.get(mk, 0) + r["c"]
        n_live = db.one("SELECT COUNT(*) n FROM attempts WHERE mode='live'")["n"]
        return jsonify({"logged": db.live_spend(), "counted": db.effective_spend(), "caps": caps(), "calls": n_live,
                        "by_provider": by_p, "by_model": by_m, "recent": rows, "reconciled": db.spend_summary(),
                        "live_recent": len(live)})

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
            return jsonify(runner.advance(jid))
        except BriefError as e:
            return err(e, 409)

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
        rows = db.q("SELECT id, mode FROM jobs WHERE status IN ('created','running','partial') ORDER BY updated_at LIMIT 3")
        out = []
        for row in rows:
            if row["mode"] == "live" and os.environ.get("STUDIO_ALLOW_LIVE") != "1":
                out.append({"id": row["id"], "skipped": "live blocked"})
                continue
            try:
                out.append({"id": row["id"], **runner.advance(row["id"])})
            except Exception as e:
                out.append({"id": row["id"], "error": str(e)})
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

    # ---------- SPA. /assets is registered on the app so it stays available
    # when the database gate returns before the blueprint.

    def spa(**_):
        if not (UI_DIST / "index.html").exists():
            return ("UI not built: cd ui && npm install && npm run build. The classic pages are at /classic.", 503)
        return send_file(UI_DIST / "index.html")

    app.register_blueprint(bp)
    for rule in ("/", "/new", "/jobs", "/jobs/<path:x>", "/library", "/export", "/personas", "/settings", "/spend"):
        app.add_url_rule(rule, "spa_" + rule.strip("/").replace("/", "_").replace("<path:x>", "x") or "spa_root", spa)
