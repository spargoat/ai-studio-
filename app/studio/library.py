"""Asset + prompt library: an index (same SQLite DB) of approved outputs with persona, template, prompt, scores, path.
Approved = review-approved stage output, or a finish/compose output of a job that reached 'done'.
'promoted' = picked as a reusable reference/prompt (manual)."""
import json
from .db import now

SCHEMA = """CREATE TABLE IF NOT EXISTS library (
  id INTEGER PRIMARY KEY AUTOINCREMENT, stage_run_id INTEGER UNIQUE, job_id TEXT, stage TEXT, version INTEGER,
  persona TEXT, template TEXT, mode TEXT, prompt TEXT, path TEXT, identity REAL, passed INTEGER, scores_json TEXT,
  reason TEXT, promoted INTEGER DEFAULT 0, note TEXT, added_at TEXT)"""
FINAL = ("finish", "compose")


def ensure(db):
    db.x(SCHEMA)


def add(db, run, reason):
    ensure(db)
    j = db.job(run["job_id"]); sc = db.score(run["id"])
    db.x("""INSERT INTO library (stage_run_id, job_id, stage, version, persona, template, mode, prompt, path, identity,
            passed, scores_json, reason, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(stage_run_id) DO UPDATE SET identity=excluded.identity, passed=excluded.passed,
            scores_json=excluded.scores_json, reason=excluded.reason""",
         (run["id"], run["job_id"], run["stage"], run["version"], j["persona"], j["template"], run["mode"],
          run["prompt"], run["output_path"], sc and sc["identity"], sc and sc["passed"],
          json.dumps({k: sc[k] for k in ("identity", "identity_note", "identity_threshold", "visual", "flags", "copy", "brand", "engagement")}) if sc else None,
          reason, now()))


def eligible(db, run, job):
    if run["status"] != "succeeded" or not run["output_path"]:
        return None
    if run["review_status"] == "approved":
        return "review approved"
    latest = db.latest(run["job_id"], run["stage"])
    if run["stage"] in FINAL and job["status"] == "done" and latest and latest["id"] == run["id"]:
        return "final output of a done job"
    return None


def backfill(db):
    ensure(db); n = 0
    for j in db.jobs():
        for run in db.runs(j["id"]):
            why = eligible(db, run, db.job(j["id"]))
            if why:
                add(db, run, why); n += 1
    return n


def rows(db, q=None, persona=None, promoted=None, limit=50):
    ensure(db)
    sql, a = "SELECT * FROM library WHERE 1=1", []
    if q:
        sql += " AND (prompt LIKE ? OR job_id LIKE ? OR template LIKE ? OR stage LIKE ? OR note LIKE ?)"; a += [f"%{q}%"] * 5
    if persona:
        sql += " AND persona=?"; a.append(persona)
    if promoted:
        sql += " AND promoted=1"
    return db.q(sql + " ORDER BY promoted DESC, added_at DESC, id DESC LIMIT ?", (*a, limit))


def promote(db, ref, note=""):
    """ref = library id, or job/stage[/vN]."""
    ensure(db)
    if str(ref).isdigit():
        r = db.one("SELECT * FROM library WHERE id=?", (int(ref),))
    else:
        parts = ref.split("/"); jid, st = parts[0], parts[1]
        run = (db.one("SELECT * FROM stage_runs WHERE job_id=? AND stage=? AND version=?", (jid, st, int(parts[2].lstrip("v"))))
               if len(parts) > 2 else db.latest(jid, st))
        if not run:
            raise ValueError(f"no run {ref}")
        if run["status"] != "succeeded":
            raise ValueError(f"{ref} has no successful output")
        add(db, run, "promoted manually")
        r = db.one("SELECT * FROM library WHERE stage_run_id=?", (run["id"],))
    if not r:
        raise ValueError(f"no library entry {ref}")
    db.x("UPDATE library SET promoted=1, note=? WHERE id=?", (note or r["note"], r["id"]))
    return r["id"]


def fmt(r):
    note = ""
    try:
        note = (json.loads(r.get("scores_json") or "{}") or {}).get("identity_note") or ""
    except (TypeError, ValueError):
        note = ""
    if note == "dry run, self-reference":
        idt = note
    else:
        idt = "n/a" if r["identity"] is None else f"{r['identity']:.3f}"
    return (f"#{r['id']:<4} {'*' if r['promoted'] else ' '} {r['persona']:7} {r['template']:14} {r['job_id']}/{r['stage']} v{r['version']} "
            f"[{r['mode']}] id {idt} {'PASS' if r['passed'] else 'FLAG' if r['passed'] is not None else '-'}  {r['path']}"
            + (f"\n       prompt: {r['prompt'][:140]}" if r["prompt"] else ""))
