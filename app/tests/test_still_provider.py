"""Per-job still provider, and the still_only template (no LLM stages)."""
import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
          "HIGGSFIELD_API_KEY", "HIGGSFIELD_API_SECRET", "HF_KEY", "STUDIO_ALLOW_LIVE",
          "STUDIO_ONE_STAGE", "STUDIO_FAL_TOPPED_UP"):
    os.environ.pop(k, None)

from studio.config import load_config
from studio.runner import BriefError, Runner
from studio.web import create_app

TMP = Path(tempfile.mkdtemp())


def cfg():
    c = load_config()
    c["_data_dir"] = TMP / "still-provider"
    c["_db_file"] = c["_data_dir"] / "studio.db"
    c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
    return c


BRIEF = {"persona": "marco", "template": "talking_head", "script": "One short line."}


class StillProvider(unittest.TestCase):
    def tearDown(self):
        for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "STUDIO_FAL_TOPPED_UP", "STUDIO_ONE_STAGE"):
            os.environ.pop(k, None)

    def test_default_still_stays_fal(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job(dict(BRIEF, id="t-default"))
        job = r.db.job(jid)
        prov, sc = r.provider_for("still", job=job)
        self.assertEqual(prov.name, "mock")                 # dry run swaps the paid adapter
        self.assertEqual(sc["provider"], "fal")
        self.assertEqual(sc["price_key"], "fal_nano_banana_2")
        self.assertNotIn("still_provider", job["brief"])

    def test_still_provider_overrides_only_the_still(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job(dict(BRIEF, id="t-hf", still_provider="higgsfield"))
        job = r.db.job(jid)
        prov, sc = r.provider_for("still", job=job)
        self.assertEqual(prov.name, "mock")
        self.assertEqual((sc["provider"], sc["model"], sc["price_key"]),
                         ("higgsfield", "alibaba/qwen-image-3/edit", "hf_image"))
        voice = r.provider_for("voice", job=job)[1]
        self.assertEqual(voice["provider"], "elevenlabs")
        video = r.provider_for("video", job=job)[1]
        self.assertEqual(video["provider"], "fal")
        rows = r.estimate_job(dict(BRIEF, still_provider="higgsfield"))
        still = next(row for row in rows if row["stage"] == "still")
        self.assertEqual((still["provider"], still["model"], still["usd_per_unit"]),
                         ("higgsfield", "alibaba/qwen-image-3/edit", 0.094))
        other = next(row for row in rows if row["stage"] == "video")
        self.assertEqual(other["provider"], "fal")

    def test_explicit_fal_skips_the_lock_fallback(self):
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy")
        c = cfg()
        c["_data_dir"] = TMP / "locked"
        c["_db_file"] = c["_data_dir"] / "studio.db"
        opened = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(opened.close)
        opened.db.add_attempt(job_id="t-lock", stage="still", attempt=1, mode="live", provider="fal",
                              status="failed", error='fal 403: {"detail":"User is locked. Reason: Exhausted balance."}',
                              est_cost_usd=0.05, actual_cost_usd=0.0, started_at="2099-01-01T00:00:00+00:00")
        locked = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(locked.close)
        self.assertEqual(locked.provider_for("still")[0].name, "higgsfield")   # no job: fallback unchanged
        plain = locked.db.job(locked.create_job(dict(BRIEF, id="t-plain")))
        self.assertEqual(locked.provider_for("still", job=plain)[0].name, "higgsfield")
        chosen = locked.db.job(locked.create_job(dict(BRIEF, id="t-fal", template="still_only",
                                                      still_provider="fal")))
        prov, sc = locked.provider_for("still", job=chosen)
        self.assertEqual(prov.name, "fal")
        self.assertEqual(sc["price_key"], "fal_nano_banana_2")
        self.assertEqual(locked.provider_for("video", job=chosen)[1]["provider"], "higgsfield")

    def test_unknown_still_provider_is_rejected(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        with self.assertRaises(BriefError) as caught:
            r.create_job(dict(BRIEF, id="t-bad", still_provider="openai"))
        self.assertIn("still_provider", str(caught.exception))
        self.assertIsNone(r.db.job("t-bad"))


class StillOnly(unittest.TestCase):
    def test_template_is_brief_and_still_and_runs_without_an_llm_key(self):
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertIsNone(os.environ.get(k))
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        self.addCleanup(r.close)
        jid = r.create_job({"id": "t-still-only", "persona": "marco", "template": "still_only",
                            "script": "One still.", "setting": "a quiet room", "still_provider": "higgsfield"})
        defs = r.stage_defs(r.db.job(jid))
        self.assertEqual([d["id"] for d in defs], ["brief", "still"])
        self.assertNotIn("llm", {d["kind"] for d in defs})
        self.assertEqual(r.run_job(jid), "done")
        run = r.db.latest(jid, "still")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["provider"], "mock (dry-run of higgsfield)")
        self.assertEqual(run["model"], "alibaba/qwen-image-3/edit")
        self.assertEqual(run["price_key"], "hf_image")
        self.assertIsNone(r.db.latest(jid, "voice"))
        self.assertIsNone(r.db.latest(jid, "script"))

    def test_api_accepts_template_and_still_provider(self):
        os.environ["STUDIO_ONE_STAGE"] = "1"
        self.addCleanup(os.environ.pop, "STUDIO_ONE_STAGE", None)
        c = cfg()
        c["_data_dir"] = TMP / "api"
        c["_db_file"] = c["_data_dir"] / "studio.db"
        client = create_app(c).test_client()
        body = {"brief": {"id": "t-api", "persona": "marco", "template": "still_only",
                          "script": "One still.", "setting": "a quiet room", "still_provider": "higgsfield"}}
        res = client.post("/api/jobs", json=body)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.get_json()["id"], "t-api")
        detail = client.get("/api/jobs/t-api").get_json()
        self.assertEqual(detail["job"]["brief"]["still_provider"], "higgsfield")
        self.assertEqual(detail["job"]["template"], "still_only")
        still = next(s for s in detail["stages"] if s["id"] == "still")
        self.assertEqual(still["provider"], "higgsfield")
        self.assertEqual(still["model"], "alibaba/qwen-image-3/edit")
        self.assertEqual([s["id"] for s in detail["stages"]], ["brief", "still"])
        missing = client.post("/api/jobs", json={"brief": dict(body["brief"], id="t-api-bad", still_provider="midjourney")})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("still_provider", missing.get_json()["error"])
