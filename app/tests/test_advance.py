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


if __name__ == "__main__":
    unittest.main(verbosity=2)
