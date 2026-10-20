import json
import sqlite3
import threading
import time
import datetime as dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, template TEXT, persona TEXT, brief_json TEXT,
  status TEXT, mode TEXT, batch_id TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT, stage TEXT, version INTEGER, status TEXT, mode TEXT,
  provider TEXT, model TEXT, price_key TEXT,
  prompt TEXT, params_json TEXT, inputs_json TEXT,
  output_path TEXT, meta_json TEXT,
  est_cost_usd REAL, actual_cost_usd REAL, cost_basis TEXT,
  attempts INTEGER DEFAULT 0, error TEXT,
  review_status TEXT, review_note TEXT, reviewed_at TEXT,
  started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_run_id INTEGER, job_id TEXT, stage TEXT, attempt INTEGER, mode TEXT,
  provider TEXT, model TEXT, status TEXT, error TEXT,
  est_cost_usd REAL, actual_cost_usd REAL, provider_request_id TEXT,
  started_at TEXT, finished_at TEXT, latency_s REAL
);
CREATE TABLE IF NOT EXISTS reconciliations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, amount_usd REAL, as_of TEXT,
  note TEXT, logged_at_entry REAL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS scores (
  stage_run_id INTEGER PRIMARY KEY, job_id TEXT, stage TEXT, version INTEGER, persona TEXT, path TEXT,
  identity REAL, identity_min REAL, identity_threshold REAL, identity_note TEXT, visual_json TEXT,
  copy REAL, brand REAL, engagement REAL, flags_json TEXT, passed INTEGER, scored_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, stage TEXT,
  level TEXT, message TEXT, created_at TEXT
);
"""

_lock = threading.Lock()


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        if self.conn is not None:
            self.conn.close(); self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fal_lock(self):
        """Last fal 'account locked / exhausted balance' rejection newer than the last fal success, else None.
        Offline check: reads the local attempt log only, never calls fal."""
        bad = self.one("SELECT job_id, stage, error, started_at FROM attempts WHERE provider='fal' AND status='failed' "
                       "AND (error LIKE '%User is locked%' OR error LIKE '%Exhausted balance%') ORDER BY id DESC LIMIT 1")
        if not bad:
            return None
        ok = self.one("SELECT started_at FROM attempts WHERE provider='fal' AND status='succeeded' ORDER BY id DESC LIMIT 1")
        return None if ok and ok["started_at"] > bad["started_at"] else bad

    def x(self, sql, args=()):
        with _lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def q(self, sql, args=()):
        with _lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    # --- jobs
    def create_job(self, job_id, template, persona, brief, mode, batch_id=None):
        t = now()
        self.x("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
               (job_id, template, persona, json.dumps(brief), "created", mode, batch_id, t, t))

    def set_job_status(self, job_id, status):
        self.x("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, now(), job_id))

    def job(self, job_id):
        j = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if j:
            j["brief"] = json.loads(j["brief_json"])
        return j

    def jobs(self):
        return self.q("SELECT * FROM jobs ORDER BY created_at DESC, id DESC")

    # --- stage runs
    def new_run(self, job_id, stage, mode, provider, model, price_key, prompt, params, inputs, est):
        v = (self.one("SELECT MAX(version) v FROM stage_runs WHERE job_id=? AND stage=?",
                      (job_id, stage))["v"] or 0) + 1
        cur = self.x("""INSERT INTO stage_runs (job_id, stage, version, status, mode, provider, model,
                     price_key, prompt, params_json, inputs_json, est_cost_usd, started_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (job_id, stage, v, "running", mode, provider, model, price_key, prompt,
                      json.dumps(params), json.dumps(inputs), est, now()))
        return cur.lastrowid, v

    def update_run(self, run_id, **kw):
        if "meta" in kw:
            kw["meta_json"] = json.dumps(kw.pop("meta"))
        sets = ", ".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE stage_runs SET {sets} WHERE id=?", (*kw.values(), run_id))

    def run(self, run_id):
        return self.one("SELECT * FROM stage_runs WHERE id=?", (run_id,))

    def runs(self, job_id, stage=None):
        if stage:
            return self.q("SELECT * FROM stage_runs WHERE job_id=? AND stage=? ORDER BY version", (job_id, stage))
        return self.q("SELECT * FROM stage_runs WHERE job_id=? ORDER BY stage, version", (job_id,))

    def latest(self, job_id, stage):
        return self.one("SELECT * FROM stage_runs WHERE job_id=? AND stage=? ORDER BY version DESC LIMIT 1",
                        (job_id, stage))

    # --- attempts / spend
    def add_attempt(self, **kw):
        cols = ", ".join(kw)
        cur = self.x(f"INSERT INTO attempts ({cols}) VALUES ({','.join('?'*len(kw))})", tuple(kw.values()))
        return cur.lastrowid

    def update_attempt(self, aid, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE attempts SET {sets} WHERE id=?", (*kw.values(), aid))

    def attempts(self, stage_run_id):
        return self.q("SELECT * FROM attempts WHERE stage_run_id=? ORDER BY attempt", (stage_run_id,))

    def live_spend(self, job_id=None, since=None):
        """Committed live spend. Failed attempts count at their estimate (conservative)."""
        sql = ("SELECT COALESCE(SUM(COALESCE(actual_cost_usd, est_cost_usd)),0) s FROM attempts "
               "WHERE mode='live'")
        args = []
        if job_id:
            sql += " AND job_id=?"; args.append(job_id)
        if since:
            sql += " AND started_at>=?"; args.append(since)
        return self.one(sql, args)["s"]

    def provider_spend(self, provider, since=None):
        sql = ("SELECT COALESCE(SUM(COALESCE(actual_cost_usd, est_cost_usd)),0) s FROM attempts "
               "WHERE mode='live' AND provider=?")
        args = [provider]
        if since:
            sql += " AND started_at>?"; args.append(since)
        return self.one(sql, args)["s"]

    def add_reconciliation(self, provider, amount, as_of, note=""):
        logged = self.provider_spend(provider)
        self.x("INSERT INTO reconciliations (provider, amount_usd, as_of, note, logged_at_entry, created_at) "
               "VALUES (?,?,?,?,?,?)", (provider, amount, as_of, note, logged, now()))
        return logged

    def latest_reconciliation(self, provider):
        return self.one("SELECT * FROM reconciliations WHERE provider=? ORDER BY as_of DESC, id DESC LIMIT 1", (provider,))

    def spend_summary(self):
        """Per provider: logged, confirmed (dashboard total as of a date + logged after it), effective = max."""
        provs = {r["provider"] for r in self.q("SELECT DISTINCT provider FROM attempts WHERE mode='live'")}
        provs |= {r["provider"] for r in self.q("SELECT DISTINCT provider FROM reconciliations")}
        out = {}
        for p in sorted(x for x in provs if x):
            logged = self.provider_spend(p)
            rec = self.latest_reconciliation(p)
            confirmed = None if not rec else rec["amount_usd"] + self.provider_spend(p, since=rec["as_of"])
            out[p] = dict(logged=logged, confirmed=confirmed, rec=rec,
                          effective=max(logged, confirmed or 0))
        return out

    def effective_spend(self):
        """Spend used by the global cap: higher of logged and dashboard-confirmed, per provider."""
        return sum(v["effective"] for v in self.spend_summary().values())

    def save_score(self, run, persona, path, s):
        self.x("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run["id"], run["job_id"], run["stage"], run["version"], persona, path, s.get("identity"),
                s.get("identity_min"), s.get("identity_threshold"), s.get("identity_note"), json.dumps(s.get("visual")),
                None, None, None, json.dumps(s.get("flags", [])), int(bool(s.get("passed"))), now()))

    def score(self, stage_run_id):
        r = self.one("SELECT * FROM scores WHERE stage_run_id=?", (stage_run_id,))
        if r:
            r["visual"] = json.loads(r["visual_json"] or "null"); r["flags"] = json.loads(r["flags_json"] or "[]")
        return r

    def job_costs(self, job_id):
        r = self.one("""SELECT COALESCE(SUM(est_cost_usd),0) est, COUNT(*) n FROM stage_runs WHERE job_id=?""", (job_id,))
        r["act"] = self.one("SELECT COALESCE(SUM(actual_cost_usd),0) a FROM attempts WHERE job_id=?", (job_id,))["a"]
        return r

    # --- events
    def log(self, job_id, stage, level, message):
        self.x("INSERT INTO events (job_id, stage, level, message, created_at) VALUES (?,?,?,?,?)",
               (job_id, stage, level, message, now()))

    def events(self, job_id, limit=200):
        return self.q("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))
