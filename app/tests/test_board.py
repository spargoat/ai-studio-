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
          "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"):
    os.environ.pop(k, None)

import requests
from studio.adintel import FIXTURE_ADS, FIXTURE_LABEL, LONGEVITY_NOTE, REFERENCE_LABEL, AdIntelError, _get, clean_step_label, query_ads
from studio.board import (BoardError, compile_board, job_brief, migrate_talking_head_layout, preview_config, preview_pack,
                          talking_head_preset, timeline_artefact as board_timeline)
from studio.config import connection_status, load_config
from studio.runner import BriefError, Runner, SpendGuardError
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

    def test_canvas_objects_persist_without_becoming_run_stages(self):
        graph = talking_head_preset()
        graph["funnel"] = {"name": "VSL", "stage": "mapping", "sourceAdIds": ["rec_4c91e2"]}
        graph["nodes"].append({"id": "research", "type": "quick_link", "position": {"x": 1800, "y": 0},
                               "config": {"title": "Ad record", "url": "https://example.test/ad"}})
        graph["nodes"].append({"id": "vsl-kit", "type": "kit_master", "position": {"x": 2100, "y": 0},
                               "config": {"kit_id": "vsl", "source_ad_id": "rec_4c91e2",
                                          "stages": json.dumps([["Hook", "Evidence needed"]]), "completed": "[]"}})
        self.assertNotIn("research", {s["id"] for s in compile_board(graph)})
        self.assertNotIn("vsl-kit", {s["id"] for s in compile_board(graph)})
        from studio.db import DB
        db = DB(cfg()["_db_file"])
        db.save_board("test-funnel", "VSL", graph, preset=False)
        saved = db.board("test-funnel")["graph"]
        self.assertEqual(saved["funnel"]["sourceAdIds"], ["rec_4c91e2"])
        self.assertEqual(saved["nodes"][-2]["config"]["url"], "https://example.test/ad")
        self.assertEqual(saved["nodes"][-1]["config"]["source_ad_id"], "rec_4c91e2")

    def test_planning_handoff_wires_persist_without_becoming_run_dependencies(self):
        graph = talking_head_preset()
        for index in (1, 2):
            graph["nodes"].append({"id": f"plan-{index}", "type": "funnel_stage",
                                   "position": {"x": 1800 + index * 300, "y": 0},
                                   "config": {"title": f"Step {index}"}})
        graph["edges"].append({"id": "plan-handoff", "source": "plan-1", "sourceHandle": "flow",
                               "target": "plan-2", "targetHandle": "flow", "label": "qualified lead"})
        stages = compile_board(graph)
        self.assertNotIn("plan-1", {stage["id"] for stage in stages})
        self.assertNotIn("plan-2", {stage["id"] for stage in stages})
        from studio.db import DB
        db = DB(cfg()["_db_file"])
        db.save_board("test-map", "Map", graph, preset=False)
        self.assertEqual(db.board("test-map")["graph"]["edges"][-1]["label"], "qualified lead")

    def test_image_node_feeds_video_and_preserves_prompt(self):
        graph = talking_head_preset()
        graph["nodes"].append({"id": "still", "type": "still", "position": {"x": 1800, "y": 0},
                               "config": {"prompt": "Owned product still", "aspect_ratio": "4:5"}})
        graph["edges"].append({"id": "e-brief-still", "source": "brief", "sourceHandle": "brief",
                               "target": "still", "targetHandle": "brief"})
        graph["edges"].append({"id": "e-still-video", "source": "still", "sourceHandle": "still",
                               "target": "video", "targetHandle": "still"})
        by = {stage["id"]: stage for stage in compile_board(graph)}
        self.assertEqual(by["still"]["kind"], "image")
        self.assertEqual(by["still"]["prompt"], "Owned product still")
        self.assertEqual(by["still"]["params"]["aspect_ratio"], "4:5")
        self.assertEqual(by["video"]["input_alias"]["still"], "still")

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
                    return {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-19"},
                            "matched": 1, "count": 1, "limit": 40, "offset": 0,
                            "observed": {"ads": [{"id": "live1", "advertiser": "Northline Markets",
                                                 "status": "missing", "headline": "From the API", "daysRunning": 13,
                                                 "destinationUrl": "https://example.test/page", "libraryUrl": "https://example.test/ad"}]}}
            with patch("studio.adintel.requests.get", return_value=Resp()) as get:
                pack = query_ads({"advertiser": "Northline"})
            self.assertEqual(pack["origin"], "live")
            self.assertIs(pack["live"], True)
            self.assertEqual(pack["ads"][0]["id"], "live1")
            self.assertEqual(pack["ads"][0]["status"], "missing")
            self.assertEqual(pack["ads"][0]["longevity_days"], 13)
            self.assertEqual(pack["matched"], 1)
            self.assertEqual(get.call_args.args[0], "https://tracker.example/api/board/ads")
            headers = get.call_args.kwargs["headers"]
            self.assertEqual(headers["x-adintel-secret"], "super-secret-value")
            self.assertNotIn("super-secret-value", json.dumps({k: v for k, v in pack.items() if k != "ads"}))
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_live_funnel_route_preserves_observed_inferred_unknown(self):
        from studio.adintel import funnel_for
        os.environ["ADINTEL_API_URL"] = "https://tracker.example/api/board"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            status_code = 200
            def __init__(self, data): self.data = data
            def json(self): return self.data
        detail = {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-19"},
                  "observed": {"id": "a1", "status": "stopped", "assignedFunnel": "Funnel A", "destinationUrl": "https://example.test"}}
        route = {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-19"},
                 "funnel": {"id": "f1", "name": "Funnel A"},
                 "observed": {"storedSteps": [{"role": "landing", "title": "Page", "url": "https://example.test", "source": "route"}], "laneSteps": []},
                 "inferred": {"storedSteps": [], "laneSteps": [{"role": "booking", "title": "Likely booking"}]},
                 "unknown": {"storedSteps": [{"role": "thank_you", "title": "Unopened"}], "laneSteps": []}}
        try:
            with patch("studio.adintel.requests.get", side_effect=[Resp(detail), Resp(route)]) as get:
                result = funnel_for("a1")
            self.assertEqual([s["state"] for s in result["steps"]], ["observed", "inferred", "unknown"])
            self.assertEqual(result["funnel_id"], "f1")
            self.assertEqual(get.call_args.args[0], "https://tracker.example/api/board/funnels/Funnel%20A/route")
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_old_preset_ids_do_not_break_a_new_live_snapshot(self):
        from studio.adintel import AdIntelNotFound
        from studio.board import _pick_ad, timeline_artefact
        pack = {"ads": [{"id": "current", "headline": "Current ad"}]}
        with patch("studio.board.ad_by_id", side_effect=AdIntelNotFound("not found")):
            self.assertEqual(_pick_ad(pack, "rec_old_fixture")["id"], "current")
        live_timeline = {"origin": "live", "live": True, "source": "ads-tracker", "label": "Live tracker",
                         "data_as_of": None, "points": [], "events": [], "note": "No timeline API"}
        with patch("studio.board.market", return_value=live_timeline), patch("studio.board.ad_by_id", side_effect=AssertionError("must not read a stale marker")):
            art = timeline_artefact({"event_id": "old_event", "ad_id": "rec_old_fixture"})
        self.assertIsNone(art["ad"])
        self.assertEqual(art["inference"]["note"], "No timeline API")

    def test_feed_pagination_and_failed_live_read_do_not_use_fixtures(self):
        page = query_ads({"limit": "2", "offset": "1"})
        self.assertEqual(page["matched"], 5)
        self.assertEqual(len(page["ads"]), 2)
        self.assertEqual(page["ads"][0]["id"], FIXTURE_ADS[1]["id"])
        from studio.adintel import AdIntelError
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Failed:
            status_code = 500
        try:
            with patch("studio.adintel.requests.get", return_value=Failed()):
                with self.assertRaises(AdIntelError):
                    query_ads({"limit": "40"})
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_funnel_directory_reads_stored_funnels(self):
        from studio.adintel import directory
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            status_code = 200
            def json(self):
                return {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-21"},
                        "observed": {"funnels": [{"id": "f1", "name": "VSL", "ads": 8, "advertisers": ["Desk"]}]}}
        try:
            with patch("studio.adintel.requests.get", return_value=Resp()) as get:
                result = directory()
            self.assertEqual(result["funnels"][0]["ads"], 8)
            self.assertEqual(result["coverage"], "stored funnel directory")
            self.assertEqual(get.call_args.args[0], "https://tracker.example/api/board/funnels")
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
            graph = talking_head_preset()
            graph["funnel"] = {"name": "Northline VSL", "stage": "mapping", "sourceAdIds": ["rec_4c91e2"]}
            graph["nodes"].append({"id": "source-link", "type": "quick_link", "position": {"x": 1600, "y": 0},
                                   "config": {"title": "Source", "url": "https://example.test/ad"}})
            saved = client.put("/api/boards/talking_head", json={"name": "Talking head", "graph": graph}).get_json()
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
            loaded = client.get("/api/boards/talking_head").get_json()
            self.assertEqual(loaded["graph"]["funnel"]["name"], "Northline VSL")
            self.assertEqual(loaded["graph"]["nodes"][-1]["config"]["url"], "https://example.test/ad")
            route = client.get("/api/intel/ads/rec_4c91e2/funnel")
            self.assertEqual(route.status_code, 200)
            self.assertEqual(route.get_json()["origin"], "fixture")
            self.assertEqual(route.get_json()["steps"][2]["kind"], "landing")
            self.assertEqual(loaded["preview"]["origin"], "fixture")
            self.assertIn("Fixture", loaded["preview"]["label"])
            self.assertTrue(loaded["preview"]["timeline"]["evidence"]["fields"]["points"])
            import time
            started_at = time.perf_counter()
            started = client.post("/api/boards/talking_head/run", json={})
            first = time.perf_counter() - started_at
            self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
            payload = started.get_json()
            self.assertEqual(payload["mode"], "dry")
            self.assertNotIn("error", payload)
            # The first response has to come back while the canvas can still paint.
            # A budget cut returns pump so the client keeps advancing.
            self.assertLess(first, 20, payload)
            status = payload.get("status")
            guard = 0
            while payload.get("pump") and guard < 40:
                step = client.post(f"/api/jobs/{payload['id']}/advance", json={})
                self.assertEqual(step.status_code, 200, step.get_data(as_text=True))
                body = step.get_json()
                self.assertIsNot(body.get("ok"), False, body)
                status = body.get("status") or status
                if body.get("action") in ("idle", "stopped") or status in ("done", "failed", "awaiting_review", "blocked"):
                    break
                guard += 1
            elapsed = time.perf_counter() - started_at
            self.assertEqual(status, "done")
            self.assertLess(elapsed, 45, "a dry board should finish in seconds, not one encode per stage")
            done = client.get(f"/api/jobs/{payload['id']}").get_json()
            self.assertEqual(done["job"]["mode"], "dry")
            for sid in ("timeline", "ad_feed", "ad_breakdown", "funnel", "teardown", "brief", "script", "voice", "video"):
                self.assertEqual(_stage_status(done, sid), "succeeded", sid)
                run = _stage_run(done, sid)
                self.assertEqual(run["actual_cost_usd"], 0, sid)
                self.assertEqual(run["mode"], "dry")
            teardown = _json(_stage_run(done, "teardown")["output_path"])
            self.assertEqual(teardown["label"], REFERENCE_LABEL)
            self.assertEqual(teardown["evidence"]["label"], FIXTURE_LABEL)
            self.assertIs(teardown["evidence"]["live"], False)
            self.assertEqual(client.get(f"/api/jobs/{payload['id']}").get_json()["job"]["act"], 0)
        finally:
            os.environ.pop("STUDIO_ONE_STAGE", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_marker_keeps_the_series_and_passes_the_ad_downstream(self):
        pack = preview_pack({"advertiser": "Northline Markets", "status": "live", "ad_id": "rec_4c91e2",
                             "event_id": "ev_removed_fieldnote"})
        fields = pack["timeline"]["evidence"]["fields"]
        self.assertGreater(len(fields["points"]), 0)
        self.assertGreater(len(fields["events"]), 0)
        self.assertEqual(fields["emitted_event"]["id"], "ev_removed_fieldnote")
        self.assertEqual(pack["timeline"]["emitted_ad_id"], "rec_77aa10")
        self.assertEqual(pack["timeline"]["ad"]["headline"], "Seven checks")
        self.assertEqual(pack["feed"]["ad"]["id"], "rec_4c91e2")
        self.assertEqual(pack["breakdown"]["ad"]["id"], "rec_77aa10")
        self.assertEqual(pack["breakdown"]["ad"]["status"], "removed")
        self.assertEqual(pack["funnel"]["evidence"]["fields"]["ad_id"], "rec_77aa10")
        self.assertEqual(pack["teardown"]["ad"]["id"], "rec_77aa10")
        self.assertEqual(pack["teardown"]["ad"]["creative_family"], "curiosity-list")
        self.assertIn("Fixture", pack["label"])
        self.assertIs(pack["live"], False)
        self.assertIs(pack["breakdown"]["evidence"]["live"], False)

    def test_empty_preview_is_labelled_fixture(self):
        pack = preview_pack({})
        self.assertEqual(pack["origin"], "fixture")
        self.assertIn("Fixture", pack["label"])
        self.assertTrue(pack["feed"]["evidence"]["fields"]["ads"])
        self.assertTrue(pack["timeline"]["evidence"]["fields"]["points"])
        self.assertTrue(pack["breakdown"]["ad"])
        self.assertTrue(pack["teardown"]["pattern_summary"])
        graph = talking_head_preset()
        self.assertEqual(preview_config(graph)["event_id"], "ev_launch_northline")
        self.assertEqual(preview_config(graph)["ad_id"], "rec_4c91e2")

    def test_v3_layout_migrates_only_untouched_preset_positions(self):
        preset = talking_head_preset()
        pos = {n["id"]: n["position"] for n in preset["nodes"]}
        self.assertGreater(pos["brief"]["x"], pos["teardown"]["x"])
        self.assertGreater(pos["ad_breakdown"]["y"], pos["ad_feed"]["y"])
        self.assertGreater(pos["voice"]["x"], pos["brief"]["x"])
        self.assertIsNone(migrate_talking_head_layout(preset))
        old = json.loads(json.dumps(preset))
        slice1 = {
            "timeline": (0, 0), "ad_feed": (380, 0), "ad_breakdown": (380, 280), "funnel": (680, 280),
            "teardown": (380, 560), "brief": (0, 24), "script": (290, 24), "voice": (580, 24), "video": (870, 24),
        }
        for node in old["nodes"]:
            x, y = slice1[node["id"]]
            node["position"] = {"x": x, "y": y}
        migrated = migrate_talking_head_layout(old)
        self.assertEqual([e["id"] for e in migrated["edges"]], [e["id"] for e in old["edges"]])
        self.assertEqual(migrated["nodes"][0]["config"], old["nodes"][0]["config"])
        self.assertEqual(next(n["position"]["x"] for n in migrated["nodes"] if n["id"] == "brief"), pos["brief"]["x"])
        old["nodes"][0]["position"] = {"x": 12, "y": 12}
        self.assertIsNone(migrate_talking_head_layout(old))
        draft = json.loads(json.dumps(preset))
        draft_pos = {
            "timeline": (36, 28), "ad_feed": (36, 330), "funnel": (36, 560),
            "ad_breakdown": (36, 800), "teardown": (300, 800),
            "brief": (760, 40), "script": (760, 300), "voice": (1020, 40), "video": (1020, 320),
        }
        for node in draft["nodes"]:
            x, y = draft_pos[node["id"]]
            node["position"] = {"x": x, "y": y}
        self.assertEqual(migrate_talking_head_layout(draft)["nodes"][0]["position"]["y"], pos["timeline"]["y"])

    def test_settings_does_not_run_doctor(self):
        calls = []

        def fake_doctor(_cfg):
            calls.append("doctor")
            print("OK: live preflight would pass")
            return 0

        with patch("studio.cli.doctor", fake_doctor):
            app = create_app(cfg(), live=False)
            client = app.test_client()
            settings = client.get("/api/settings")
            self.assertEqual(settings.status_code, 200, settings.get_data(as_text=True))
            body = settings.get_json()
            self.assertIn("connections", body)
            self.assertEqual(calls, [])
            overview = client.get("/api/overview")
            self.assertEqual(overview.status_code, 200, overview.get_data(as_text=True))
            self.assertEqual(calls, ["doctor"])
            self.assertIn("doctor", overview.get_json())

    def test_account_waterfall_keeps_evidence_and_inference_apart(self):
        from studio.adintel import accounts
        from studio.board import feed_artefact
        pack = accounts()
        self.assertEqual(pack["origin"], "fixture")
        self.assertFalse(pack["live"])
        north = next(row for row in pack["accounts"] if row["advertiser"] == "Northline Markets")
        self.assertGreaterEqual(north["ads"], 3)
        art = feed_artefact({"advertiser": "Northline Markets", "status": "live"})
        rows = art["evidence"]["fields"]["waterfall"]
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(row["headline"] for row in rows))
        self.assertTrue(all("steps" not in row for row in rows))
        with patch("studio.board.funnel_for", side_effect=AssertionError("feed must not read each route")):
            self.assertEqual(len(feed_artefact({"advertiser": "Northline Markets"})["evidence"]["fields"]["waterfall"]), 3)
        self.assertNotIn("waterfall", art["inference"])
        preview = preview_pack({"advertiser": "Northline Markets", "status": "live", "ad_id": "rec_4c91e2",
                                "event_id": "ev_launch_northline"})
        evidence = preview["breakdown"]["evidence"]["fields"]["panels"]
        inference = preview["breakdown"]["inference"]["panels"]
        self.assertEqual([p["id"] for p in evidence], ["hook", "angle", "offer", "proof", "cta", "format"])
        self.assertEqual([p["id"] for p in inference], [p["id"] for p in evidence])
        self.assertIn("No offer", evidence[2]["text"])
        self.assertNotEqual(evidence[0]["text"], inference[0]["text"])
        self.assertEqual([p["id"] for p in preview["teardown"]["evidence"]["fields"]["panels"]], [p["id"] for p in evidence])

    def test_create_fields_flow_into_the_brief(self):
        from studio.board import job_brief, resolve_voice_id
        graph = talking_head_preset()
        for node in graph["nodes"]:
            if node["type"] == "brief":
                node["config"] = {"persona": "maksim", "offer": "a written stop", "angle": "name the invalidation",
                                  "length": "15", "platform": "tiktok"}
            elif node["type"] == "script":
                node["config"] = {"script": "Stop first.", "framework": "PAS"}
            elif node["type"] == "voice":
                node["config"] = {"voice": "Brian"}
            elif node["type"] == "video":
                node["config"] = {"aspect": "1:1", "format": "talking_head"}
        stages = compile_board(graph)
        brief = job_brief(graph, stages, "job")
        self.assertEqual(brief["persona"], "maksim")
        self.assertEqual(brief["script"], "Stop first.")
        self.assertEqual(brief["offer"], "a written stop")
        self.assertEqual(brief["length"], "15")
        self.assertEqual(brief["aspect"], "1:1")
        video = next(stage for stage in stages if stage["id"] == "video")
        self.assertEqual(video["params"].get("aspect"), "1:1")
        personas = load_config()["_personas"]
        self.assertEqual(resolve_voice_id(personas, "maksim", "Brian"), personas["marco"]["voice_id"])
        self.assertEqual(resolve_voice_id(personas, "marco", ""), personas["marco"]["voice_id"])

    def test_replicate_chain_compiles(self):
        graph = {"nodes": [
            {"id": "s1", "type": "funnel_step", "enabled": True, "config": {"n": "1", "kind": "ad", "label": "In-feed video"}},
            {"id": "s2", "type": "funnel_step", "enabled": True, "config": {"n": "2", "kind": "landing", "label": "Page"}},
            {"id": "brief", "type": "brief", "enabled": True, "config": {}},
            {"id": "landing", "type": "landing", "enabled": True, "config": {}},
        ], "edges": [
            {"id": "a", "source": "s1", "sourceHandle": "step", "target": "s2", "targetHandle": "step"},
            {"id": "b", "source": "s1", "sourceHandle": "step", "target": "brief", "targetHandle": "source"},
            {"id": "c", "source": "brief", "sourceHandle": "brief", "target": "landing", "targetHandle": "brief"},
            {"id": "d", "source": "s2", "sourceHandle": "step", "target": "landing", "targetHandle": "source"},
        ]}
        stages = {stage["id"]: stage for stage in compile_board(graph)}
        self.assertEqual(stages["landing"]["task"], "landing")
        self.assertIn("brief", stages["landing"]["needs"])
        self.assertEqual(stages["s1"]["task"], "step")

    def test_purpose_name_is_what_export_writes(self):
        from studio.db import DB
        from studio.export import export
        from studio import library
        from studio.names import purpose_name
        self.assertEqual(purpose_name("Marco Sterling", "compose", 2), "Marco Sterling – VSL v2 – 9x16 HD")
        root = TMP / "purpose"
        src = root / "job" / "compose" / "v2" / "out.mp4"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"dry")
        db = DB(TMP / "purpose.db")
        library.ensure(db)
        db.x("""INSERT INTO library (stage_run_id, job_id, stage, version, persona, template, mode, path, reason, added_at)
                VALUES (1, 'job', 'compose', 2, 'marco', 'talking_head', 'dry', ?, 'test', '2026-10-21')""", (str(src),))
        man, zipped = export(db, root / "bundle", ids=[1], zip_it=False)
        self.assertIsNone(zipped)
        item = man["items"][0]
        self.assertEqual(item["library_id"], 1)
        self.assertEqual(item["purpose"], "Marco Sterling – VSL v2 – 9x16 HD")
        folder = root / "bundle" / "Marco Sterling – VSL v2 – 9x16 HD"
        self.assertTrue(folder.is_dir(), item)
        self.assertTrue((folder / "Marco Sterling – VSL v2 – 9x16 HD.mp4").is_file())

    def test_spend_and_accounts_do_not_invent_live_numbers(self):
        app = create_app(cfg(), live=False)
        client = app.test_client()
        spend = client.get("/api/spend").get_json()
        self.assertEqual(spend["forecast"]["month"], 0)
        self.assertEqual(spend["higgsfield_fallback"], [])
        self.assertEqual(spend["top_apis"], [])
        self.assertIn("No live calls", spend["forecast"]["note"])
        accounts = client.get("/api/intel/accounts").get_json()
        self.assertEqual(accounts["origin"], "fixture")
        self.assertTrue(accounts["accounts"])
        pending = client.get("/api/library/pending")
        self.assertEqual(pending.status_code, 200)
        for row in pending.get_json():
            self.assertIn("purpose", row)
            self.assertEqual(row["library_id"], row["id"])

    def test_preset_feed_does_not_keep_the_fixture_window(self):
        feed = next(node for node in talking_head_preset()["nodes"] if node["id"] == "ad_feed")
        self.assertEqual(feed["config"]["status"], "")
        self.assertEqual(feed["config"]["date_from"], "")
        self.assertEqual(feed["config"]["date_to"], "")
        self.assertEqual(feed["config"]["longevity_min"], "")

    def test_live_account_query_pins_page_id(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            status_code = 200
            def json(self):
                return {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-22"},
                        "matched": 60, "limit": 40, "offset": 0,
                        "observed": {"ads": [{"id": "elio-1", "advertiser": "Elio Info", "pageId": "126",
                                              "status": "stopped", "headline": "A live ad"}]}}
        try:
            with patch("studio.adintel.requests.get", return_value=Resp()) as get:
                pack = query_ads({"advertiser": "Elio Info", "page_id": "126"})
            self.assertEqual(pack["matched"], 60)
            self.assertEqual(pack["ads"][0]["page_id"], "126")
            params = get.call_args.kwargs["params"]
            self.assertEqual(params["advertiser"], "Elio Info")
            self.assertEqual(params["pageId"], "126")
            self.assertNotIn("status", params)
            self.assertNotIn("min_days", params)
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_live_account_query_retries_without_page_id_on_400(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            def __init__(self, code, body=None):
                self.status_code = code
                self.body = body or {}
            def json(self):
                return self.body
        good = {"ok": True, "evidence": {"source": "sqlite"}, "matched": 1, "observed": {"ads": [
            {"id": "elio-1", "advertiser": "Elio Info", "status": "stopped"}]}}
        try:
            with patch("studio.adintel.requests.get", side_effect=[Resp(400), Resp(200, good)]) as get:
                pack = query_ads({"advertiser": "Elio Info", "page_id": "126"})
            self.assertEqual(pack["ads"][0]["id"], "elio-1")
            self.assertEqual(get.call_count, 2)
            self.assertNotIn("pageId", get.call_args_list[1].kwargs["params"])
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_route_steps_replace_raw_ad_ids(self):
        from studio.adintel import route_for_funnel
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            status_code = 200
            def json(self):
                return {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-22"},
                        "funnel": {"id": "rec1", "name": "Algo Structure Fx"},
                        "observed": {"storedSteps": [
                            {"role": "family", "title": "1005340982122322"},
                            {"role": "landing", "title": "See details", "url": "https://example.test/join"},
                        ], "laneSteps": []},
                        "inferred": {"storedSteps": [], "laneSteps": []},
                        "unknown": {"storedSteps": [], "laneSteps": []}}
        try:
            with patch("studio.adintel.requests.get", return_value=Resp()):
                route = route_for_funnel("rec1")
            self.assertEqual(route["funnel_name"], "Algo Structure Fx")
            self.assertEqual(route["steps"][0]["label"], "Ad creative")
            self.assertEqual(route["steps"][1]["label"], "See details")
            self.assertNotIn("1005340982122322", route["steps"][0]["label"])
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_board_reports_tracker_origin_without_a_preview(self):
        client = create_app(cfg()).test_client()
        board = client.get("/api/boards/talking_head")
        self.assertEqual(board.status_code, 200)
        body = board.get_json()
        self.assertEqual(body["intel_origin"], "fixture")
        self.assertEqual(body["preview"]["origin"], "fixture")
        self.assertEqual(client.get("/api/meta").get_json()["intel_origin"], "fixture")

    def test_dry_board_run_is_not_stopped_by_review_gates(self):
        c = cfg()
        c["_db_file"] = TMP / "drygate.db"
        os.environ["STUDIO_ONE_STAGE"] = "1"
        self.addCleanup(os.environ.pop, "STUDIO_ONE_STAGE", None)
        app = create_app(c)
        with patch("studio.runner.Runner.advance", return_value={"action": "idle", "status": "done", "ok": True}) as run:
            res = app.test_client().post("/api/boards/talking_head/run", json={"mode": "dry"})
        self.assertTrue(run.called)
        self.assertIs(res.get_json()["pump"], False)
        self.assertEqual(res.get_json()["status"], "done")
        self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
        jid = res.get_json()["id"]
        job = app.test_client().get(f"/api/jobs/{jid}").get_json()
        stages = job["job"]["brief"]["board_stages"]
        self.assertTrue(stages)
        self.assertTrue(all(stage.get("review") is False for stage in stages))

    def test_dry_run_error_is_json_and_a_zero_budget_returns_immediately(self):
        c = cfg()
        c["_db_file"] = TMP / "dryerr.db"
        os.environ["STUDIO_ONE_STAGE"] = "1"
        os.environ["STUDIO_DRY_BUDGET_S"] = "0"
        self.addCleanup(os.environ.pop, "STUDIO_ONE_STAGE", None)
        self.addCleanup(os.environ.pop, "STUDIO_DRY_BUDGET_S", None)
        app = create_app(c)
        client = app.test_client()
        import time
        started = time.perf_counter()
        early = client.post("/api/boards/talking_head/run", json={"mode": "dry"})
        self.assertLess(time.perf_counter() - started, 5)
        self.assertEqual(early.status_code, 201, early.get_data(as_text=True))
        self.assertIs(early.get_json()["pump"], True)
        self.assertIn(early.get_json()["status"], ("created", "running", "partial"))
        os.environ["STUDIO_DRY_BUDGET_S"] = "12"
        with patch("studio.runner.Runner.advance", return_value={"action": "stage", "ok": False, "error": "voice blew up", "status": "failed"}):
            failed = client.post("/api/boards/talking_head/run", json={"mode": "dry"})
        self.assertEqual(failed.status_code, 201)
        self.assertEqual(failed.get_json()["error"], "voice blew up")
        self.assertIs(failed.get_json()["pump"], False)
        with patch("studio.runner.Runner.advance", side_effect=RuntimeError("disk full")):
            blown = client.post("/api/boards/talking_head/run", json={"mode": "dry"})
        self.assertEqual(blown.status_code, 502)
        self.assertEqual(blown.get_json()["error"], "disk full")
        self.assertTrue(blown.get_json()["id"])

    def test_vercel_dry_run_does_not_download_ffmpeg(self):
        c = cfg()
        c["_db_file"] = TMP / "nofetch.db"
        os.environ["STUDIO_ONE_STAGE"] = "1"
        os.environ["STUDIO_FETCH_FFMPEG"] = "1"
        self.addCleanup(os.environ.pop, "STUDIO_ONE_STAGE", None)
        self.addCleanup(os.environ.pop, "STUDIO_FETCH_FFMPEG", None)
        app = create_app(c)
        client = app.test_client()
        with patch("studio.assets.ffmpeg_on_disk", return_value=None), \
             patch("studio.assets.cached_download", side_effect=AssertionError("ffmpeg download")):
            res = client.post("/api/boards/talking_head/run", json={"mode": "dry"})
            self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
            body = res.get_json()
            self.assertNotIn("error", body)
            status = body.get("status")
            guard = 0
            while body.get("pump") and guard < 40:
                step = client.post(f"/api/jobs/{body['id']}/advance", json={})
                self.assertEqual(step.status_code, 200, step.get_data(as_text=True))
                advanced = step.get_json()
                self.assertIsNot(advanced.get("ok"), False, advanced)
                status = advanced.get("status") or status
                if advanced.get("action") in ("idle", "stopped") or status in ("done", "failed", "awaiting_review"):
                    break
                guard += 1
        self.assertEqual(status, "done")
        done = client.get(f"/api/jobs/{body['id']}").get_json()
        for sid in ("voice", "video"):
            self.assertEqual(_stage_status(done, sid), "succeeded", sid)
            blob = Path(_stage_run(done, sid)["output_path"]).read_bytes()
            self.assertGreater(len(blob), 100)
            self.assertIn(b"ID3" if sid == "voice" else b"ftyp", blob[:64])

    def test_tracker_read_retries_a_502_and_times_out_in_the_open(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        self.addCleanup(os.environ.pop, "ADINTEL_API_URL", None)
        self.addCleanup(os.environ.pop, "ADINTEL_API_SECRET", None)

        class Resp:
            def __init__(self, code, body=None):
                self.status_code = code
                self._body = body or {"ok": True, "evidence": {}}

            def json(self):
                return self._body

        with patch("studio.adintel.requests.get", side_effect=[Resp(502), Resp(200, {"ok": True, "evidence": {"asOf": "2026-10-22"}})]) as get:
            body = _get("/ads")
        self.assertEqual(get.call_count, 2)
        self.assertLessEqual(get.call_args.kwargs["timeout"], 8)
        self.assertTrue(body["ok"])
        with patch("studio.adintel.requests.get", return_value=Resp(503)) as get:
            with self.assertRaises(AdIntelError) as ctx:
                _get("/ads")
        self.assertEqual(get.call_count, 2)
        self.assertIn("503", str(ctx.exception))
        with patch("studio.adintel.requests.get", side_effect=requests.Timeout("slow")) as get:
            with self.assertRaises(AdIntelError) as ctx:
                _get("/ads")
        self.assertEqual(get.call_count, 2)
        self.assertIn("timed out", str(ctx.exception))
        with patch("studio.adintel.requests.get", return_value=Resp(400)) as get:
            with self.assertRaises(AdIntelError) as ctx:
                _get("/ads")
        self.assertEqual(get.call_count, 1)
        self.assertIn("400", str(ctx.exception))
        with patch("studio.adintel.requests.get", return_value=Resp(404)) as get:
            with self.assertRaises(AdIntelError):
                _get("/ads")
        self.assertEqual(get.call_count, 1)

    def test_spend_read_fails_stuck_compose_jobs(self):
        import datetime as dt
        c = cfg()
        c["_db_file"] = TMP / "stuck.db"
        r = Runner(c, log=lambda m: None)
        old = (dt.datetime.now().astimezone() - dt.timedelta(days=2)).isoformat(timespec="seconds")
        ids = {}
        for jid in ("j-1020-183743-uho", "j-1020-183744-tfn"):
            r.db.create_job(jid, "talking_head", "marco",
                            {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
            rid, _ = r.db.new_run(jid, "compose", "dry", "ffmpeg", "compose", "local", "", {}, {}, 0.0)
            r.db.update_run(rid, started_at=old)
            r.db.set_job_status(jid, "running")
            r.db.add_attempt(stage_run_id=rid, job_id=jid, stage="compose", attempt=1, mode="dry",
                             provider="ffmpeg", model="compose", status="running", started_at=old)
            ids[jid] = rid
        fresh = "j-fresh-running"
        r.db.create_job(fresh, "talking_head", "marco",
                        {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
        fresh_id, _ = r.db.new_run(fresh, "compose", "dry", "ffmpeg", "compose", "local", "", {}, {}, 0.0)
        r.db.set_job_status(fresh, "running")
        r.db.add_attempt(stage_run_id=fresh_id, job_id=fresh, stage="compose", attempt=1, mode="dry",
                         provider="ffmpeg", model="compose", status="running", started_at=r.db.one("SELECT started_at FROM stage_runs WHERE id=?", (fresh_id,))["started_at"])
        orphan = "j-1020-orphan-ledger"
        r.db.create_job(orphan, "talking_head", "marco",
                        {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
        orphan_run, _ = r.db.new_run(orphan, "compose", "dry", "ffmpeg", "compose", "local", "", {}, {}, 0.0)
        r.db.update_run(orphan_run, status="failed", error="instance was killed", finished_at=old, started_at=old)
        r.db.set_job_status(orphan, "running")
        r.db.add_attempt(stage_run_id=0, job_id=orphan, stage="compose", attempt=1, mode="dry",
                         provider="ffmpeg", model="compose", status="running", started_at=old)
        linked = "j-1020-linked-failed"
        r.db.create_job(linked, "talking_head", "marco",
                        {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
        linked_run, _ = r.db.new_run(linked, "compose", "dry", "ffmpeg", "compose", "local", "", {}, {}, 0.0)
        r.db.update_run(linked_run, status="failed", error="instance was killed", finished_at=old, started_at=old)
        r.db.set_job_status(linked, "failed")
        r.db.add_attempt(stage_run_id=linked_run, job_id=linked, stage="compose", attempt=1, mode="dry",
                         provider="ffmpeg", model="compose", status="running", started_at=old)
        missing_id = "j-1020-null-ledger"
        r.db.create_job(missing_id, "talking_head", "marco",
                        {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
        r.db.set_job_status(missing_id, "failed")
        r.db.add_attempt(job_id=missing_id, stage="compose", attempt=1, mode="dry",
                         provider="ffmpeg", model="compose", status="running", started_at=old)
        fal_job = "j-fal-still-polling"
        r.db.create_job(fal_job, "talking_head", "marco",
                        {"persona": "marco", "template": "talking_head", "script": "Stop first."}, "dry")
        fal_run, _ = r.db.new_run(fal_job, "video", "dry", "fal", "kling", "local", "", {}, {}, 0.0)
        r.db.update_run(fal_run, started_at=old, meta={"fal": {"request_id": "keep"}})
        r.db.set_job_status(fal_job, "running")
        r.db.add_attempt(stage_run_id=fal_run, job_id=fal_job, stage="video", attempt=1, mode="dry",
                         provider="fal", model="kling", status="running", started_at=old)
        r.close()
        client = create_app(c).test_client()
        res = client.get("/api/spend")
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        check = Runner(c, log=lambda m: None)
        self.addCleanup(check.close)
        recent = {row["job_id"]: row for row in res.get_json()["recent"]}
        for jid, rid in ids.items():
            self.assertEqual(check.db.run(rid)["status"], "failed", jid)
            self.assertIn("timed out", check.db.run(rid)["error"])
            self.assertEqual(check.db.job(jid)["status"], "failed", jid)
            self.assertEqual(recent[jid]["status"], "failed")
        self.assertEqual(check.db.run(fresh_id)["status"], "running")
        self.assertEqual(check.db.job(fresh)["status"], "running")
        self.assertEqual(check.db.attempts(fresh_id)[0]["status"], "running")
        orphan_attempt = check.db.one("SELECT status, error FROM attempts WHERE job_id=?", (orphan,))
        self.assertEqual(orphan_attempt["status"], "failed", orphan_attempt)
        self.assertTrue(orphan_attempt["error"])
        linked_attempt = check.db.one("SELECT status, error FROM attempts WHERE job_id=?", (linked,))
        self.assertEqual(linked_attempt["status"], "failed", linked_attempt)
        self.assertIn("already failed", linked_attempt["error"])
        null_attempt = check.db.one("SELECT status, error FROM attempts WHERE job_id=?", (missing_id,))
        self.assertEqual(null_attempt["status"], "failed", null_attempt)
        self.assertTrue(null_attempt["error"])
        self.assertEqual(check.db.run(fal_run)["status"], "running")
        self.assertEqual(check.db.attempts(fal_run)[0]["status"], "running")

    def test_index_html_is_not_cached(self):
        res = create_app(cfg()).test_client().get("/board")
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])
        cache = res.headers["Cache-Control"]
        self.assertIn("no-cache", cache)
        self.assertIn("no-store", cache)
        self.assertIn("must-revalidate", cache)

    def test_funnel_step_labels_drop_css_and_long_titles(self):
        raw = ".logo-wordmark-brand_svg__st3{fill:#221d19} Eventbrite"
        self.assertEqual(clean_step_label(raw), "Eventbrite")
        self.assertEqual(clean_step_label(".a{color:red}.b{color:blue} Hello"), "Hello")
        long = "Buy tickets to the annual city conference and the workshop that follows it this weekend"
        short = clean_step_label(long)
        self.assertLessEqual(len(short), 48)
        self.assertTrue(short.endswith("…"))
        self.assertNotIn("{", short)

    def test_replicate_scaffold_dry_run_has_a_script(self):
        graph = {"nodes": [
            {"id": "s1", "type": "funnel_step", "enabled": True, "config": {"n": "1", "kind": "ad", "label": "In-feed video"}},
            {"id": "s2", "type": "funnel_step", "enabled": True, "config": {"n": "2", "kind": "landing", "label": "Ticket rule"}},
            {"id": "brief", "type": "brief", "enabled": True, "config": {"persona": "marco"}},
            {"id": "script", "type": "script", "enabled": True, "config": {}},
            {"id": "voice", "type": "voice", "enabled": True, "config": {}},
            {"id": "video", "type": "video", "enabled": True, "config": {}},
            {"id": "landing", "type": "landing", "enabled": True, "config": {}},
        ], "edges": [
            {"id": "a", "source": "s1", "sourceHandle": "step", "target": "brief", "targetHandle": "source"},
            {"id": "b", "source": "brief", "sourceHandle": "brief", "target": "script", "targetHandle": "brief"},
            {"id": "c", "source": "script", "sourceHandle": "script", "target": "voice", "targetHandle": "script"},
            {"id": "d", "source": "s2", "sourceHandle": "step", "target": "landing", "targetHandle": "source"},
            {"id": "e", "source": "brief", "sourceHandle": "brief", "target": "landing", "targetHandle": "brief"},
        ]}
        stages = compile_board(graph)
        brief = job_brief(graph, stages)
        self.assertIn("Ticket rule", brief["script"])
        self.assertIn("In-feed video", brief["script"])
        c = cfg()
        c["_db_file"] = TMP / "scaffold.db"
        runner = Runner(c, log=lambda m: None)
        self.addCleanup(runner.close)
        jid = runner.create_job(brief)
        self.assertTrue(runner.db.job(jid)["brief"]["script"].strip())
        with self.assertRaises(BriefError) as caught:
            runner.create_job({"persona": "marco", "template": "talking_head", "script": "  "})
        self.assertIn("brief.script is empty", str(caught.exception))

    def test_step_card_drops_css_from_a_stored_label(self):
        from studio.board import step_artefact
        raw = ".logo-wordmark-brand_svg__st3{fill:#221d19} Eventbrite"
        art = step_artefact({"n": "2", "kind": "landing", "label": raw, "url": "https://example.test/e"})
        self.assertEqual(art["page"]["title"], "Eventbrite")
        self.assertEqual(art["evidence"]["fields"]["label"], "Eventbrite")
        self.assertNotIn("{", art["page"]["title"])

    def test_clickthrough_layout_contracts(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "ui" / "src" / "index.css").read_text()
        nodes = (root / "ui" / "src" / "board" / "nodes.tsx").read_text()
        page = (root / "ui" / "src" / "pages" / "BoardPage.tsx").read_text()
        fall = css.split(".v3-fall {", 1)[1].split("}", 1)[0]
        self.assertIn("max-height: 280px", fall)
        self.assertIn("overflow-y: auto", fall)
        label = css.split(".palette-label {", 1)[1].split("}", 1)[0]
        self.assertNotIn("line-clamp", label)
        self.assertNotIn("ellipsis", label)
        self.assertIn("overflow: visible", label)
        self.assertIn(".v3-grid-kits { grid-template-columns: minmax(0, 1fr); }", css)
        card = css.split(".lib-card {", 1)[1].split("}", 1)[0]
        self.assertIn("height: auto", card)
        self.assertIn("align-self: start", card)
        split = css.split(".lib-split {", 1)[1].split("}", 1)[0]
        self.assertIn("align-items: start", split)
        self.assertIn('loading ? "Loading ads…"', nodes)
        self.assertIn('scope: "feed"', nodes)
        self.assertNotIn("keepPreviousData", nodes)
        self.assertIn("cleanStepLabel(config.label)", nodes)
        self.assertIn("isPending", page)
        self.assertNotIn("keepPreviousData", page)
        item = css.split(".v3-dir-item b, .v3-dir-item small {", 1)[1].split("}", 1)[0]
        self.assertIn("white-space: normal", item)
        self.assertIn("overflow-wrap: anywhere", item)
        self.assertNotIn("ellipsis", item)
        main = (root / "ui" / "src" / "main.tsx").read_text()
        router = (root / "ui" / "src" / "router.tsx").read_text()
        self.assertIn("vite:preloadError", main)
        self.assertIn("studio:chunk-reload", main)
        self.assertIn("preventDefault", main)
        self.assertNotIn("serviceWorker", main + router)
        self.assertIn("errorElement", router)
        api_src = (root / "ui" / "src" / "lib" / "api.ts").read_text()
        self.assertIn("Route read failed:", nodes)
        self.assertIn("Retrying route…", nodes)
        self.assertIn("Ad read failed:", nodes)
        self.assertIn("Retrying ads…", nodes)
        self.assertIn("Funnel read failed:", nodes)
        self.assertIn("Retrying funnel…", nodes)
        self.assertIn("Preview read failed:", page)
        self.assertIn("Retrying preview…", page)
        self.assertIn("Running…", page)
        self.assertIn("v3-alert", page)
        self.assertIn("setRunError", page)
        self.assertIn("timeoutMs: 25000", page)
        self.assertIn('draggable={type !== "funnel_route"}', page)
        self.assertIn('addNode("funnel_route", { funnel_id: f.id, funnel_name: f.name })', page)
        self.assertIn("Add this funnel as a Funnel Map block", page)
        self.assertNotIn("mapDirectoryFunnel", page)
        self.assertIn("Funnel Map block. Choose a funnel in the inspector", nodes)
        self.assertIn("pinnedBoards", page)
        self.assertIn("INTEL_TIMEOUT_MS", api_src)
        self.assertIn("Request timed out", api_src)
        self.assertIn(".v3-alert", css)

    def test_marco_face_sheet_is_a_placeholder_when_the_file_is_missing(self):
        client = create_app(cfg()).test_client()
        rows = client.get("/api/personas").get_json()
        marco = next(row for row in rows if row["id"] == "marco")
        ref = marco["refs"][0]
        if ref["exists"]:
            self.assertFalse(ref["placeholder"])
        else:
            self.assertTrue(ref["placeholder"])
            self.assertFalse(ref["exists"])

    def test_script_key_clears_when_the_gateway_is_available(self):
        from studio.pricing import cost
        c = cfg()
        self.assertEqual(c["stages"]["llm"]["model"], "openai/gpt-4o-mini")
        self.assertEqual(c["stages"]["llm"]["price_key"], "llm_gpt4o_mini")
        client = create_app(c).test_client()
        unset = next(row for row in client.get("/api/meta").get_json()["connections"] if row["id"] == "llm")
        self.assertFalse(unset["set"])
        os.environ["VERCEL_OIDC_TOKEN"] = "oidc-test"
        self.addCleanup(os.environ.pop, "VERCEL_OIDC_TOKEN", None)
        live = next(row for row in client.get("/api/meta").get_json()["connections"] if row["id"] == "llm")
        self.assertTrue(live["set"])
        self.assertEqual(live["env"], "VERCEL_OIDC_TOKEN")
        self.assertNotIn("oidc-test", json.dumps(live))
        os.environ["AI_GATEWAY_API_KEY"] = "gw-test"
        self.addCleanup(os.environ.pop, "AI_GATEWAY_API_KEY", None)
        preferred = next(row for row in client.get("/api/meta").get_json()["connections"] if row["id"] == "llm")
        self.assertEqual(preferred["env"], "AI_GATEWAY_API_KEY")
        spent = cost(c, "llm_gpt4o_mini", "script", 1, {"input_tokens": 1000, "output_tokens": 200})
        self.assertAlmostEqual(spent, 1000 * 0.00000015 + 200 * 0.0000006, places=6)

    def test_oidc_header_is_a_gateway_credential_and_is_never_returned(self):
        import subprocess
        from studio.providers.llm import LLMProvider
        token = "oidc-header-secret"
        c = cfg()
        client = create_app(c).test_client()
        res = client.get("/api/meta", headers={"x-vercel-oidc-token": token})
        body = res.get_data(as_text=True)
        self.assertNotIn(token, body)
        cache = res.headers["Cache-Control"]
        self.assertIn("no-store", cache)
        self.assertNotIn("public", cache.lower())
        self.assertEqual(res.headers["CDN-Cache-Control"], "no-store")
        row = next(item for item in res.get_json()["connections"] if item["id"] == "llm")
        self.assertTrue(row["set"])
        self.assertEqual(row["env"], "x-vercel-oidc-token")
        self.assertEqual(row["mask"], "••••••••")
        marco = next(item for item in res.get_json()["personas"] if item["id"] == "marco")
        self.assertEqual(marco["voice_name"], "Brian")
        self.assertNotIn("voice_id", marco)
        blank = client.get("/api/meta", headers={"x-vercel-oidc-token": "   "})
        self.assertFalse(next(item for item in blank.get_json()["connections"] if item["id"] == "llm")["set"])
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY", None)
        held = client.get("/api/meta", headers={"x-vercel-oidc-token": token})
        self.assertNotIn(token, held.get_data(as_text=True))
        self.assertEqual(next(item for item in held.get_json()["connections"] if item["id"] == "llm")["env"], "x-vercel-oidc-token")
        os.environ["AI_GATEWAY_API_KEY"] = "gw-test"
        self.addCleanup(os.environ.pop, "AI_GATEWAY_API_KEY", None)
        preferred = client.get("/api/settings", headers={"x-vercel-oidc-token": token})
        self.assertNotIn(token, preferred.get_data(as_text=True))
        self.assertEqual(next(item for item in preferred.get_json()["connections"] if item["id"] == "llm")["env"], "AI_GATEWAY_API_KEY")
        os.environ.pop("AI_GATEWAY_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        c["_db_file"] = TMP / f"oidc-{id(self)}.db"
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy", STUDIO_FAL_TOPPED_UP="1")
        for key in ("FAL_KEY", "ELEVENLABS_API_KEY", "STUDIO_FAL_TOPPED_UP"):
            self.addCleanup(os.environ.pop, key, None)
        seen = {}

        class Resp:
            status_code = 200
            text = ""
            def json(self):
                return {"id": "chatcmpl-oidc", "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                        "choices": [{"message": {"content": json.dumps({"script": "Leave the size."})}}]}

        def post(url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization")
            seen["model"] = (json or {}).get("model")
            return Resp()

        app = create_app(c)
        r = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(r.close)
        with app.test_request_context("/api/jobs/x/advance", headers={"x-vercel-oidc-token": token}):
            with patch("studio.providers.llm.requests.post", post):
                jid = r.create_job({"persona": "maksim", "template": "talking_head_scripted", "topic": "the stop"})
                self.assertEqual(r.advance(jid)["stage"], "brief")
                step = r.advance(jid)
        self.assertEqual(step["stage"], "script")
        self.assertTrue(step["ok"], step)
        self.assertEqual(seen["url"], "https://ai-gateway.vercel.sh/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {token}")
        self.assertEqual(seen["model"], "openai/gpt-4o-mini")
        run = r.db.latest(jid, "script")
        folder = Path(run["output_path"]).parent
        disk = "\n".join(path.read_text(errors="ignore") for path in folder.rglob("*") if path.is_file())
        self.assertNotIn(token, disk)
        self.assertNotIn(token, json.dumps(r.db.events(jid, 40)))
        self.assertNotIn(token, json.dumps({k: run[k] for k in run.keys()}))
        outside = LLMProvider("llm", c["providers"]["llm"])
        with self.assertRaises(Exception) as caught:
            outside.do_script({"stage": "script", "kind": "script", "model": "openai/gpt-4o-mini",
                               "prompt": "x", "params": {}, "out_dir": str(TMP)})
        self.assertIn("needs key", str(caught.exception))
        self.assertNotIn(token, str(caught.exception))

        ui = Path(__file__).resolve().parents[1] / "ui"
        proc = subprocess.run(["node", "src/board/inputs.test.ts"], cwd=ui, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_live_script_uses_the_gateway_and_logs_token_cost(self):
        from studio.providers.llm import LLMProvider
        c = cfg()
        c["_db_file"] = TMP / f"gateway-{id(self)}.db"
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy", AI_GATEWAY_API_KEY="gw-test",
                          STUDIO_FAL_TOPPED_UP="1")
        for key in ("FAL_KEY", "ELEVENLABS_API_KEY", "AI_GATEWAY_API_KEY", "STUDIO_FAL_TOPPED_UP", "VERCEL_OIDC_TOKEN"):
            self.addCleanup(os.environ.pop, key, None)
        seen = {}

        class Resp:
            status_code = 200
            text = ""
            def json(self):
                return {"id": "chatcmpl-test", "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
                        "choices": [{"message": {"content": json.dumps({
                            "framework": "PAS", "beats": {"Problem": "Name the stop."},
                            "script": "Decide the stop before you size the trade.", "hooks": ["Where are you wrong?"]})}}]}

        def post(url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["body"] = json
            seen["auth"] = (headers or {}).get("Authorization")
            return Resp()

        r = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(r.close)
        with patch("studio.providers.llm.requests.post", post):
            jid = r.create_job({"persona": "maksim", "template": "talking_head_scripted", "topic": "the stop"})
            self.assertEqual(r.advance(jid)["stage"], "brief")
            step = r.advance(jid)
        self.assertEqual(step["stage"], "script", step)
        self.assertTrue(step["ok"], step)
        self.assertEqual(seen["url"], "https://ai-gateway.vercel.sh/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer gw-test")
        self.assertEqual(seen["body"]["model"], "openai/gpt-4o-mini")
        run = r.db.latest(jid, "script")
        self.assertAlmostEqual(run["actual_cost_usd"], 0.00027, places=6)
        self.assertEqual(run["price_key"], "llm_gpt4o_mini")
        written = json.loads(Path(run["output_path"]).read_text())
        self.assertEqual(written["script"], "Decide the stop before you size the trade.")
        self.assertIsNone(r.db.latest(jid, "still"))
        os.environ.pop("AI_GATEWAY_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY", None)
        provider = LLMProvider("llm", c["providers"]["llm"])
        with patch("studio.providers.llm.requests.post", post):
            provider.do_script({"stage": "script", "kind": "script", "model": "openai/gpt-4o-mini",
                                "prompt": "Write it.", "params": {"task": "script", "framework": "PAS"},
                                "out_dir": str(TMP)})
        self.assertEqual(seen["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(seen["body"]["model"], "gpt-4o-mini")
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)

        class Ant:
            status_code = 200
            text = ""
            def json(self):
                return {"id": "msg-test", "usage": {"input_tokens": 10, "output_tokens": 4},
                        "content": [{"type": "text", "text": "{\"script\": \"Leave it.\"}"}]}

        def apost(url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["body"] = json
            seen["key_header"] = (headers or {}).get("x-api-key")
            return Ant()

        with patch("studio.providers.llm.requests.post", apost):
            saved = provider.do_script({"stage": "script", "kind": "script", "model": "openai/gpt-4o-mini",
                                        "prompt": "Write it.", "params": {"task": "script"}, "out_dir": str(TMP)})
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["body"]["model"], "claude-haiku-4-5")
        self.assertEqual(seen["key_header"], "ant-test")
        self.assertEqual(saved["price_key"], "llm_claude_haiku_4_5")
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"):
            os.environ.pop(key, None)
        with self.assertRaises(Exception) as caught:
            provider.do_script({"stage": "script", "kind": "script", "model": "openai/gpt-4o-mini",
                                "prompt": "x", "params": {}, "out_dir": str(TMP)})
        self.assertIn("needs key", str(caught.exception))

    def test_feed_scope_is_only_the_waterfall(self):
        client = create_app(cfg()).test_client()
        res = client.get("/api/intel/preview?advertiser=Northline%20Markets&page_id=&scope=feed")
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertEqual(body["origin"], "fixture")
        self.assertIn("feed", body)
        self.assertNotIn("breakdown", body)
        self.assertNotIn("timeline", body)
        self.assertGreater(body["feed"]["evidence"]["fields"]["matched"], 0)
        cache = res.headers["Cache-Control"]
        self.assertIn("no-store", cache)
        self.assertNotIn("public", cache.lower())
        self.assertEqual(res.headers["CDN-Cache-Control"], "no-store")

    def test_mixed_pages_narrow_and_a_wrong_page_is_empty(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        class Resp:
            status_code = 200
            def __init__(self, ads, matched):
                self.ads, self.matched = ads, matched
            def json(self):
                return {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-22"},
                        "matched": self.matched, "limit": 40, "offset": 0, "observed": {"ads": self.ads}}
        mixed = [{"id": "keep", "advertiser": "Financial Markets Online", "pageId": "141622589997474", "status": "live", "headline": "Course"},
                 {"id": "drop", "advertiser": "Christopher Edwards", "pageId": "1284750291390880", "status": "live", "headline": "Transparency Before Performance"}]
        wrong = [{"id": "other", "advertiser": "Christopher Edwards", "pageId": "1284750291390880", "status": "live", "headline": "Transparency Before Performance"}]
        try:
            with patch("studio.adintel.requests.get", return_value=Resp(mixed, 621)):
                pack = query_ads({"advertiser": "Financial Markets Online", "page_id": "141622589997474"})
            self.assertEqual([ad["id"] for ad in pack["ads"]], ["keep"])
            self.assertEqual(pack["matched"], 1)
            with patch("studio.adintel.requests.get", side_effect=[type("Bad", (), {"status_code": 400, "json": lambda self: {}})(), Resp(wrong, 109)]):
                empty = query_ads({"advertiser": "Financial Markets Online", "page_id": "141622589997474"})
            self.assertEqual(empty["ads"], [])
            self.assertEqual(empty["matched"], 0)
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_unassigned_ad_joins_the_advertiser_funnel(self):
        from studio.adintel import funnel_for, route_for_funnel
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "secret"
        directory = {"ok": True, "evidence": {"source": "sqlite", "asOf": "2026-10-21"}, "observed": {"funnels": [
            {"id": "ethan", "name": "Ethan Young", "advertisers": [], "ads": 0},
            {"id": "small", "name": "FMO short", "advertisers": ["financial markets online"], "ads": 2},
            {"id": "recrZfUBy00FQqCeX", "name": "Financial Markets Online", "advertisers": ["Financial Markets Online"], "ads": 39},
        ]}}
        class Resp:
            def __init__(self, data, code=200):
                self.status_code = code
                self.data = data
            def json(self):
                return self.data
        def fmo(url, params=None, timeout=None, headers=None):
            if url.endswith("/ads/fmo-1"):
                return Resp({"ok": True, "evidence": {"asOf": "2026-10-21"}, "observed": {
                    "id": "fmo-1", "advertiser": "Financial Markets Online", "status": "live",
                    "assignedFunnel": "", "funnel": "", "headline": "90-Day Trading Course"}})
            if url.endswith("/funnels"):
                return Resp(directory)
            if url.endswith("/funnels/recrZfUBy00FQqCeX/route"):
                owned = [{"role": "cta", "title": "Form", "adId": "fmo-1"}, {"role": "landing", "title": "Webinar", "adId": "fmo-1"}]
                noise = [{"role": "landing", "title": "Eventbrite", "adId": "other"}] * 20
                return Resp({"ok": True, "evidence": {"asOf": "2026-10-21"},
                             "funnel": {"id": "recrZfUBy00FQqCeX", "name": "Financial Markets Online"},
                             "observed": {"storedSteps": noise + owned, "laneSteps": []},
                             "inferred": {"storedSteps": [], "laneSteps": []},
                             "unknown": {"storedSteps": [], "laneSteps": []}})
            raise AssertionError(url)
        def ethan(url, params=None, timeout=None, headers=None):
            if url.endswith("/ads/ethan-1"):
                return Resp({"ok": True, "evidence": {"asOf": "2026-10-21"}, "observed": {
                    "id": "ethan-1", "advertiser": "Ethan Young", "status": "live", "assignedFunnel": "", "funnel": ""}})
            if url.endswith("/funnels"):
                return Resp(directory)
            raise AssertionError(url)
        wide = [{"role": "next", "title": f"Step {i}"} for i in range(15) for _ in range(2)]
        wide_body = {"ok": True, "evidence": {"asOf": "2026-10-21"}, "funnel": {"id": "big", "name": "Big"},
                     "observed": {"storedSteps": wide, "laneSteps": []},
                     "inferred": {"storedSteps": [], "laneSteps": []}, "unknown": {"storedSteps": [], "laneSteps": []}}
        try:
            with patch("studio.adintel.requests.get", side_effect=fmo):
                result = funnel_for("fmo-1")
            self.assertEqual(result["funnel_id"], "recrZfUBy00FQqCeX")
            self.assertEqual(result["funnel_name"], "Financial Markets Online")
            self.assertNotIn("note", result)
            self.assertEqual([step["label"] for step in result["steps"]], ["Form", "Webinar"])
            self.assertTrue(all(step["ad_id"] == "fmo-1" for step in result["steps"]))
            with patch("studio.adintel.requests.get", side_effect=ethan) as get:
                empty = funnel_for("ethan-1")
            self.assertIn("No funnel is assigned", empty["note"])
            self.assertEqual(empty["steps"], [])
            self.assertEqual(get.call_count, 2)
            with patch("studio.adintel.requests.get", return_value=Resp(wide_body)):
                collapsed = route_for_funnel("big", ad_id="missing")
            self.assertEqual(len(collapsed["steps"]), 12)
            self.assertEqual(len({(step["kind"], step["label"]) for step in collapsed["steps"]}), 12)
        finally:
            os.environ.pop("ADINTEL_API_URL", None)
            os.environ.pop("ADINTEL_API_SECRET", None)

    def test_dry_placeholders_stay_small_unless_asked(self):
        from studio.providers.mock import frame_size, mock_fast, placeholder_seconds
        saved = os.environ.get("STUDIO_MOCK_FAST")
        def restore():
            if saved is None:
                os.environ.pop("STUDIO_MOCK_FAST", None)
            else:
                os.environ["STUDIO_MOCK_FAST"] = saved
        self.addCleanup(restore)
        os.environ.pop("STUDIO_MOCK_FAST", None)
        self.assertTrue(mock_fast())
        self.assertEqual(frame_size(), (270, 480, 10))
        self.assertEqual(placeholder_seconds(15), 1.0)
        os.environ["STUDIO_MOCK_FAST"] = "0"
        self.assertFalse(mock_fast())
        self.assertEqual(frame_size(), (1080, 1920, 30))
        self.assertEqual(placeholder_seconds(15), 15.0)

    def test_dry_run_round_trips_stay_small_when_each_call_is_slow(self):
        """A remote libSQL call is one round trip. Thirty stages must stay in seconds, not minutes."""
        import time
        nodes = [{"id": f"n{i}", "type": "market_timeline", "position": {"x": 0, "y": i * 20},
                  "enabled": True, "config": {}} for i in range(30)]
        graph = {"nodes": nodes, "edges": [], "variables": {"persona": "marco", "offer": "a checklist",
                 "script": "Decide the stop before you size the trade."}}
        c = cfg()
        c["_db_file"] = TMP / "latency.db"
        url = str(TMP / "latency-libsql.db")
        os.environ["TURSO_DATABASE_URL"] = url
        self.addCleanup(os.environ.pop, "TURSO_DATABASE_URL", None)
        os.environ.pop("STUDIO_SQLITE", None)
        runner = Runner(c, log=lambda m: None)
        self.assertTrue(type(runner.db.conn).__name__ == "_LibsqlConn")
        brief = job_brief(graph, compile_board(graph))
        jid = runner.create_job(brief)
        runner.db.round_trips = 0
        runner.db.latency_s = 0.02
        started = time.monotonic()
        with runner.db.job_scope(jid):
            status = "created"
            for _ in range(40):
                step = runner.advance(jid)
                status = step.get("status") or status
                if step.get("ok") is False or step.get("action") in ("idle", "stopped") or status in ("done", "failed", "awaiting_review"):
                    break
        elapsed = time.monotonic() - started
        self.assertEqual(status, "done", step)
        self.assertLess(elapsed, 8, f"{elapsed:.2f}s and {runner.db.round_trips} round trips")
        self.assertLess(runner.db.round_trips, 80, runner.db.round_trips)
        self.assertEqual(runner.db.job(jid)["status"], "done")
        runner.db.latency_s = 0
        runner.db.round_trips = 0
        self.assertEqual(len(runner.db.runs(jid)), 30)
        runner.db.q("SELECT * FROM scores WHERE job_id=?", (jid,))
        runner.db.events(jid, 60)
        runner.db.job(jid)
        # Paint is one job read, one runs list, one scores list, one event list.
        self.assertLess(runner.db.round_trips, 8, runner.db.round_trips)
        detail = create_app(c).test_client().get(f"/api/jobs/{jid}").get_json()
        self.assertEqual(detail["job"]["status"], "done")
        self.assertEqual(sum(1 for stage in detail["stages"] if _stage_status(detail, stage["id"]) == "succeeded"), 30)

    def test_stale_hrana_stream_is_reopened(self):
        c = cfg()
        c["_db_file"] = TMP / "reconnect.db"
        runner = Runner(c, log=lambda m: None)
        runner.db.fail_statements = 1
        jid = runner.create_job({"persona": "marco", "template": "talking_head",
                                 "script": "Decide the stop before you size the trade."})
        self.assertEqual(runner.db.job(jid)["persona"], "marco")
        runner.db.fail_scripts = 1
        step = runner.advance(jid)
        self.assertTrue(step.get("ok"), step)
        self.assertEqual(runner.db.latest(jid, step["stage"])["status"], "succeeded")


def _stage_run(job, sid):
    for stage in job["stages"]:
        if stage["id"] == sid and stage["runs"]:
            return stage["runs"][-1]
    return None


def _stage_status(job, sid):
    run = _stage_run(job, sid)
    return run["status"] if run else None


if __name__ == "__main__":
    unittest.main()
