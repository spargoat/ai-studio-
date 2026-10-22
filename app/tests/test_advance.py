"""One stage per request, fal queue polling, cron, and the live-run gate."""
import os, sys, json, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS", "STUDIO_ALLOW_LIVE", "STUDIO_ONE_STAGE", "CRON_SECRET"):
    os.environ.pop(k, None)

from studio.config import load_config
from studio.runner import Runner
from studio.providers.fal import FalProvider, FalPending
from studio.web import create_app

APP = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())


def cfg():
    c = load_config()
    c["_data_dir"] = TMP
    c["_db_file"] = TMP / "advance.db"
    c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
    return c


BRIEF = {"id": "t-adv", "persona": "marco", "template": "talking_head", "script": "Quick test line."}


class T(unittest.TestCase):
    def tearDown(self):
        for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "STUDIO_ALLOW_LIVE", "STUDIO_ONE_STAGE",
                  "STUDIO_FAL_TOPPED_UP", "CRON_SECRET"):
            os.environ.pop(k, None)

    def test_one_stage_per_advance(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job(dict(BRIEF))
        seen = []
        for _ in range(6):
            before = len(r.db.runs(jid))
            step = r.advance(jid)
            seen.append(step["action"])
            self.assertLessEqual(len(r.db.runs(jid)) - before, 1)
            if r.db.job(jid)["status"] == "awaiting_review":
                break
        self.assertEqual(r.db.job(jid)["status"], "awaiting_review")
        self.assertIsNotNone(r.db.latest(jid, "still"))
        self.assertIsNotNone(r.db.latest(jid, "voice"))
        self.assertIsNone(r.db.latest(jid, "video"))          # still gate is not approved
        self.assertEqual(r.db.latest(jid, "still")["version"], 1)
        self.assertIn("stage", seen)

    def test_fal_poll_does_not_resubmit(self):
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy", STUDIO_FAL_TOPPED_UP="1")
        calls = {"run": 0, "wait": 0}

        def run(self, kind, ctx):
            calls["run"] += 1
            raise FalPending("req-1", "https://queue.fal.run/status", "https://queue.fal.run/resp", "IN_QUEUE")

        def wait(self, rid, status_url, resp_url, timeout=None):
            calls["wait"] += 1
            return {"images": [{"url": "https://example.invalid/still.png"}]}

        def save(self, kind, res, out_dir):
            p = Path(out_dir) / "still.png"
            p.write_bytes(b"x" * 80)
            return {"output_path": str(p), "meta": {"url": "https://abc.public.blob.vercel-storage.com/still.png"},
                    "units_actual": 1, "request_id": "req-1"}

        orig = (FalProvider.run, FalProvider._wait, FalProvider.save_result)
        FalProvider.run, FalProvider._wait, FalProvider.save_result = run, wait, save
        self.addCleanup(lambda: setattr(FalProvider, "run", orig[0]))
        self.addCleanup(lambda: setattr(FalProvider, "_wait", orig[1]))
        self.addCleanup(lambda: setattr(FalProvider, "save_result", orig[2]))
        r = Runner(cfg(), live=True, log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job(dict(BRIEF, id="t-fal"))
        self.assertEqual(r.advance(jid)["stage"], "brief")
        step = r.advance(jid)
        self.assertEqual(step["pending"], True)
        self.assertEqual(r.db.latest(jid, "still")["status"], "running")
        self.assertEqual(calls["run"], 1)
        step = r.advance(jid)
        self.assertEqual(step["action"], "poll")
        self.assertEqual(calls["run"], 1)                     # poll did not submit again
        self.assertEqual(calls["wait"], 1)
        self.assertEqual(r.db.latest(jid, "still")["status"], "succeeded")
        self.assertEqual(r.db.latest(jid, "still")["version"], 1)

    def test_api_advance_cron_and_live_gate(self):
        app = create_app(cfg())
        c = app.test_client()
        self.assertEqual(c.post("/api/jobs", json={"brief": BRIEF, "mode": "live"}).status_code, 409)
        os.environ["STUDIO_ONE_STAGE"] = "1"
        res = c.post("/api/jobs", json={"brief": dict(BRIEF, id="t-api")})
        self.assertEqual(res.status_code, 201, res.data)
        jid = res.get_json()["id"]
        self.assertEqual(c.get(f"/api/jobs/{jid}").get_json()["job"]["status"], "created")
        step = c.post(f"/api/jobs/{jid}/advance")
        self.assertEqual(step.status_code, 200)
        self.assertEqual(step.get_json()["stage"], "brief")
        self.assertEqual(c.get("/api/cron/advance").status_code, 503)
        os.environ["CRON_SECRET"] = "cron-test"
        self.assertEqual(c.get("/api/cron/advance").status_code, 401)
        self.assertEqual(c.get("/api/cron/advance", headers={"Authorization": "Bearer cron-test"}).status_code, 200)
        os.environ["STUDIO_ALLOW_LIVE"] = "1"
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy", STUDIO_FAL_TOPPED_UP="1")
        live = c.post("/api/jobs", json={"brief": dict(BRIEF, id="t-live"), "mode": "live"})
        self.assertEqual(live.status_code, 201, live.data)
        self.assertEqual(c.get("/api/jobs/t-live").get_json()["job"]["mode"], "live")
        self.assertEqual(c.get("/api/jobs/t-live").get_json()["job"]["status"], "created")
        self.assertEqual(c.post("/api/jobs/t-live/advance").status_code, 200)
        os.environ.pop("STUDIO_ALLOW_LIVE")
        self.assertEqual(c.post("/api/jobs/t-live/advance").status_code, 409)
        redir = c.get("/file?p=https://evil.example/secret.mp4")
        self.assertNotEqual(redir.status_code, 302)
        ok = c.get("/file?p=https://abc.public.blob.vercel-storage.com/jobs/a.mp4")
        self.assertEqual(ok.status_code, 302)
        self.assertIn("blob.vercel-storage.com", ok.headers["Location"])

    def test_vercel_without_database_url_fails_closed(self):
        from studio.db import DB, DatabaseNotConfigured
        keys = ("VERCEL", "TURSO_DATABASE_URL", "LIBSQL_URL", "STUDIO_SQLITE", "CRON_SECRET")
        saved = {k: os.environ.get(k) for k in keys}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k in keys:
            os.environ.pop(k, None)
        os.environ["VERCEL"] = "1"
        path = TMP / "vercel-missing.db"
        with self.assertRaises(DatabaseNotConfigured):
            DB(path)
        self.assertFalse(path.exists())
        c = cfg()
        c["_db_file"] = path
        client = create_app(c).test_client()
        for route in ("/", "/api/jobs", "/api/meta", "/classic"):
            res = client.get(route)
            self.assertEqual(res.status_code, 503, route)
            self.assertIn("Database not configured", res.get_data(as_text=True))
        self.assertEqual(client.post("/api/jobs", json={"brief": BRIEF}).status_code, 503)
        os.environ["CRON_SECRET"] = "cron-test"
        cron = client.get("/api/cron/advance", headers={"Authorization": "Bearer cron-test"})
        self.assertEqual(cron.status_code, 503)
        self.assertIn("Database not configured", cron.get_data(as_text=True))
        self.assertNotIn("advanced", cron.get_data(as_text=True))
        self.assertFalse(path.exists())
        self.assertEqual(client.get("/healthz").status_code, 200)
        asset = client.get("/assets/index-BusMjKwX.js")
        self.assertEqual(asset.status_code, 200, asset.data[:120])
        self.assertIn("javascript", asset.content_type)
        os.environ["STUDIO_SQLITE"] = "1"
        client = create_app(c).test_client()
        self.assertEqual(client.get("/api/jobs").status_code, 200)
        cron = client.get("/api/cron/advance", headers={"Authorization": "Bearer cron-test"})
        self.assertEqual(cron.status_code, 200)
        self.assertEqual(cron.get_json()["advanced"], [])
        self.assertTrue(path.exists())

    def test_blob_upload_failure_marks_job_failed(self):
        from unittest.mock import patch
        from studio import storage
        os.environ["BLOB_READ_WRITE_TOKEN"] = "vercel_blob_rw_abc123_secret"
        os.environ["STUDIO_ONE_STAGE"] = "1"
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)
        body = '{"error":{"code":"bad_request","message":"Invalid pathname"}}'

        def boom(pathname, data, content_type, session=None):
            raise storage.BlobError(f"Blob upload failed (400): {body}")

        app = create_app(cfg())
        client = app.test_client()
        with patch("studio.storage.put_file", boom):
            created = client.post("/api/jobs", json={"brief": dict(BRIEF, id="t-blob")})
            self.assertEqual(created.status_code, 201, created.data)
            step = client.post("/api/jobs/t-blob/advance")
        self.assertEqual(step.status_code, 200, step.data)
        self.assertNotIn(b"Traceback", step.data)
        payload = step.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "brief")
        self.assertEqual(payload["status"], "failed")
        self.assertIn("Invalid pathname", payload["error"])
        self.assertIn("bad_request", payload["error"])
        detail = client.get("/api/jobs/t-blob").get_json()
        self.assertEqual(detail["job"]["status"], "failed")
        brief = next(s for s in detail["stages"] if s["id"] == "brief")
        self.assertEqual(brief["runs"][-1]["status"], "failed")
        self.assertIn("Invalid pathname", brief["runs"][-1]["error"])

    def test_failed_stage_is_not_advanced_again(self):
        from studio.providers.base import ProviderError
        from studio.providers.mock import MockProvider
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job(dict(BRIEF, id="t-stop"))
        self.assertEqual(r.advance(jid)["stage"], "brief")
        orig = MockProvider.do_image

        def boom(self, ctx):
            raise ProviderError("Fontconfig error: Cannot find a valid font for the family Sans")

        MockProvider.do_image = boom
        self.addCleanup(setattr, MockProvider, "do_image", orig)
        step = r.advance(jid)
        self.assertFalse(step["ok"])
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["stage"], "still")
        self.assertIn("family Sans", step["error"])
        self.assertEqual(r.db.latest(jid, "still")["version"], 1)
        r.db.set_job_status(jid, "partial")
        again = r.advance(jid)
        self.assertEqual(again["action"], "stopped")
        self.assertFalse(again["ok"])
        self.assertEqual(again["status"], "failed")
        self.assertIn("family Sans", again["error"])
        self.assertEqual(len(r.db.runs(jid, "still")), 1)
        os.environ["CRON_SECRET"] = "cron-test"
        os.environ["STUDIO_ONE_STAGE"] = "1"
        cron = create_app(cfg()).test_client().get(
            "/api/cron/advance", headers={"Authorization": "Bearer cron-test"})
        self.assertEqual(cron.status_code, 200, cron.data)
        self.assertNotIn(jid, [row["id"] for row in cron.get_json()["advanced"]])
        MockProvider.do_image = orig
        retried = r.advance(jid, only="still")
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(r.db.latest(jid, "still")["status"], "succeeded")
        self.assertEqual(r.db.latest(jid, "still")["version"], 2)

    def test_hashed_ui_assets_are_served_without_a_session(self):
        os.environ["STUDIO_APP_PASSWORD"] = "pw"
        os.environ["STUDIO_SESSION_SECRET"] = "secret"
        os.environ["STUDIO_REQUIRE_AUTH"] = "1"
        for key in ("STUDIO_APP_PASSWORD", "STUDIO_SESSION_SECRET", "STUDIO_REQUIRE_AUTH"):
            self.addCleanup(os.environ.pop, key, None)
        client = create_app(cfg()).test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/login", page.headers["Location"])
        js = client.get("/assets/index-BusMjKwX.js")
        self.assertEqual(js.status_code, 200, js.data[:200])
        self.assertIn("javascript", js.content_type)
        self.assertGreater(len(js.data), 1000)
        self.assertNotIn(b"<html", js.data[:80].lower())
        css = client.get("/assets/index-3cY9phPo.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("css", css.content_type)
        missing = client.get("/assets/missing-hash.js")
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn("Location", missing.headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
