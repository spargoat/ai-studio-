"""Storage helper: local passthrough, Blob upload contract, and the libSQL connection."""
import os, sys, tempfile, unittest
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
        return Resp({"url": "https://abc123.public.blob.vercel-storage.com/jobs/a.png"})

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
        os.environ["BLOB_READ_WRITE_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)
        http = HTTP()
        url = storage.put_file("jobs/j/still/v1/still.png", b"png", "image/png", session=http)
        self.assertEqual(url, "https://abc123.public.blob.vercel-storage.com/jobs/a.png")
        self.assertEqual(http.calls[0][1]["params"]["pathname"], "jobs/j/still/v1/still.png")
        self.assertEqual(http.calls[0][0], storage.BLOB_PUT)
        headers = http.calls[0][1]["headers"]
        self.assertEqual(headers["authorization"], "Bearer test-token")
        self.assertEqual(headers["x-api-version"], "7")
        self.assertNotIn("test-token", url)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
