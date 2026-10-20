import argparse
import csv
import json
import sys
from pathlib import Path
import yaml

from .config import load_config, key_status
from .runner import Runner, SpendGuardError, BriefError, new_job_id


def load_briefs(path):
    p = Path(path)
    if p.suffix.lower() == ".csv":
        with open(p) as f:
            return [dict(r) for r in csv.DictReader(f)]
    data = yaml.safe_load(p.read_text())
    return data if isinstance(data, list) else data.get("jobs", [data])


def parse_fail(v):
    out = {}
    for item in v or []:
        s, _, n = item.partition(":")
        out[s] = int(n or 99)
    return out


def fmt_score(sc):
    if sc["identity"] is None and str(sc.get("identity_note") or "").startswith("not scored"):
        idt = "not scored"
    else:
        idt = "n/a" if sc["identity"] is None else f"{sc['identity']:.3f} (thr {sc['identity_threshold']})"
    v = sc["visual"] or {}
    return (f"{'PASS' if sc['passed'] else 'FLAGGED'} identity {idt} | visual {v.get('width')}x{v.get('height')} "
            f"sharp {v.get('sharpness')} luma {v.get('luma_mean')} | copy/brand/engagement: not scored (no LLM key)"
            + ("" if sc["passed"] else " | " + "; ".join(sc["flags"])))


def doctor(cfg):
    """Read-only. No network, no provider calls. Exit 1 if live mode would be refused."""
    from .db import DB
    from .lora import load_registry
    problems = []
    for n, p in cfg["providers"].items():
        if p.get("key_env"):
            name, ok = key_status(p)
            opt = p.get("optional_key")
            print(f"key   {n:11} {name} {'set' if ok else 'NOT SET'}" + (" (optional: needed only for its own stages)" if opt and not ok else ""))
            if not ok and not opt: problems.append(f"{n} key not set")
    b = cfg.get("budget") or {}
    with DB(cfg["_db_file"]) as db:
        spent = db.effective_spend()
        g = b.get("global_cap_usd")
        for prov, v in db.spend_summary().items():
            if not v["rec"] and not v["logged"]:
                continue
            if v["rec"]:
                print(f"spend {prov:11} logged ${v['logged']:.2f} | confirmed ${v['confirmed']:.2f} "
                      f"(dashboard ${v['rec']['amount_usd']:.2f} as of {v['rec']['as_of']}) | gap ${v['confirmed']-v['logged']:+.2f}")
            else:
                print(f"spend {prov:11} logged ${v['logged']:.2f} | not reconciled (run: studio reconcile --provider {prov} ...)")
        print(f"spend counted against caps ${spent:.2f} of cap ${g} (per-job ${b.get('per_job_cap_usd')}, daily ${b.get('daily_cap_usd')})")
        if not g: problems.append("no global cap")
        elif spent >= g: problems.append("global cap reached")
        f = db.one("SELECT COUNT(*) n, COALESCE(SUM(est_cost_usd),0) e FROM attempts WHERE mode='live' AND status='failed'")
        print(f"fails live attempts failed: {f['n']} (est ${f['e']:.2f} if charged; ledger counts them at actual, provider-rejected = $0)")
        lock = db.fal_lock()
        if lock:
            print(f"fal   LOCKED (exhausted balance) since {lock['started_at']} on {lock['job_id']}/{lock['stage']}")
            problems.append("fal locked: top up, then run live with STUDIO_FAL_TOPPED_UP=1")
        else:
            print("fal   no lock recorded")
        qs = db.one("SELECT COUNT(*) n, COALESCE(SUM(passed=0),0) bad FROM scores")
        print(f"qual  {qs['n']} outputs scored, {qs['bad']} flagged (identity thr "
              f"{(cfg.get('quality') or {}).get('identity_threshold')}); copy/brand/engagement not scored (no LLM key); flags below are live jobs only (dry-run mocks are placeholders)")
        for f in db.q("SELECT s.job_id, s.stage, s.version, s.identity, s.flags_json FROM scores s JOIN jobs j ON j.id=s.job_id WHERE passed=0 AND j.mode='live' AND "
                      "version=(SELECT MAX(version) FROM stage_runs WHERE job_id=s.job_id AND stage=s.stage) ORDER BY job_id"):
            print(f"flag  {f['job_id']}/{f['stage']} v{f['version']}: {'; '.join(json.loads(f['flags_json']))}")
        stuck = db.q("SELECT job_id, stage, version FROM stage_runs WHERE status='running'")
        for r in stuck: print(f"stuck {r['job_id']}/{r['stage']} v{r['version']} still 'running'")
        for st in db.q("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
            print(f"jobs  {st['status']:16} {st['n']}")
        for j in db.q("SELECT j.id, r.stage FROM jobs j JOIN stage_runs r ON r.job_id=j.id WHERE r.review_status='pending' "
                      "AND r.version=(SELECT MAX(version) FROM stage_runs WHERE job_id=j.id AND stage=r.stage)"):
            print(f"review pending {j['id']}/{j['stage']}")
    for persona, e in (load_registry() or {}).items():
        print(f"lora  {persona}: {str(e)[:140]}")
    print("OK: live preflight would pass" if not problems else "BLOCKERS: " + "; ".join(problems))
    return 0 if not problems else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio", description="AI Studio M1 stage-runner (DRY_RUN by default)")
    ap.add_argument("--live", action="store_true", help="make REAL paid calls (needs budget caps in config)")
    ap.add_argument("--auto-approve", action="store_true", help="dry-run only: pass review gates automatically")
    ap.add_argument("--auto-check", action="store_true", help="pass gates only if automated blank/corrupt QC passes")
    ap.add_argument("--fail-stage", action="append", help="mock only: force STAGE[:N] to fail N attempts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new"); s.add_argument("brief")
    s = sub.add_parser("run"); s.add_argument("job")
    s = sub.add_parser("rerun"); s.add_argument("job"); s.add_argument("--stage", required=True); s.add_argument("--cascade", action="store_true")
    s = sub.add_parser("approve"); s.add_argument("job"); s.add_argument("stage"); s.add_argument("--note", default="")
    s = sub.add_parser("reject"); s.add_argument("job"); s.add_argument("stage"); s.add_argument("--note", required=True)
    s = sub.add_parser("batch"); s.add_argument("file"); s.add_argument("--parallel", type=int, default=1)
    s = sub.add_parser("estimate"); s.add_argument("brief")
    s = sub.add_parser("status"); s.add_argument("job", nargs="?")
    s = sub.add_parser("save-template"); s.add_argument("job"); s.add_argument("name")
    s = sub.add_parser("train-lora"); s.add_argument("persona"); s.add_argument("--steps", type=int); s.add_argument("--resume", help="fal request id to re-attach to (no resubmit)")
    s = sub.add_parser("lora-test"); s.add_argument("persona"); s.add_argument("--n", type=int, default=4); s.add_argument("--version", type=int)
    s = sub.add_parser("loras")
    s = sub.add_parser("override"); s.add_argument("job"); s.add_argument("stage"); s.add_argument("--param", action="append", default=[])
    s.add_argument("--model"); s.add_argument("--price-key"); s.add_argument("--prompt")
    s = sub.add_parser("reconcile", help="offline: record dashboard-confirmed total spend for a provider")
    s.add_argument("--provider", required=True); s.add_argument("--amount", type=float, required=True, help="dashboard total USD to date")
    s.add_argument("--as-of", required=True, help="ISO date/time the dashboard figure covers, e.g. 2026-10-20T09:00+01:00")
    s.add_argument("--note", default="")
    s = sub.add_parser("score", help="offline: (re)score outputs: identity vs refs + visual checks"); s.add_argument("job", nargs="?")
    s = sub.add_parser("library", help="asset + prompt library of approved outputs")
    s.add_argument("action", choices=["list", "search", "promote", "backfill"]); s.add_argument("arg", nargs="?")
    s.add_argument("--persona"); s.add_argument("--promoted", action="store_true"); s.add_argument("--note", default="")
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("export", help="offline: bundle approved library items + captions (.srt) + caption.txt into a folder/zip")
    s.add_argument("--out", required=True); s.add_argument("--persona"); s.add_argument("--job"); s.add_argument("--promoted", action="store_true")
    s.add_argument("--id", type=int, action="append"); s.add_argument("--no-zip", action="store_true"); s.add_argument("--live-only", action="store_true")
    s.add_argument("--stages", default="finish,compose", help="comma list, or 'all' (default: final outputs; compose supersedes finish)")
    s = sub.add_parser("keys")
    s = sub.add_parser("doctor", help="offline health check: keys (names only), caps, spend, fal lock, LoRAs, failures")
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--public", action="store_true",
                   help="hosted mode: waitress server, sign-in required (STUDIO_APP_PASSWORD + STUDIO_SESSION_SECRET), dry runs only")
    a = ap.parse_args(argv)
    cfg = load_config()

    if a.cmd == "keys":
        for n, p in cfg["providers"].items():
            if p.get("key_env"):
                name, ok = key_status(p)
                print(f"{n}: {name} {'set' if ok else 'NOT SET'}")
        return 0
    if a.cmd == "reconcile":
        from .db import DB
        with DB(cfg["_db_file"]) as db:
            logged = db.add_reconciliation(a.provider, a.amount, a.as_of, a.note)
            v = db.spend_summary()[a.provider]
            print(f"recorded {a.provider} ${a.amount:.2f} as of {a.as_of}. logged ${logged:.2f}, gap ${a.amount-logged:+.2f}. "
                  f"caps now count ${v['effective']:.2f} for {a.provider}, ${db.effective_spend():.2f} total")
        return 0
    if a.cmd == "library":
        from .db import DB
        from . import library as L
        with DB(cfg["_db_file"]) as db:
            if a.action == "backfill":
                print(f"indexed {L.backfill(db)} approved outputs")
            elif a.action == "promote":
                if not a.arg:
                    print("usage: studio library promote <id | job/stage[/vN]> [--note]", file=sys.stderr); return 2
                try:
                    print(f"promoted #{L.promote(db, a.arg, a.note)}")
                except ValueError as e:
                    print(f"REFUSED: {e}", file=sys.stderr); return 2
            else:
                rs = L.rows(db, a.arg if a.action == "search" else None, a.persona, a.promoted, a.limit)
                print("\n".join(L.fmt(r) for r in rs) or "library empty (run: studio library backfill)")
        return 0
    if a.cmd == "doctor":
        return doctor(cfg)
    if a.cmd == "estimate":
        r = Runner(cfg)
        total = 0
        import atexit; atexit.register(r.close)
        for b in load_briefs(a.brief):
            rows = r.estimate_job(b)
            for row in rows:
                print(f"{row['stage']:8} {row['model']:45} {row['units']:>7} {row['unit']:9} x ${row['usd_per_unit']:<7} = ${row['est_usd']:.4f}")
            t = sum(x["est_usd"] for x in rows); total += t
            print(f"{'job total':8} ${t:.4f}  (one clean pass; worst case with 2 retries per paid stage ${t*3:.4f})")
        return 0
    if a.cmd == "loras":
        from .lora import load_registry
        print(yaml.safe_dump(load_registry(), sort_keys=False) or "no LoRAs trained yet"); return 0
    if a.cmd == "serve":
        from .web import create_app
        import os
        if a.public:
            if a.live:
                print("REFUSED: --public never runs live; live runs stay on the CLI", file=sys.stderr); return 2
            os.environ["STUDIO_REQUIRE_AUTH"] = "1"
            from .auth import config as auth_config
            if not auth_config()["configured"]:
                print("REFUSED: --public needs STUDIO_APP_PASSWORD and STUDIO_SESSION_SECRET in the environment", file=sys.stderr); return 2
            from waitress import serve
            serve(create_app(cfg, live=False), host=a.host, port=a.port, threads=8, trusted_proxy="127.0.0.1",
                  trusted_proxy_headers={"x-forwarded-proto"}, clear_untrusted_proxy_headers=True)
            return 0
        create_app(cfg, live=a.live).run(host=a.host, port=a.port, debug=False)
        return 0
    try:
        r = Runner(cfg, live=a.live, auto_approve=a.auto_approve, auto_check=a.auto_check,
                   fail_inject=parse_fail(a.fail_stage))
        if a.cmd == "new":
            for b in load_briefs(a.brief)[:1]:
                jid = r.create_job(b); print(jid); st = r.run_job(jid)
        elif a.cmd == "batch":
            bid = new_job_id("batch")
            ids = [r.create_job(b, batch_id=bid) for b in load_briefs(a.file)]
            r.run_jobs(ids, a.parallel)
            print(bid)
        elif a.cmd == "run":
            r.run_job(a.job)
        elif a.cmd == "rerun":
            r.rerun(a.job, a.stage, a.cascade)
        elif a.cmd in ("approve", "reject"):
            r.review(a.job, a.stage, "approved" if a.cmd == "approve" else "rejected", a.note)
        elif a.cmd == "override":
            params = {}
            for kv in a.param:
                k, _, v = kv.partition("=")
                params[k] = yaml.safe_load(v)
            r.set_override(a.job, a.stage, params, a.model, a.price_key, a.prompt)
        elif a.cmd == "train-lora":
            from .lora import train
            print(json.dumps(train(r, a.persona, a.steps, resume=a.resume), indent=2, default=str))
        elif a.cmd == "lora-test":
            from .lora import test_stills
            if not a.live:
                print("REFUSED: lora-test makes paid calls; add --live", file=sys.stderr); return 2
            print("\n".join(test_stills(r, a.persona, a.n, a.version)))
        elif a.cmd == "export":
            from .export import export
            from . import library; library.backfill(r.db)
            man, z = export(r.db, a.out, persona=a.persona, promoted=a.promoted, job=a.job, ids=a.id, zip_it=not a.no_zip,
                            live_only=a.live_only,
                            stages=None if a.stages == 'all' else tuple(a.stages.split(',')))
            for i in man["items"]:
                print(f"#{i['library_id']:<4} " + (f"{i['job']}/{i['stage']} v{i['version']} [{i['mode']}] -> {i['folder']}/ ({len(i['files'])} files)"
                                                 if "files" in i else i["skipped"]))
            print(f"{man['count']} item(s) -> {a.out}" + (f"  zip: {z}" if z else ""))
        elif a.cmd == "save-template":
            print(r.save_template(a.job, a.name))
        elif a.cmd == "score":
            for jid in ([a.job] if a.job else [j["id"] for j in r.db.jobs()]):
                for run in r.db.runs(jid):
                    if run["status"] == "succeeded" or run["output_path"]:
                        sc = r.score_run(run["id"])
                        if sc: print(f"{jid} {run['stage']:8} v{run['version']} {fmt_score(r.db.score(run['id']))}")
        elif a.cmd == "status":
            jobs = [r.db.job(a.job)] if a.job else r.db.jobs()
            for j in jobs:
                c = r.db.job_costs(j["id"])
                print(f"{j['id']}  {j['persona']:7} {j['status']:16} mode={j['mode']} est=${c['est']:.4f} actual=${c['act']:.4f}")
                if a.job:
                    for run in r.db.runs(j["id"]):
                        print(f"   {run['stage']:8} v{run['version']} {run['status']:15} review={run['review_status']} "
                              f"est=${run['est_cost_usd'] or 0:.4f} act=${run['actual_cost_usd'] or 0:.4f} tries={run['attempts']} "
                              f"{(run['error'] or '')[:80]}")
                        sc = r.db.score(run["id"])
                        if sc: print(f"      quality: {fmt_score(sc)}")
            print(f"live spend logged (all jobs): ${r.db.live_spend():.4f}")
    except (SpendGuardError, BriefError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    finally:
        if "r" in locals():
            r.close()
    return 0
