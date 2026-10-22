"""Dry-run tests. Uses a throwaway DB; paid adapters are replaced by a tripwire so any
real network call would fail the test. Keys are removed from the env for this process."""
import os, sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")          # small placeholder renders: same code paths, ~10x faster
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS"):
    os.environ.pop(k, None)
from studio.config import load_config
from studio.runner import Runner, SpendGuardError
from studio.providers import fal, elevenlabs
from studio.cli import load_briefs

def _trip(*a, **k):
    raise AssertionError("TRIPWIRE: a paid provider was called in a test")
fal.FalProvider.run = _trip
elevenlabs.ElevenLabsProvider.run = _trip

APP = Path(__file__).resolve().parent.parent
TMP = Path(os.environ.get("STUDIO_TEST_DIR") or tempfile.mkdtemp())

def cfg():
    c = load_config()
    c["_data_dir"] = TMP; c["_db_file"] = TMP / "studio.db"
    return c

BRIEF = load_briefs(APP / "briefs/example_marco.yaml")[0]


class T(unittest.TestCase):
    def setUp(self):
        self._runners = []
        orig = Runner.__init__
        test = self
        def init(self_, *a, **k):
            orig(self_, *a, **k); test._runners.append(self_)
        Runner.__init__ = init; self._orig = orig

    def tearDown(self):
        Runner.__init__ = self._orig
        for r in self._runners: r.close()

    def test_1_e2e(self):
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        jid = r.create_job(dict(BRIEF, id="t-e2e"))
        self.assertEqual(r.run_job(jid), "done")
        fin = r.db.latest(jid, "finish")
        self.assertTrue(Path(fin["output_path"]).stat().st_size > 10000)
        self.assertEqual(r.db.live_spend(), 0)

    def test_2_gates_stop(self):
        r = Runner(cfg(), log=lambda m: None)
        jid = r.create_job(dict(BRIEF, id="t-gate"))
        self.assertEqual(r.run_job(jid), "awaiting_review")
        self.assertIsNone(r.db.latest(jid, "video"))       # blocked by still gate
        self.assertIsNotNone(r.db.latest(jid, "voice"))    # independent branch ran
        r.review(jid, "still", "approved", "ok"); r.review(jid, "voice", "approved", "ok")
        self.assertEqual(r.run_job(jid), "done")

    def test_3_fail_then_rerun_only_lipsync(self):
        r = Runner(cfg(), auto_approve=True, fail_inject={"lipsync": 99}, log=lambda m: None)
        jid = r.create_job(dict(BRIEF, id="t-fail"))
        self.assertEqual(r.run_job(jid), "failed")
        ls = r.db.latest(jid, "lipsync")
        self.assertEqual((ls["status"], ls["attempts"]), ("failed", 3))  # 1 + 2 retries
        before = {s: r.db.latest(jid, s)["id"] for s in ("brief", "still", "video", "voice")}
        r2 = Runner(cfg(), auto_approve=True, log=lambda m: None)
        r2.rerun(jid, "lipsync")
        after = {s: r2.db.latest(jid, s)["id"] for s in before}
        self.assertEqual(before, after)                      # nothing upstream re-ran
        self.assertEqual(r2.db.latest(jid, "lipsync")["version"], 2)
        self.assertIsNone(r2.db.latest(jid, "finish"))       # rerun touched ONLY lipsync
        self.assertEqual(r2.run_job(jid), "done")

    def test_4_batch3(self):
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        ids = [r.create_job(b, batch_id="t-batch") for b in load_briefs(APP / "briefs/batch3.csv")]
        self.assertEqual(r.run_jobs(ids, parallel=3), ["done"] * 3)

    def test_5_voice_change_reruns_lipsync_not_video(self):
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        jid = "t-e2e"
        vid = r.db.latest(jid, "video")["id"]
        r.rerun(jid, "voice", cascade=True)
        self.assertEqual(r.db.latest(jid, "video")["id"], vid)
        self.assertEqual(r.db.latest(jid, "lipsync")["version"], 2)

    def test_6_guards(self):
        with self.assertRaises(SpendGuardError):          # live needs caps
            c = cfg(); c["budget"] = {"global_cap_usd": None, "per_job_cap_usd": None}
            Runner(c, live=True)
        with self.assertRaises(SpendGuardError):          # live needs keys
            c = cfg(); c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
            Runner(c, live=True)
        os.environ["FAL_KEY"] = "dummy"; os.environ["ELEVENLABS_API_KEY"] = "dummy"
        try:
            c = cfg(); c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 0.01}
            r = Runner(c, live=True, log=lambda m: None)
            jid = r.create_job(dict(BRIEF, id="t-guard"))
            r.run_job(jid)
            self.assertEqual(r.db.latest(jid, "still")["status"], "blocked_budget")  # blocked before tripwire
        finally:
            os.environ.pop("FAL_KEY"); os.environ.pop("ELEVENLABS_API_KEY")

    def test_7_fal_lock_preflight(self):
        os.environ["FAL_KEY"] = "dummy"; os.environ["ELEVENLABS_API_KEY"] = "dummy"
        try:
            c = cfg(); c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
            r = Runner(c, log=lambda m: None)
            r.db.add_attempt(job_id="t-lock", stage="still", attempt=1, mode="live", provider="fal", status="failed",
                             error='fal 403: {"detail":"User is locked. Reason: Exhausted balance."}',
                             est_cost_usd=0.05, actual_cost_usd=0.0, started_at="2099-01-01T00:00:00+00:00")
            with self.assertRaises(SpendGuardError):
                Runner(c, live=True, log=lambda m: None)
            os.environ["STUDIO_FAL_TOPPED_UP"] = "1"
            Runner(c, live=True, log=lambda m: None)            # acknowledged top-up: preflight passes, no call made
        finally:
            for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "STUDIO_FAL_TOPPED_UP"): os.environ.pop(k, None)

    def test_8_reconcile_raises_cap_spend(self):
        from studio.db import DB
        with DB(TMP / "rec.db") as db:
            db.add_attempt(job_id="j", stage="s", attempt=1, mode="live", provider="fal", status="succeeded",
                           est_cost_usd=10, actual_cost_usd=10, started_at="2026-10-19T10:00:00+01:00")
            self.assertEqual(db.effective_spend(), 10)
            db.add_reconciliation("fal", 16.5, "2026-10-19T12:00:00+01:00", "dashboard")
            self.assertEqual(db.effective_spend(), 16.5)                 # higher confirmed figure wins
            db.add_attempt(job_id="j", stage="s", attempt=2, mode="live", provider="fal", status="succeeded",
                           est_cost_usd=1, actual_cost_usd=1, started_at="2026-10-19T13:00:00+01:00")
            self.assertEqual(db.effective_spend(), 17.5)                 # spend after as-of adds on top
            db.add_reconciliation("fal", 5, "2026-10-19T14:00:00+01:00")
            self.assertEqual(db.effective_spend(), 11)                   # logged wins when higher

    def test_9_quality_gate(self):
        from studio import score
        import numpy as np, cv2
        c = cfg()
        ref = score.ref_paths(c, "maksim")[0]
        s = score.score_file(c, "maksim", ref, stage="still")           # a ref scored against its own set
        self.assertGreater(s["identity"], 0.9)
        self.assertTrue(all(s[k] is None for k in score.NOT_SCORED))    # never faked
        blank = str(TMP / "blank.png"); cv2.imwrite(blank, np.zeros((1920, 1080, 3), np.uint8))
        b = score.score_file(c, "maksim", blank, stage="still")
        self.assertFalse(b["passed"]); self.assertIn("identity: no face found", b["flags"])
        c["quality"] = dict(c.get("quality") or {}, identity_threshold=0.99)
        self.assertFalse(score.score_file(c, "maksim", score.ref_paths(c, "maksim")[1], stage="still")["passed"])
        r = Runner(cfg(), log=lambda m: None)                            # e2e job runs were scored and stored
        self.assertIsNotNone(r.db.score(r.db.latest("t-e2e", "still")["id"]))

    def test_99_compose(self):
        from studio import compose as C
        cues = C.parse_vtt("/workspace/maksim-vsl/captions-en-v3.vtt") if Path("/workspace/maksim-vsl/captions-en-v3.vtt").exists() else []
        if cues:
            self.assertEqual(cues[0][:2], (0.4, 3.186))
            w = C.spread(cues[0][2], cues[0][0], cues[0][1])
            self.assertEqual(w[0][1], 0.4); self.assertAlmostEqual(w[-1][2], 3.186, 2)
            self.assertTrue(all(a[2] <= b[1] + 1e-6 for a, b in zip(w, w[1:])))
        f = C.frame_filter(1080, 1920, 1080, 1080, "crop", (900, 300))      # face near top-right: window clamps
        self.assertIn("crop=1080:1080:0:0", f)
        self.assertIn("pad=1920:1080", C.frame_filter(1080, 1920, 1920, 1080, "pad", None))
        r = Runner(cfg(), log=lambda m: None)                                # e2e dry job composed all aspects
        run = r.db.latest("t-e2e", "compose")
        m = json.loads(run["meta_json"])
        exp = ["16:9", "1:1", "9:16"] if os.environ.get("STUDIO_MOCK_FAST") == "0" else ["9:16"]
        self.assertEqual(sorted(m["exports"]), exp)
        self.assertTrue(all(Path(p).stat().st_size > 5000 for p in m["exports"].values()))
        self.assertEqual(sorted(m["export_scores"]), exp) if len(exp) > 1 else None
        self.assertGreater(m["words"], 5)

    def test_a1_character_lock(self):
        from studio.persona import lock_text, missing
        c = cfg()
        for name in ("maksim", "marco", "callum"):
            self.assertIn("lock", c["_personas"][name])
        t = lock_text(c["_personas"]["maksim"], "image")
        self.assertIn("swept-back ash-blond/grey", t); self.assertNotIn("skin tone", t)   # unknown not injected
        self.assertIn("skin_tone", missing(c["_personas"]["maksim"]))
        self.assertEqual(lock_text(c["_personas"]["maksim"], "voice"), "")
        r = Runner(c, log=lambda m: None)
        run = r.db.latest("t-e2e", "still") or {}
        self.assertIn("Character lock", run.get("prompt") or "")
        self.assertIn("Character lock", r.db.latest("t-e2e", "video")["prompt"])

    def test_a2_library(self):
        from studio import library as L
        r = Runner(cfg(), log=lambda m: None)
        fin = [x for x in L.rows(r.db, persona="marco", limit=500) if x["job_id"] == "t-e2e"]
        self.assertTrue(any(x["stage"] == "compose" for x in fin))                  # done job's final indexed
        self.assertTrue(any(x["stage"] == "still" for x in fin))                    # auto-approved still indexed
        self.assertGreater(L.backfill(r.db), 0)
        self.assertTrue(L.rows(r.db, q="Character lock"))                            # prompt search
        lid = L.promote(r.db, "t-e2e/still", "hero still")
        self.assertEqual(L.rows(r.db, promoted=True)[0]["id"], lid)
        with self.assertRaises(ValueError):
            L.promote(r.db, "t-fail/nope")

    def test_a3_review_page(self):
        from studio.web import create_app
        app = create_app(cfg()); c = app.test_client()
        for u in ("/classic", "/job/t-e2e", "/classic/library", "/classic/library?q=still"):
            self.assertEqual(c.get(u).status_code, 200, u)
        page = c.get("/job/t-e2e").data.decode()
        self.assertIn("viewport", page); self.assertIn("identity", page); self.assertIn("not scored", page)
        self.assertEqual(c.post("/job/t-e2e/review/still", data={"v": "rejected", "note": ""}).status_code, 400)
        r = Runner(cfg(), log=lambda m: None)                    # header shows the model the run used
        lid = r.db.latest("t-e2e", "lipsync")["id"]
        r.db.update_run(lid, model="fal-ai/sync-lipsync/v3")
        page = c.get("/job/t-e2e").data.decode()
        self.assertIn("fal-ai/sync-lipsync/v3", page); self.assertIn("default fal-ai/sync-lipsync/v2/pro", page)
        self.assertEqual(c.post("/job/t-e2e/review/still", data={"v": "approved", "note": "ok"}).status_code, 302)

    def test_a4_ported_templates_e2e(self):
        os.environ["STUDIO_COMPOSE_ALL"] = "1"                  # this test covers all three aspect exports
        self.addCleanup(os.environ.pop, "STUDIO_COMPOSE_ALL", None)
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        j1 = r.create_job({"id": "t-static", "persona": "maksim", "template": "static_ad", "topic": "calm trading",
                           "idea": "quiet desk at dawn", "headline": "Where am I wrong?", "cta": "Free guide"})
        self.assertEqual(r.run_job(j1), "done")
        m = json.loads(r.db.latest(j1, "compose")["meta_json"])
        self.assertEqual(sorted(m["exports"]), ["16:9", "1:1", "9:16"])
        self.assertTrue(all(p.endswith(".png") for p in m["exports"].values()))
        self.assertIn("[MOCK prompt 1/10]", r.db.latest(j1, "still")["prompt"])
        j2 = r.create_job({"id": "t-beats", "persona": "callum", "template": "script_to_scene_beats",
                           "premise": "a riser's first club night", "setting": "a London club corridor"})
        self.assertEqual(r.run_job(j2), "done")
        self.assertIn("[MOCK beat 1]", r.db.latest(j2, "video")["prompt"])

    def test_a5_script_stage(self):
        r = Runner(cfg(), auto_approve=True, log=lambda m: None)
        jid = r.create_job({"id": "t-script", "persona": "maksim", "template": "talking_head_scripted",
                            "topic": "where am I wrong", "audience": "evening traders"})
        self.assertEqual(r.run_job(jid), "done")
        out = json.loads(Path(r.db.latest(jid, "script")["output_path"]).read_text())
        self.assertEqual(out["framework"], "PAS"); self.assertEqual(len(out["hooks"]), 5)
        self.assertIn("PAS order", r.db.latest(jid, "script")["prompt"])
        self.assertIn("100-110 wpm", r.db.latest(jid, "script")["prompt"])          # cadence from the lock
        self.assertEqual(json.loads(Path(r.db.latest(jid, "brief")["output_path"]).read_text())["brief"]["topic"], "where am I wrong")
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy", STUDIO_FAL_TOPPED_UP="1")
        try:                                                     # live with no LLM key: clean stop, nothing paid called
            c = cfg(); c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
            for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"):
                os.environ.pop(k, None)
            lr = Runner(c, live=True, log=lambda m: None)
            j2 = lr.create_job({"id": "t-script-live", "persona": "maksim", "template": "talking_head_scripted", "topic": "x"})
            self.assertEqual(lr.run_job(j2), "failed")
            run = lr.db.latest(j2, "script")
            self.assertIn("needs key", run["error"]); self.assertEqual(run["attempts"], 1)
            self.assertIsNone(lr.db.latest(j2, "voice")); self.assertIsNone(lr.db.latest(j2, "still"))  # no paid stage ran
        finally:
            for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "STUDIO_FAL_TOPPED_UP"): os.environ.pop(k, None)

    def test_a6_higgsfield_fallback(self):
        from studio.providers import higgsfield
        calls = []
        def fake(kind, payload, out_dir):                      # mock transport only, no network
            calls.append((kind, payload["model"]))
            p = Path(out_dir) / "hf.png"; p.write_bytes(b"x" * 20000)
            return {"output_path": str(p), "units_actual": 1, "request_id": "hf-mock"}
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy")
        os.environ.pop("STUDIO_FAL_TOPPED_UP", None)
        try:
            c = cfg(); c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
            # TMP db holds the fal lock row from test_7; unpriced fallback -> still refused
            with self.assertRaises(SpendGuardError):
                Runner(c, live=True, log=lambda m: None)
            for k in ("hf_image", "hf_video", "hf_lipsync"):
                c["prices"][k]["usd_per_unit"] = 0.1
            r = Runner(c, live=True, log=lambda m: None)         # priced fallback -> live allowed while fal locked
            prov, sc = r.provider_for("still")
            self.assertEqual((prov.name, sc["price_key"]), ("higgsfield", "hf_image"))
            self.assertEqual(r.provider_for("voice")[0].name, "elevenlabs")   # non-fal stages unchanged
            with self.assertRaises(Exception):                   # default transport: clean refusal, nothing sent
                prov.run("image", {"model": "m", "out_dir": str(TMP), "stage": "still"})
            higgsfield.HiggsfieldProvider.transport = staticmethod(fake)
            res = prov.run("image", {"model": sc["model"], "out_dir": str(TMP), "stage": "still", "prompt": "p"})
            self.assertEqual(res["meta"]["provider"], "higgsfield"); self.assertEqual(calls[0][0], "image")
            self.assertIsNone(Runner(cfg(), log=lambda m: None).fallback_route("still", cfg()["stages"]["still"]))  # dry-run: never
        finally:
            higgsfield.HiggsfieldProvider.transport = staticmethod(higgsfield._no_transport)
            for k in ("FAL_KEY", "ELEVENLABS_API_KEY"): os.environ.pop(k, None)

    def test_a7_alignment_captions_and_overlay(self):
        from studio import compose as C
        from studio.providers import elevenlabs as E
        al = {"characters": list("No hoping. Small"), "character_start_times_seconds": [i * 0.1 for i in range(16)],
              "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(16)]}
        w = C.words_from_alignment(al, offset=0.3)
        self.assertEqual([x[0] for x in w], ["No", "hoping.", "Small"])
        self.assertEqual((w[0][1], w[0][2]), (0.3, 0.5)); self.assertEqual(w[2][1], round(1.1 + 0.3, 3))
        self.assertIn("fontsize=81", C.overlay_filters([{"text": "x"}], 1080, 1920, "f")[0])      # 7.5% on 9:16
        self.assertIn("fontsize=54", C.overlay_filters([{"text": "x"}], 1920, 1080, "f")[0])      # 5% on 16:9
        import base64, types                                       # mocked HTTP: no network
        captured = {}
        def post(url, **kw):
            captured["url"] = url
            return types.SimpleNamespace(status_code=200, headers={"character-cost": "16"},
                                         json=lambda: {"audio_base64": base64.b64encode(b"ID3fake").decode(), "alignment": al})
        orig = E.requests.post; E.requests.post = post
        orig_run = E.ElevenLabsProvider.run
        try:
            del E.ElevenLabsProvider.run                           # undo the test tripwire for this one mocked call
            os.environ["ELEVENLABS_API_KEY"] = "dummy"
            p = E.ElevenLabsProvider("elevenlabs", {"key_env": ["ELEVENLABS_API_KEY"], "with_timestamps": True})
            res = p.run("voice", {"script": "No hoping. Small", "model": "m", "voice_id": "v", "out_dir": str(TMP)})
            self.assertTrue(captured["url"].endswith("/with-timestamps"))
            self.assertEqual(json.loads(Path(res["meta"]["alignment_path"]).read_text())["characters"][0], "N")
        finally:
            E.requests.post = orig; E.ElevenLabsProvider.run = orig_run; os.environ.pop("ELEVENLABS_API_KEY", None)


    def test_a8_wide_framing(self):
        from studio.compose import frame_filter
        z = frame_filter(1080, 1920, 1920, 1080, "zoom", (540, 900), "0x141414", 0.6)
        self.assertIn("pad=1920:1080", z); self.assertNotIn("blur", z)
        sd = frame_filter(1080, 1920, 1920, 1080, "side", None, "0x141414", side="left")
        self.assertIn("pad=1920:1080:153:0", sd); self.assertNotIn("blur", sd)
        self.assertNotIn("pad=", frame_filter(1080, 1920, 1920, 1080, "crop", None))

    def test_a9_export(self):
        from studio.export import export, ass_to_srt
        r = Runner(cfg(), log=lambda m: None)
        out = Path(tempfile.mkdtemp()) / "bundle"
        man, z = export(r.db, out, job="t-e2e")
        it = [i for i in man["items"] if "files" in i]
        self.assertEqual([i["stage"] for i in it], ["compose"])                    # compose supersedes finish
        f = it[0]["files"]
        self.assertIn("caption.txt", f); self.assertTrue(any(x.endswith(".mp4") for x in f))
        self.assertTrue(Path(z).exists() and json.loads((out / "manifest.json").read_text())["count"] == 1)
        self.assertEqual(export(r.db, Path(tempfile.mkdtemp()) / "b", job="t-e2e", live_only=True)[0]["count"], 0)
        a = Path(tempfile.mkdtemp()) / "c.ass"
        a.write_text("Dialogue: 0,0:00:00.39,0:00:00.78,Cap,,0,0,0,,{\\pos(1,1)}{\\c&H0&}Hi{\\c&H1&} there\n"
                     "Dialogue: 0,0:00:00.78,0:00:01.00,Cap,,0,0,0,,Hi {\\c&H0&}there\n")
        self.assertEqual(ass_to_srt(a), "1\n00:00:00,390 --> 00:00:01,000\nHi there\n\n")

    def test_b1_api_journey(self):
        import time
        from studio.web import create_app
        app = create_app(cfg()); c = app.test_client()
        for u in ("/api/meta", "/api/overview", "/api/jobs", "/api/library", "/api/personas", "/api/settings", "/api/spend", "/api/exports"):
            self.assertEqual(c.get(u).status_code, 200, u)
        st = c.get("/api/settings").get_json()
        self.assertTrue(all(set(k) == {"provider", "env", "set", "optional"} for k in st["keys"]))   # names only
        b = {"persona": "marco", "template": "talking_head", "script": "Quick test line."}
        self.assertGreater(c.post("/api/estimate", json={"brief": b}).get_json()["total"], 0)
        self.assertEqual(c.post("/api/jobs", json={"brief": b, "mode": "live"}).status_code, 409)       # UI never starts live
        self.assertEqual(c.post("/api/jobs", json={"brief": dict(b, persona="nobody")}).status_code, 400)
        jid = c.post("/api/jobs", json={"brief": dict(b, id="t-ui")}).get_json()["id"]
        for _ in range(300):
            j = c.get(f"/api/jobs/{jid}").get_json()
            if not j["running"] and j["job"]["status"] != "running": break
            time.sleep(0.1)
        self.assertEqual(j["job"]["status"], "awaiting_review")                                         # stops at the still gate
        self.assertEqual(c.post(f"/api/jobs/{jid}/review/still", json={"verdict": "rejected"}).status_code, 400)
        self.assertEqual(c.post(f"/api/jobs/{jid}/review/still", json={"verdict": "approved", "note": "ok"}).status_code, 200)
        lib = [r for r in c.get("/api/library?q=t-ui").get_json() if r["job_id"] == "t-ui"]
        self.assertTrue(any(r["stage"] == "still" for r in lib))
        self.assertEqual(c.post(f"/api/library/{lib[0]['id']}/promote", json={"note": "ui"}).status_code, 200)
        self.assertEqual(c.post("/api/export", json={"ids": []}).status_code, 400)
        r = c.post("/api/export", json={"ids": [lib[0]["id"]], "all_stages": True}).get_json()
        self.assertTrue(r["zip"].endswith(".zip") and r["manifest"]["count"] == 1)
        self.assertEqual(c.get(r["url"]).status_code, 200)
        self.assertEqual(c.get("/api/personas/marco/ref/99").status_code, 404)
        self.assertIn(c.get("/jobs/t-ui").status_code, (200, 503))                                       # SPA route (503 = UI not built)

    def test_b2_password_gate(self):
        from studio.web import create_app
        from studio import auth
        env = {"STUDIO_REQUIRE_AUTH": "1", "STUDIO_APP_PASSWORD": "t-pass-123", "STUDIO_SESSION_SECRET": "t-secret-abc"}
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ["STUDIO_REQUIRE_AUTH"] = "1"; os.environ.pop("STUDIO_APP_PASSWORD", None)
            c = create_app(cfg()).test_client()
            self.assertEqual(c.get("/").status_code, 503)                                  # fails closed
            os.environ.update(env); auth._fail.clear()
            c = create_app(cfg()).test_client()
            r = c.get("/jobs"); self.assertEqual(r.status_code, 302); self.assertIn("/login?next=/jobs", r.headers["Location"])
            self.assertEqual(c.get("/api/meta").status_code, 401)
            self.assertEqual(c.get("/file?p=/etc/hostname").status_code, 302)
            self.assertEqual(c.post("/login", data={"password": "wrong"}).status_code, 401)
            r = c.post("/login", data={"password": "t-pass-123", "next": "//evil.example"})
            self.assertEqual(r.status_code, 302); self.assertEqual(r.headers["Location"], "/")    # no open redirect
            self.assertIn("HttpOnly", r.headers["Set-Cookie"]); self.assertIn("SameSite=Lax", r.headers["Set-Cookie"])
            self.assertEqual(c.get("/api/meta").status_code, 200)
            self.assertEqual(c.post("/api/jobs", json={"brief": {"persona": "marco", "script": "x"}, "mode": "live"}).status_code, 409)
            self.assertFalse(auth.verify(auth.sign("t-secret-abc", now=0), "t-secret-abc"))     # expired
            self.assertFalse(auth.verify(auth.sign("other"), "t-secret-abc"))                   # forged
            c.get("/logout"); self.assertEqual(c.get("/api/meta").status_code, 401)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_auth_disabled_switch_skips_the_gate(self):
        from studio.web import create_app
        keys = ("STUDIO_REQUIRE_AUTH", "STUDIO_APP_PASSWORD", "STUDIO_SESSION_SECRET", "STUDIO_AUTH_DISABLED")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["STUDIO_REQUIRE_AUTH"] = "1"
            os.environ["STUDIO_APP_PASSWORD"] = "t-pass-123"
            os.environ["STUDIO_SESSION_SECRET"] = "t-secret-abc"
            os.environ.pop("STUDIO_AUTH_DISABLED", None)
            closed = create_app(cfg()).test_client()
            page = closed.get("/board")
            self.assertEqual(page.status_code, 302)
            self.assertIn("/login", page.headers["Location"])
            self.assertEqual(closed.get("/api/meta").status_code, 401)
            self.assertEqual(closed.get("/api/boards").status_code, 401)
            os.environ["STUDIO_AUTH_DISABLED"] = "0"
            still = create_app(cfg()).test_client()
            self.assertEqual(still.get("/api/spend").status_code, 401)
            os.environ["STUDIO_AUTH_DISABLED"] = "1"
            open_ = create_app(cfg()).test_client()
            board = open_.get("/board")
            self.assertNotIn("/login", board.headers.get("Location", ""))
            self.assertIn(board.status_code, (200, 503))
            self.assertEqual(open_.get("/library").status_code, board.status_code)
            self.assertEqual(open_.get("/api/meta").status_code, 200)
            self.assertEqual(open_.get("/api/boards").status_code, 200)
            self.assertEqual(open_.get("/api/spend").status_code, 200)
            login = open_.get("/login")
            self.assertEqual(login.status_code, 302)
            self.assertNotIn("password", login.get_data(as_text=True).lower())
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

if __name__ == "__main__":
    unittest.main(verbosity=2)
