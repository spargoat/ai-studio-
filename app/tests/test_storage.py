"""Storage helper: local passthrough, Blob upload contract, and the libSQL connection."""
import os, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("LIBSQL_URL", None)

from studio import storage
from studio.db import DB


class Resp:
    def __init__(self, payload, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class HTTP:
    def __init__(self):
        self.calls = []

    def put(self, url, **kw):
        self.calls.append((url, kw))
        pathname = kw["params"]["pathname"]
        return Resp({
            "url": "https://abc123.public.blob.vercel-storage.com/" + pathname,
            "downloadUrl": "https://abc123.public.blob.vercel-storage.com/" + pathname + "?download=1",
            "pathname": pathname,
            "contentType": kw["headers"]["x-content-type"],
            "contentDisposition": "attachment; filename=\"still.png\"",
            "etag": "\"abc\"",
        })

    def get(self, url, timeout=None):
        self.calls.append(("GET", url))
        return Resp({}, content=b"hello-bytes")


class T(unittest.TestCase):
    def test_local_passthrough_without_blob(self):
        raw = Path(tempfile.mkdtemp()) / "clip.mp4"
        raw.write_bytes(b"mp4")
        self.assertFalse(storage.enabled())
        self.assertEqual(storage.local(str(raw)), str(raw))
        self.assertEqual(storage.publish_tree("jobs/x", raw.parent), {})
        self.assertFalse(storage.allowed_redirect(str(raw)))
        self.assertFalse(storage.allowed_redirect("https://evil.example/x.png"))
        self.assertFalse(storage.allowed_redirect("https://blob.vercel-storage.com.evil.example/x"))

    def test_blob_put_materialize_and_redirect(self):
        os.environ["BLOB_READ_WRITE_TOKEN"] = "vercel_blob_rw_abc123_secret"
        os.environ["STUDIO_TMP"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)
        self.addCleanup(os.environ.pop, "STUDIO_TMP", None)
        http = HTTP()
        url = storage.put_file("jobs/j/still/v1/still.png", b"png", "image/png", session=http)
        self.assertEqual(url, "https://abc123.public.blob.vercel-storage.com/jobs/j/still/v1/still.png")
        self.assertEqual(http.calls[0][1]["params"]["pathname"], "jobs/j/still/v1/still.png")
        self.assertEqual(http.calls[0][1]["data"], b"png")
        self.assertEqual(http.calls[0][0], storage.BLOB_PUT)
        headers = http.calls[0][1]["headers"]
        self.assertEqual(headers["authorization"], "Bearer vercel_blob_rw_abc123_secret")
        self.assertEqual(headers["x-api-version"], "13")
        self.assertEqual(headers["x-vercel-blob-store-id"], "abc123")
        self.assertEqual(headers["x-vercel-blob-access"], "public")
        self.assertEqual(headers["x-content-type"], "image/png")
        self.assertEqual(headers["x-add-random-suffix"], "0")
        self.assertEqual(headers["x-allow-overwrite"], "1")
        self.assertNotIn("secret", url)
        self.assertTrue(storage.allowed_redirect(url))
        self.assertTrue(storage.allowed_redirect("https://blob.vercel-storage.com/jobs/a.png"))
        self.assertFalse(storage.allowed_redirect("https://evil.example/still.png"))
        path = storage.local(url, getter=http)
        self.assertEqual(Path(path).read_bytes(), b"hello-bytes")
        self.assertEqual(storage.local(url, getter=http), path)          # cached, no second download
        self.assertEqual(sum(1 for c in http.calls if c[0] == "GET"), 1)
        tree = Path(tempfile.mkdtemp())
        (tree / "meta.json").write_text("{}")
        mapping = storage.publish_tree("jobs/j/brief/v1", tree, session=http)
        self.assertEqual(len(mapping), 1)
        self.assertTrue(next(iter(mapping.values())).startswith("https://"))

    def test_blob_error_includes_response_body(self):
        os.environ["BLOB_READ_WRITE_TOKEN"] = "vercel_blob_rw_abc123_secret"
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)
        payload = '{"error":{"code":"bad_request","message":"Invalid pathname"}}'

        class Bad:
            status_code = 400
            text = payload
            content = payload.encode()

            def raise_for_status(self):
                import requests
                raise requests.HTTPError("400 Client Error: Bad Request")

            def json(self):
                raise AssertionError("error responses are not parsed as success")

        class HTTPBad:
            def put(self, url, **kw):
                self.url, self.kw = url, kw
                return Bad()

        http = HTTPBad()
        with self.assertRaises(storage.BlobError) as caught:
            storage.put_file("jobs/j-1020/brief/v1/brief.json", b"{}", "application/json", session=http)
        self.assertIn("400", str(caught.exception))
        self.assertIn("bad_request", str(caught.exception))
        self.assertIn("Invalid pathname", str(caught.exception))
        self.assertEqual(http.url, storage.BLOB_PUT)
        self.assertEqual(http.kw["params"]["pathname"], "jobs/j-1020/brief/v1/brief.json")
        self.assertEqual(http.kw["headers"]["x-api-version"], "13")
        self.assertEqual(http.kw["headers"]["x-allow-overwrite"], "1")

    def test_libsql_connection_fallback(self):
        """Turso URL uses libSQL; with the env unset, DB stays on local SQLite."""
        d = Path(tempfile.mkdtemp())
        with DB(d / "local.db") as db:
            db.create_job("local-1", "talking_head", "marco", {"script": "hi"}, "dry")
            self.assertEqual(db.job("local-1")["persona"], "marco")
        url = str(d / "turso.db")
        os.environ["TURSO_DATABASE_URL"] = url
        self.addCleanup(os.environ.pop, "TURSO_DATABASE_URL", None)
        with DB(d / "ignored.db") as db:
            db.create_job("remote-1", "talking_head", "marco", {"script": "hi"}, "live")
            db.add_attempt(job_id="remote-1", stage="still", attempt=1, mode="live", provider="fal",
                           status="succeeded", est_cost_usd=1.5, actual_cost_usd=1.5,
                           started_at="2026-10-19T10:00:00+00:00")
            self.assertEqual(db.job("remote-1")["brief"]["script"], "hi")
            self.assertEqual(db.effective_spend(), 1.5)
            self.assertIsNone(db.job("local-1"))
        self.assertTrue((d / "local.db").exists())
        self.assertFalse((d / "ignored.db").exists())

    def test_capacity_error_does_not_open_another_connection(self):
        from studio import db as dbmod
        d = Path(tempfile.mkdtemp())
        database = DB(d / "cap.db")
        self.addCleanup(database.close)

        class Boom:
            def __init__(self):
                self.calls = 0

            def execute(self, sql, args=()):
                self.calls += 1
                raise ValueError("Hrana: stream error: Server database capacity temporarily exceeded, please try again later")

            def close(self):
                raise AssertionError("a capacity error must not close and reopen")

        boom = Boom()
        original = database.conn
        database.conn = boom
        database.remote = True
        try:
            with self.assertRaises(dbmod.DatabaseBusy) as caught:
                database.q("SELECT 1")
            self.assertIn("busy", str(caught.exception))
            self.assertEqual(boom.calls, 2)
            self.assertIs(database.conn, boom)
        finally:
            database.conn = original
        self.assertFalse(dbmod._stale_error(ValueError("Hrana: capacity temporarily exceeded")))
        self.assertTrue(dbmod._stale_error(TimeoutError("Hrana: http error: connection closed before message completed")))

    def test_timed_out_remote_call_does_not_wait_for_close(self):
        from studio import db as dbmod
        d = Path(tempfile.mkdtemp())
        database = DB(d / "slow.db")
        self.addCleanup(database.close)
        old = dbmod._REMOTE_CALL_S
        dbmod._REMOTE_CALL_S = 0.2
        self.addCleanup(setattr, dbmod, "_REMOTE_CALL_S", old)

        class Slow:
            def execute(self, sql, args=()):
                time.sleep(2)
                raise AssertionError("the call should already have been abandoned")

            def close(self):
                time.sleep(2)

        original = database.conn
        database.conn = Slow()
        database.remote = True
        try:
            started = time.time()
            with self.assertRaises(TimeoutError):
                database._call(lambda conn: conn.execute("SELECT 1"))
            self.assertLess(time.time() - started, 1.0)
        finally:
            database.conn = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
