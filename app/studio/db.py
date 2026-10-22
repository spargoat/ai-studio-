import json
import os
import sqlite3
import threading
import time
import datetime as dt
from pathlib import Path

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
CREATE TABLE IF NOT EXISTS boards (
  id TEXT PRIMARY KEY, name TEXT, preset INTEGER DEFAULT 0, graph_json TEXT,
  last_job_id TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS leases (
  name TEXT PRIMARY KEY, owner TEXT, job_id TEXT, expires_at TEXT
);
"""

_lock = threading.Lock()


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class _CursorShim:
    lastrowid = None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Row(dict):
    """Mapping row so `dict(row)` and `row["col"]` match sqlite3.Row."""


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = cur.lastrowid

    def fetchall(self):
        desc = self._cur.description or ()
        names = [d[0] for d in desc]
        rows = self._cur.fetchall()
        if not names:
            return rows
        return [_Row(zip(names, row)) for row in rows]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class _LibsqlConn:
    """sqlite3-shaped wrapper around a libSQL connection (no row_factory)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, args=()):
        return _Cursor(self._raw.execute(sql, args))

    def executescript(self, sql):
        self._raw.executescript(sql)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

    @property
    def in_transaction(self):
        return bool(getattr(self._raw, "in_transaction", False))


# One Hrana execute is a network round trip. A dead stream surfaces as this
# family of errors and has to be reopened; the next statement will not heal it.
# "hrana" matches every libSQL error, including "capacity exceeded". Treating that
# as a dead stream opened a second connection and made the limit worse.
_STALE_MARKERS = (
    "connection closed",
    "closed before message",
    "stream closed",
    "stream expired",
    "server closed",
    "broken pipe",
    "connection reset",
    "not connected",
)
_REMOTE_CALL_S = 8.0


def _network_url(url):
    return (url or "").lower().startswith(("libsql:", "https:", "http:", "wss:", "ws:"))


def _stale_error(exc):
    if _capacity_error(exc):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _STALE_MARKERS)


def _capacity_error(exc):
    msg = str(exc).lower()
    return "capacity" in msg or "too many connections" in msg or "temporarily exceeded" in msg


def _close_quietly(conn):
    """Close off the request thread. libSQL close waits for the call that already timed out."""
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def _lit(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return "NULL"
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render(sql, args):
    args = tuple(args or ())
    parts = sql.split("?")
    if len(parts) != len(args) + 1:
        raise ValueError(f"SQL placeholder mismatch ({len(parts) - 1} vs {len(args)})")
    out = []
    for part, arg in zip(parts, args):
        out.append(part)
        out.append(_lit(arg))
    out.append(parts[-1])
    return "".join(out)


class _JobScope:
    """One job's rows in memory, and one script of writes flushed as a single round trip."""

    def __init__(self, db, job_id):
        self.db = db
        self.job_id = job_id
        self.job = None
        self.rows = []
        self.scores = {}
        self.stmts = []
        self.next_run = 1
        self.next_attempt = 1

    def load(self):
        self.job = self.db._read_one("SELECT * FROM jobs WHERE id=?", (self.job_id,))
        if self.job:
            self.job["brief"] = json.loads(self.job["brief_json"])
        self.rows = self.db._read_all(
            "SELECT * FROM stage_runs WHERE job_id=? ORDER BY version", (self.job_id,))
        ids = self.db._read_one(
            "SELECT (SELECT COALESCE(MAX(id),0) FROM stage_runs) AS runs, "
            "(SELECT COALESCE(MAX(id),0) FROM attempts) AS attempts")
        self.next_run = int(ids["runs"] or 0) + 1
        self.next_attempt = int(ids["attempts"] or 0) + 1
        for row in self.db._read_all("SELECT * FROM scores WHERE job_id=?", (self.job_id,)):
            row["visual"] = json.loads(row["visual_json"] or "null")
            row["flags"] = json.loads(row["flags_json"] or "[]")
            self.scores[row["stage_run_id"]] = row

    def buffer(self, sql, args=()):
        self.stmts.append(_render(sql, args))

    def flush(self):
        if not self.stmts:
            return
        script = ";\n".join(self.stmts)
        self.stmts = []
        self.db._exec_script(script)

    def latest(self, stage):
        found = None
        for row in self.rows:
            if row["stage"] != stage:
                continue
            if found is None or row["version"] >= found["version"]:
                found = row
        return found

    def runs(self, stage=None):
        rows = self.rows
        if stage:
            rows = [row for row in rows if row["stage"] == stage]
        return list(rows)

    def run(self, run_id):
        for row in self.rows:
            if row["id"] == run_id:
                return row
        return None

    def statuses(self):
        latest, order = {}, []
        for row in self.rows:
            if row["stage"] not in latest:
                order.append(row["stage"])
            latest[row["stage"]] = row["status"]
        return [{"id": sid, "status": latest[sid]} for sid in order]


class _JobScopeCtx:
    def __init__(self, db, job_id):
        self.db = db
        self.job_id = job_id
        self.owned = False
        self.scope = None

    def __enter__(self):
        current = self.db._scope
        if current is not None and current.job_id == self.job_id:
            self.owned = False
            self.scope = current
            return current
        scope = _JobScope(self.db, self.job_id)
        scope.load()
        self.db._scope = scope
        self.scope = scope
        self.owned = True
        return scope

    def __exit__(self, exc_type, exc, tb):
        if not self.owned:
            return False
        try:
            self.scope.flush()
        finally:
            if self.db._scope is self.scope:
                self.db._scope = None
        return False


class DatabaseNotConfigured(RuntimeError):
    """Vercel has no Turso/libSQL URL and STUDIO_SQLITE is not set. Do not open /tmp SQLite."""


class DatabaseBusy(RuntimeError):
    """Turso refused the call, or the call did not finish. The route should say so and stop."""


def remote_database_url():
    return os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL") or ""


def missing_on_vercel():
    """True when a Vercel instance would otherwise hide jobs in ephemeral SQLite."""
    if os.environ.get("VERCEL") != "1":
        return False
    if os.environ.get("STUDIO_SQLITE") == "1":
        return False
    return not remote_database_url()


def _open(path):
    """Turso/libSQL when a URL is set. Local SQLite otherwise, except on Vercel.

    On Vercel, /tmp SQLite disappears with the instance. Refuse to open it
    unless STUDIO_SQLITE=1 is set explicitly for a test.
    """
    url = remote_database_url()
    if url and os.environ.get("STUDIO_SQLITE") != "1":
        import libsql
        token = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN") or ""
        raw = libsql.connect(database=url, auth_token=token, _check_same_thread=False)
        return _LibsqlConn(raw)
    if missing_on_vercel():
        raise DatabaseNotConfigured("Database not configured")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


class DB:
    def __init__(self, path):
        self.path = path
        self.round_trips = 0
        self.latency_s = 0.0
        self.fail_statements = 0
        self.fail_scripts = 0
        self._scope = None
        self._tables = {}
        self.conn = None
        self.remote = _network_url(remote_database_url()) and os.environ.get("STUDIO_SQLITE") != "1"
        self._connect()

    def _connect(self):
        """Open the database. A remote open that sits past the call limit becomes DatabaseBusy."""
        if not self.remote:
            self.conn = _open(self.path)
            self.conn.executescript(SCHEMA)
            self.conn.commit()
            return
        last = None
        for attempt in (1, 2):
            box = {}

            def run():
                try:
                    conn = _open(self.path)
                    conn.executescript(SCHEMA)
                    if getattr(conn, "in_transaction", False):
                        conn.commit()
                    box["conn"] = conn
                except BaseException as exc:
                    box["e"] = exc

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            worker.join(_REMOTE_CALL_S)
            if worker.is_alive():
                last = TimeoutError("Hrana: http error: connection closed before message completed")
            elif "e" in box:
                last = box["e"]
            else:
                self.conn = box["conn"]
                return
            if attempt == 1 and (_capacity_error(last) or isinstance(last, TimeoutError)):
                time.sleep(0.35)
                continue
            break
        if _capacity_error(last) or isinstance(last, TimeoutError):
            raise DatabaseBusy("The board database is busy. Try again in a few seconds.") from last
        raise last

    def job_scope(self, job_id):
        """Hold this job's rows in memory and flush each stage's writes as one script."""
        return _JobScopeCtx(self, job_id)

    def _reopen(self):
        old = self.conn
        self.conn = None
        threading.Thread(target=_close_quietly, args=(old,), daemon=True).start()
        self.round_trips += 1
        if self.latency_s:
            time.sleep(self.latency_s)
        self._connect()

    def _call(self, op, script=False):
        self.round_trips += 1
        if script and self.fail_scripts:
            self.fail_scripts -= 1
            raise RuntimeError("Hrana: http error: connection closed before message completed")
        if self.fail_statements:
            self.fail_statements -= 1
            raise RuntimeError("Hrana: http error: connection closed before message completed")
        if self.latency_s:
            time.sleep(self.latency_s)
        conn = self.conn
        if not self.remote:
            return op(conn)
        box = {}

        def run():
            try:
                box["v"] = op(conn)
            except BaseException as exc:
                box["e"] = exc

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(_REMOTE_CALL_S)
        if worker.is_alive():
            # close() waits for the RPC that just timed out. Doing it here held the
            # process, and every other request on the instance, until the function died.
            threading.Thread(target=_close_quietly, args=(conn,), daemon=True).start()
            if self.conn is conn:
                self.conn = None
            raise TimeoutError("Hrana: http error: connection closed before message completed")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def _attempt(self, op, script=False):
        try:
            return self._call(op, script=script)
        except Exception as exc:
            if _capacity_error(exc):
                time.sleep(0.35)
                try:
                    return self._call(op, script=script)
                except Exception as again:
                    if _capacity_error(again) or _stale_error(again):
                        raise DatabaseBusy("The board database is busy. Try again in a few seconds.") from again
                    raise
            if not _stale_error(exc):
                raise
            self._reopen()
            return self._call(op, script=script)

    def _read_all(self, sql, args=()):
        with _lock:
            cur = self._attempt(lambda conn: conn.execute(sql, args))
            return [dict(row) for row in cur.fetchall()]

    def _read_one(self, sql, args=()):
        rows = self._read_all(sql, args)
        return rows[0] if rows else None

    def _exec_script(self, script):
        with _lock:
            self._attempt(lambda conn: conn.executescript(script), script=True)
            if getattr(self.conn, "in_transaction", False):
                self._attempt(lambda conn: conn.commit())

    def close(self):
        # Hold the same lock as queries. A background job can still be writing
        # when a test (or a shutdown) closes the runner; closing under the
        # connection is what used to segfault sqlite.
        with _lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

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
            if self._scope is not None:
                self._scope.buffer(sql, args)
                return _CursorShim()
            cur = self._attempt(lambda conn: conn.execute(sql, args))
            if getattr(self.conn, "in_transaction", True):
                self._attempt(lambda conn: conn.commit())
            return cur

    def q(self, sql, args=()):
        return self._read_all(sql, args)

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def has_table(self, name):
        if name in self._tables:
            return self._tables[name]
        found = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None
        self._tables[name] = found
        return found

    def release_expired_leases(self):
        """Drop compose leases past their expiry. Databases without the table have nothing to release."""
        if not self.has_table("leases"):
            return []
        stamp = now()
        rows = self.q("SELECT name, job_id FROM leases WHERE expires_at <= ?", (stamp,))
        if rows:
            self.x("DELETE FROM leases WHERE expires_at <= ?", (stamp,))
        return [{"name": r["name"], "job_id": r["job_id"]} for r in rows]

    # --- jobs
    def create_job(self, job_id, template, persona, brief, mode, batch_id=None):
        t = now()
        self.x("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
               (job_id, template, persona, json.dumps(brief), "created", mode, batch_id, t, t))

    def set_job_status(self, job_id, status):
        stamp = now()
        self.x("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, stamp, job_id))
        scope = self._scope
        if scope is not None and scope.job_id == job_id and scope.job is not None:
            scope.job["status"] = status
            scope.job["updated_at"] = stamp

    def job(self, job_id):
        scope = self._scope
        if scope is not None and scope.job_id == job_id:
            return scope.job
        j = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if j and "brief" not in j:
            j["brief"] = json.loads(j["brief_json"])
        return j

    def jobs(self):
        return self.q("SELECT * FROM jobs ORDER BY created_at DESC, id DESC")

    # --- stage runs
    def new_run(self, job_id, stage, mode, provider, model, price_key, prompt, params, inputs, est):
        scope = self._scope if self._scope is not None and self._scope.job_id == job_id else None
        if scope is not None:
            found = scope.latest(stage)
            v = (found["version"] if found else 0) + 1
        else:
            v = (self.one("SELECT MAX(version) v FROM stage_runs WHERE job_id=? AND stage=?",
                          (job_id, stage))["v"] or 0) + 1
        stamp = now()
        params_json, inputs_json = json.dumps(params), json.dumps(inputs)
        if scope is not None:
            rid = scope.next_run
            scope.next_run += 1
            self.x("""INSERT INTO stage_runs (id, job_id, stage, version, status, mode, provider, model,
                     price_key, prompt, params_json, inputs_json, est_cost_usd, started_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (rid, job_id, stage, v, "running", mode, provider, model, price_key, prompt,
                    params_json, inputs_json, est, stamp))
            scope.rows.append({
                "id": rid, "job_id": job_id, "stage": stage, "version": v, "status": "running",
                "mode": mode, "provider": provider, "model": model, "price_key": price_key,
                "prompt": prompt, "params_json": params_json, "inputs_json": inputs_json,
                "output_path": None, "meta_json": None, "est_cost_usd": est, "actual_cost_usd": None,
                "cost_basis": None, "attempts": 0, "error": None, "review_status": None,
                "review_note": None, "reviewed_at": None, "started_at": stamp, "finished_at": None,
            })
            return rid, v
        cur = self.x("""INSERT INTO stage_runs (job_id, stage, version, status, mode, provider, model,
                     price_key, prompt, params_json, inputs_json, est_cost_usd, started_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (job_id, stage, v, "running", mode, provider, model, price_key, prompt,
                      params_json, inputs_json, est, stamp))
        return cur.lastrowid, v

    def update_run(self, run_id, **kw):
        if "meta" in kw:
            kw["meta_json"] = json.dumps(kw.pop("meta"))
        if not kw:
            return
        sets = ", ".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE stage_runs SET {sets} WHERE id=?", (*kw.values(), run_id))
        scope = self._scope
        if scope is not None:
            row = scope.run(run_id)
            if row is not None:
                row.update(kw)

    def run(self, run_id):
        scope = self._scope
        if scope is not None:
            row = scope.run(run_id)
            if row is not None:
                return row
        return self.one("SELECT * FROM stage_runs WHERE id=?", (run_id,))

    def runs(self, job_id, stage=None):
        scope = self._scope
        if scope is not None and scope.job_id == job_id:
            return scope.runs(stage)
        if stage:
            return self.q("SELECT * FROM stage_runs WHERE job_id=? AND stage=? ORDER BY version", (job_id, stage))
        return self.q("SELECT * FROM stage_runs WHERE job_id=? ORDER BY stage, version", (job_id,))

    def latest(self, job_id, stage):
        scope = self._scope
        if scope is not None and scope.job_id == job_id:
            return scope.latest(stage)
        return self.one("SELECT * FROM stage_runs WHERE job_id=? AND stage=? ORDER BY version DESC LIMIT 1",
                        (job_id, stage))

    # --- attempts / spend
    def add_attempt(self, **kw):
        scope = self._scope
        if scope is not None:
            aid = scope.next_attempt
            scope.next_attempt += 1
            kw = {"id": aid, **kw}
        cols = ", ".join(kw)
        cur = self.x(f"INSERT INTO attempts ({cols}) VALUES ({','.join('?'*len(kw))})", tuple(kw.values()))
        if scope is not None:
            return kw["id"]
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
        passed = s.get("passed")
        passed_i = None if passed is None else int(bool(passed))
        self.x("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run["id"], run["job_id"], run["stage"], run["version"], persona, path, s.get("identity"),
                s.get("identity_min"), s.get("identity_threshold"), s.get("identity_note"), json.dumps(s.get("visual")),
                None, None, None, json.dumps(s.get("flags", [])), passed_i, now()))
        scope = self._scope
        if scope is not None:
            stored = dict(s)
            stored["flags"] = s.get("flags") or []
            scope.scores[run["id"]] = stored

    def score(self, stage_run_id):
        scope = self._scope
        if scope is not None and scope.run(stage_run_id) is not None:
            return scope.scores.get(stage_run_id)
        r = self.one("SELECT * FROM scores WHERE stage_run_id=?", (stage_run_id,))
        if r and "visual" not in r:
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

    # --- boards (graph JSON; Turso when that is the connection, SQLite otherwise)
    def save_board(self, board_id, name, graph, preset=None, last_job_id=None):
        t = now()
        existing = self.board(board_id)
        blob = json.dumps(graph)
        if existing:
            if preset is None:
                preset = existing["preset"]
            self.x("UPDATE boards SET name=?, preset=?, graph_json=?, updated_at=?, last_job_id=COALESCE(?, last_job_id) WHERE id=?",
                   (name, int(bool(preset)), blob, t, last_job_id, board_id))
        else:
            self.x("INSERT INTO boards (id, name, preset, graph_json, last_job_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                   (board_id, name, int(bool(preset)), blob, last_job_id, t, t))

    def board(self, board_id):
        row = self.one("SELECT * FROM boards WHERE id=?", (board_id,))
        if row:
            row["graph"] = json.loads(row["graph_json"] or "{}")
            row["preset"] = bool(row["preset"])
        return row

    def boards(self):
        rows = self.q("SELECT id, name, preset, last_job_id, created_at, updated_at FROM boards ORDER BY preset DESC, name")
        for row in rows:
            row["preset"] = bool(row["preset"])
        return rows

    def set_board_job(self, board_id, job_id):
        self.x("UPDATE boards SET last_job_id=?, updated_at=? WHERE id=?", (job_id, now(), board_id))

    # --- leases (one compose at a time across instances; Turso when that is the database)
    def acquire_lease(self, name, owner, job_id, ttl_s):
        """Take `name` unless another owner still holds an unexpired lease. True when this owner holds it."""
        exp = (dt.datetime.now().astimezone() + dt.timedelta(seconds=int(ttl_s))).isoformat(timespec="seconds")
        with _lock:
            self.conn.execute(
                """INSERT INTO leases (name, owner, job_id, expires_at) VALUES (?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     owner=excluded.owner, job_id=excluded.job_id, expires_at=excluded.expires_at
                   WHERE leases.expires_at <= ? OR leases.owner = excluded.owner""",
                (name, owner, job_id, exp, now()))
            self.conn.commit()
            row = self.conn.execute("SELECT owner FROM leases WHERE name=?", (name,)).fetchone()
        if row is None:
            return False
        got = row["owner"] if not isinstance(row, tuple) else row[0]
        return got == owner

    def lease(self, name):
        return self.one("SELECT * FROM leases WHERE name=?", (name,))

    def release_lease(self, name, owner):
        self.x("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))
