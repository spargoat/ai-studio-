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
from studio.board import (BoardError, compile_board, migrate_talking_head_layout, preview_config, preview_pack,
                          talking_head_preset, timeline_artefact as board_timeline)
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
            loaded = client.get("/api/boards/talking_head").get_json()
            self.assertEqual(loaded["preview"]["origin"], "fixture")
            self.assertIn("Fixture", loaded["preview"]["label"])
            self.assertTrue(loaded["preview"]["timeline"]["evidence"]["fields"]["points"])
            started = client.post("/api/boards/talking_head/run", json={})
            self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
            payload = started.get_json()
            self.assertEqual(payload["mode"], "dry")
            self.assertIs(payload["pump"], True)
            job = client.get(f"/api/jobs/{payload['id']}").get_json()
            self.assertEqual(job["job"]["mode"], "dry")
            # The first request only runs the unwired timeline. The chain continues on /advance.
            self.assertEqual(_stage_status(job, "timeline"), "succeeded")
            self.assertIsNone(_stage_status(job, "ad_feed"))
            last = {}
            for _ in range(16):
                last = client.post(f"/api/jobs/{payload['id']}/advance").get_json()
                if last.get("ok") is False or last.get("status") in ("awaiting_review", "done", "failed"):
                    break
                if last.get("action") in ("idle", "stopped"):
                    break
            self.assertEqual(last.get("status"), "awaiting_review", last)
            done = client.get(f"/api/jobs/{payload['id']}").get_json()
            for sid in ("timeline", "ad_feed", "ad_breakdown", "funnel", "teardown", "brief", "script"):
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
        self.assertTrue(all(row["steps"] and row["headline"] for row in rows))
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
