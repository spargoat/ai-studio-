"""Board slice 1. One ad runs Ad Feed → Breakdown and Funnel → Teardown → Brief →
Script → Voiceover → Video in dry run. Actual spend stays $0. Intel reads are fixture
data unless both tracker env vars are set, and the fixture is never labelled live.
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS", "ADINTEL_API_URL", "ADINTEL_API_SECRET",
          "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)

from studio.adintel import FIXTURE_LABEL, LONGEVITY_NOTE, REFERENCE_LABEL, query_ads
from studio.board import BoardError, compile_board, preview_pack, talking_head_preset, timeline_artefact as board_timeline
from studio.config import connection_status, load_config
from studio.runner import Runner, SpendGuardError
from studio.providers import fal, elevenlabs
from studio.web import create_app

def _trip(*a, **k):
    raise AssertionError("TRIPWIRE: a paid provider was called in a test")
fal.FalProvider.run = _trip
elevenlabs.ElevenLabsProvider.run = _trip

TMP = Path(tempfile.mkdtemp(prefix="board-"))

def cfg():
    c = load_config()
    c["_data_dir"] = TMP
    c["_db_file"] = TMP / "studio.db"
    return c


def _json(path):
    return json.loads(Path(path).read_text())


class T(unittest.TestCase):
    def setUp(self):
        self._runners = []
        orig = Runner.__init__
        test = self
        def init(self_, *a, **k):
            orig(self_, *a, **k)
            test._runners.append(self_)
        Runner.__init__ = init
        self._orig = orig

    def tearDown(self):
        Runner.__init__ = self._orig
        for r in self._runners:
            r.close()

    def test_compile_wires_funnel_and_breakdown_into_teardown(self):
        stages = compile_board(talking_head_preset())
        by = {s["id"]: s for s in stages}
        self.assertIn("ad_breakdown", by["teardown"]["needs"])
        self.assertIn("funnel", by["teardown"]["needs"])
        self.assertEqual(by["teardown"]["port_of"]["ad_breakdown"], "ad")
        self.assertEqual(by["teardown"]["port_of"]["funnel"], "page")
        self.assertTrue(by["script"]["review"])
        self.assertTrue(by["voice"]["review"])
        self.assertEqual(by["video"]["needs"], ["voice"])
        order = [s["id"] for s in stages]
        self.assertLess(order.index("ad_feed"), order.index("ad_breakdown"))
        self.assertLess(order.index("ad_breakdown"), order.index("teardown"))
        self.assertLess(order.index("funnel"), order.index("teardown"))
        self.assertLess(order.index("teardown"), order.index("brief"))
        self.assertLess(order.index("script"), order.index("voice"))
        self.assertLess(order.index("voice"), order.index("video"))

    def test_ports_reject_a_mismatch(self):
        graph = talking_head_preset()
        graph["edges"].append({"id": "bad", "source": "ad_feed", "sourceHandle": "ad", "target": "voice", "targetHandle": "script"})
        with self.assertRaises(BoardError):
            compile_board(graph)

    def test_disabled_breakdown_passes_the_ad_through(self):
        graph = talking_head_preset()
        for node in graph["nodes"]:
            if node["id"] == "ad_breakdown":
                node["enabled"] = False
        stages = compile_board(graph)
        by = {s["id"]: s for s in stages}
        self.assertNotIn("ad_breakdown", by)
        self.assertIn("ad_feed", by["teardown"]["needs"])
        self.assertIn("funnel", by["teardown"]["needs"])

    def test_fixture_is_never_live(self):
        pack = query_ads({"advertiser": "Northline", "status": "live"})
        self.assertEqual(pack["origin"], "fixture")
        self.assertIs(pack["live"], False)
        self.assertIn("Fixture", pack["label"])
        self.assertNotEqual(pack["source"], "ads-tracker")
        self.assertTrue(pack["ads"])
        preview = preview_pack({"advertiser": "Northline Markets", "status": "live", "ad_id": "rec_4c91e2",
                                "event_id": "ev_launch_northline"})
        for key in ("feed", "breakdown", "funnel", "teardown", "timeline"):
            evidence = preview[key]["evidence"]
            self.assertEqual(evidence["origin"], "fixture", key)
            self.assertIs(evidence["live"], False, key)
            self.assertIn("data_as_of", evidence)
            self.assertIn("source", evidence)
            self.assertIn("inference", preview[key])
            self.assertNotEqual(set(preview[key]["evidence"]), set(preview[key]["inference"]))
        self.assertEqual(preview["breakdown"]["inference"]["longevity_note"], LONGEVITY_NOTE)
        self.assertEqual(preview["teardown"]["label"], REFERENCE_LABEL)
        text = json.dumps(preview["teardown"])
        self.assertLess(text.index("pattern_summary"), text.index("interpretation"))
        self.assertLess(text.index("interpretation"), text.index("decode"))
        self.assertIs(preview["live"], False)

    def test_timeline_click_emits_that_ad(self):
        art = board_timeline({"event_id": "ev_removed_fieldnote"})
        self.assertEqual(art["emitted_ad_id"], "rec_77aa10")
        self.assertEqual(art["ad"]["status"], "removed")
        self.assertIs(art["evidence"]["live"], False)
        self.assertIn("points", art["evidence"]["fields"])
        self.assertTrue(art["inference"]["note"])
        self.assertNotIn("points", art["inference"])

    def test_live_client_uses_the_secret_and_does_not_fall_back(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        try:
            class Resp:
                status_code = 200
                def json(self):
                    return {"data_as_of": "2026-10-19", "ads": [{"id": "live1", "advertiser": "Northline Markets",
                             "status": "active", "headline": "From the API"}]}
            with patch("studio.adintel.requests.get", return_value=Resp()) as get:
                pack = query_ads({"advertiser": "Northline"})
            self.assertEqual(pack["origin"], "live")
            self.assertIs(pack["live"], True)
            self.assertEqual(pack["ads"][0]["id"], "live1")
            self.assertEqual(pack["ads"][0]["status"], "live")
            headers = get.call_args.kwargs["headers"]
            self.assertEqual(headers["Authorization"], "Bearer super-secret-value")
            self.assertNotIn("super-secret-value", json.dumps({k: v for k, v in pack.items() if k != "ads"}))
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_connections_mask_the_secret(self):
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        os.environ["ADINTEL_API_URL"] = "https://tracker.example/private"
        try:
            rows = connection_status(cfg())
            blob = json.dumps(rows)
            self.assertNotIn("super-secret-value", blob)
            self.assertNotIn("tracker.example", blob)
            secret = next(r for r in rows if r["id"] == "adintel_secret")
            self.assertTrue(secret["set"])
            self.assertEqual(secret["mask"], "••••••••")
        finally:
            os.environ.pop("ADINTEL_API_SECRET", None)
            os.environ.pop("ADINTEL_API_URL", None)

    def test_cap_is_refused_before_a_live_run(self):
        c = cfg()
        c["budget"] = {"global_cap_usd": 0.01, "per_job_cap_usd": 0.01, "daily_cap_usd": None}
        r = Runner(c, log=lambda m: None)
        graph = talking_head_preset()
        stages = compile_board(graph)
        from studio.board import job_brief
        total = sum(row["est_usd"] for row in r.estimate_job(job_brief(graph, stages)))
        self.assertGreater(total, 0.01)
        with self.assertRaises(SpendGuardError):
            r.check_budget("board-preview", total, live=True)

    def test_chain_dry_run_is_free(self):
        r = Runner(cfg(), log=lambda m: None)
        from studio.board import job_brief
        graph = talking_head_preset()
        stages = compile_board(graph)
        jid = r.create_job(job_brief(graph, stages, job_id="t-board"))
        self.assertEqual(r.db.job(jid)["mode"], "dry")

        def until_review():
            seen = []
            last = None
            for _ in range(16):
                last = r.advance(jid)
                seen.append(last.get("stage"))
                if last.get("ok") is False or last.get("status") == "awaiting_review" or last.get("action") == "idle":
                    return last, seen
            return last, seen

        last, seen = until_review()
        self.assertEqual(last.get("status"), "awaiting_review", (last, seen))
        self.assertEqual(r.db.latest(jid, "script")["review_status"], "pending")
        self.assertIsNone(r.db.latest(jid, "video"))
        r.review(jid, "script", "approved", "ok")
        last, seen = until_review()
        self.assertEqual(last.get("status"), "awaiting_review", (last, seen))
        self.assertEqual(r.db.latest(jid, "voice")["review_status"], "pending")
        self.assertIsNone(r.db.latest(jid, "video"))
        r.review(jid, "voice", "approved", "ok")
        last, _seen = until_review()
        self.assertEqual(last.get("status"), "done", last)
        for sid in ("timeline", "ad_feed", "ad_breakdown", "funnel", "teardown", "brief", "script", "voice", "video"):
            run = r.db.latest(jid, sid)
            self.assertIsNotNone(run, sid)
            self.assertEqual(run["status"], "succeeded", (sid, run.get("error")))
            self.assertEqual(run["actual_cost_usd"], 0)
            self.assertEqual(run["mode"], "dry")
        self.assertEqual(r.db.live_spend(), 0)
        video = Path(r.db.latest(jid, "video")["output_path"])
        self.assertGreater(video.stat().st_size, 1000)
        teardown = _json(r.db.latest(jid, "teardown")["output_path"])
        self.assertEqual(teardown["label"], REFERENCE_LABEL)
        self.assertIs(teardown["evidence"]["live"], False)
        self.assertEqual(teardown["evidence"]["label"], FIXTURE_LABEL)
        self.assertIn("pattern_summary", teardown)
        self.assertIn("inference", teardown)
        breakdown = _json(r.db.latest(jid, "ad_breakdown")["output_path"])
        self.assertEqual(breakdown["inference"]["longevity_note"], LONGEVITY_NOTE)
        self.assertIn("longevity_days", breakdown["evidence"]["fields"])
        self.assertNotIn("longevity_note", breakdown["evidence"]["fields"])
        funnel = _json(r.db.latest(jid, "funnel")["output_path"])
        self.assertEqual(funnel["type"], "page")
        self.assertTrue(funnel["evidence"]["fields"]["steps"])
        brief = _json(r.db.latest(jid, "brief")["output_path"])
        self.assertEqual(brief["brief"]["label"], REFERENCE_LABEL)
        self.assertLess(json.dumps(teardown).index("pattern_summary"), json.dumps(teardown).index("decode"))

    def test_http_save_load_and_one_dry_step(self):
        os.environ["STUDIO_ONE_STAGE"] = "1"
        secret = "super-secret-value"
        os.environ["ADINTEL_API_SECRET"] = secret
        try:
            app = create_app(cfg(), live=False)
            client = app.test_client()
            listed = client.get("/api/boards").get_json()
            self.assertTrue(any(b["id"] == "talking_head" for b in listed))
            saved = client.put("/api/boards/talking_head", json={"name": "Talking head", "graph": talking_head_preset()}).get_json()
            self.assertEqual(saved["id"], "talking_head")
            self.assertIn("ad_feed", {n["id"] for n in saved["graph"]["nodes"]})
            preview = client.get("/api/intel/preview?advertiser=Northline%20Markets&ad_id=rec_4c91e2").get_json()
            # Secret is set but the URL is not, so this stays a fixture.
            self.assertEqual(preview["origin"], "fixture")
            self.assertIs(preview["live"], False)
            body = client.get("/api/settings").get_data(as_text=True)
            self.assertNotIn(secret, body)
            rows = client.get("/api/settings").get_json()["connections"]
            secret_row = next(r for r in rows if r["id"] == "adintel_secret")
            self.assertEqual(secret_row["mask"], "••••••••")
            self.assertTrue(secret_row["set"])
            started = client.post("/api/boards/talking_head/run", json={})
            self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
            payload = started.get_json()
            self.assertEqual(payload["mode"], "dry")
            job = client.get(f"/api/jobs/{payload['id']}").get_json()
            self.assertEqual(job["job"]["mode"], "dry")
        finally:
            os.environ.pop("STUDIO_ONE_STAGE", None)
            os.environ.pop("ADINTEL_API_SECRET", None)


if __name__ == "__main__":
    unittest.main()
