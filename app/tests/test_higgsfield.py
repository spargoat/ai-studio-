"""Higgsfield provider: mocked SDK client, no network."""
import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS", "HIGGSFIELD_API_KEY", "HIGGSFIELD_API_SECRET",
          "HF_KEY", "HF_API_KEY", "HF_API_SECRET", "STUDIO_FAL_TOPPED_UP"):
    os.environ.pop(k, None)

import higgsfield_client as hf
from studio.config import load_config
from studio.pricing import HIGGSFIELD_PRICES, cost
from studio.providers.base import NonRetryable, ProviderError
from studio.providers.higgsfield import HiggsfieldPending, HiggsfieldProvider, credentials_from_env
from studio.providers.higgsfield import _sdk_transport
from studio.runner import Runner

APP = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())


def cfg():
    c = load_config()
    c["_data_dir"] = TMP
    c["_db_file"] = TMP / "hf.db"
    c["budget"] = {"global_cap_usd": 75, "per_job_cap_usd": 15}
    c["quality"] = {"enabled": False}
    return c


class Controller:
    def __init__(self, statuses, result, request_id="hf-req-1"):
        self.request_id = request_id
        self.status_url = f"https://api.higgsfield.ai/requests/{request_id}/status"
        self.response_url = self.status_url
        self.cancel_url = f"https://api.higgsfield.ai/requests/{request_id}/cancel"
        self._statuses = list(statuses)
        self.result = result
        self.get_calls = 0

    def status(self):
        if not self._statuses:
            raise AssertionError("status polled more times than the test queued")
        return self._statuses.pop(0)

    def get(self):
        self.get_calls += 1
        return self.result


class Client:
    def __init__(self):
        self.submits = []
        self.resumes = []
        self.uploads = []
        self.controllers = []

    def submit(self, application, arguments, webhook_url=None):
        self.submits.append((application, dict(arguments)))
        return self.controllers.pop(0)

    def get_request_controller(self, request_id):
        self.resumes.append(request_id)
        return self.controllers.pop(0)

    def upload_file(self, path):
        self.uploads.append(str(path))
        return "https://cdn.example.test/" + Path(path).name


def _download(self, url, out):
    Path(out).write_bytes(b"x" * 64)
    return str(out)


class T(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        HiggsfieldProvider.client_factory = lambda prov: self.client
        HiggsfieldProvider.transport = staticmethod(_sdk_transport)
        self._dl = HiggsfieldProvider._download
        HiggsfieldProvider._download = _download
        os.environ["HIGGSFIELD_API_KEY"] = "kid:ksecret"
        self.addCleanup(self._restore)

    def _restore(self):
        HiggsfieldProvider.client_factory = None
        HiggsfieldProvider.transport = staticmethod(_sdk_transport)
        HiggsfieldProvider._download = self._dl
        for k in ("HIGGSFIELD_API_KEY", "HIGGSFIELD_API_SECRET", "HF_KEY", "HF_API_KEY", "HF_API_SECRET"):
            os.environ.pop(k, None)

    def _provider(self, **cfg):
        base = {"key_env": ["HIGGSFIELD_API_KEY"], "poll_seconds": 0, "timeout_seconds": 5}
        base.update(cfg)
        return HiggsfieldProvider("higgsfield", base)

    def test_missing_key_fails_before_submit(self):
        os.environ.pop("HIGGSFIELD_API_KEY", None)
        calls = []
        HiggsfieldProvider.client_factory = lambda prov: calls.append("client")
        p = self._provider()
        with self.assertRaises(NonRetryable) as cm:
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP),
                        "params": {}, "refs": []})
        self.assertIn("HIGGSFIELD_API_KEY", str(cm.exception))
        self.assertIn("Nothing was sent", str(cm.exception))
        self.assertEqual(calls, [])

    def test_key_without_secret_fails_clearly(self):
        os.environ["HIGGSFIELD_API_KEY"] = "only-the-id"
        with self.assertRaises(NonRetryable) as cm:
            credentials_from_env()
        self.assertIn("HIGGSFIELD_API_SECRET", str(cm.exception))
        self.assertNotIn("only-the-id", str(cm.exception))

    def test_credential_shapes(self):
        os.environ["HIGGSFIELD_API_KEY"] = "id:secret"
        self.assertEqual(credentials_from_env(), "id:secret")
        os.environ["HIGGSFIELD_API_KEY"] = "id"
        os.environ["HIGGSFIELD_API_SECRET"] = "sec"
        self.assertEqual(credentials_from_env(), "id:sec")
        os.environ.pop("HIGGSFIELD_API_KEY"); os.environ.pop("HIGGSFIELD_API_SECRET")
        os.environ["HF_KEY"] = "hf:key"
        self.assertEqual(credentials_from_env(), "hf:key")
        os.environ.pop("HF_KEY")
        os.environ["HF_API_KEY"] = "a"; os.environ["HF_API_SECRET"] = "b"
        self.assertEqual(credentials_from_env(), "a:b")

    def test_image_submit_and_download(self):
        ref = TMP / "face.png"; ref.write_bytes(b"png")
        done = Controller([hf.Completed()], {"images": [{"url": "https://cdn.example.test/out.png"}]})
        self.client.controllers.append(done)
        p = self._provider()
        res = p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "same person", "out_dir": str(TMP),
                          "params": {"resolution": "1K", "aspect_ratio": "9:16", "num_images": 1, "output_format": "png"},
                          "refs": [str(ref)]})
        model, args = self.client.submits[0]
        self.assertEqual(model, "alibaba/qwen-image-3/edit")
        self.assertEqual(args["resolution"], "1k")
        self.assertEqual(args["aspect_ratio"], "9:16")
        self.assertEqual(args["image_urls"], ["https://cdn.example.test/face.png"])
        self.assertNotIn("num_images", args)
        self.assertNotIn("output_format", args)
        self.assertEqual(res["request_id"], "hf-req-1")
        self.assertEqual(res["units_actual"], 1)
        self.assertEqual(res["meta"]["provider"], "higgsfield")
        self.assertTrue(Path(res["output_path"]).exists())
        self.assertEqual(done.get_calls, 1)

    def test_edit_without_refs_sends_nothing(self):
        p = self._provider()
        with self.assertRaises(NonRetryable):
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP), "params": {}})
        self.assertEqual(self.client.submits, [])

    def test_video_payload_matches_fal_shape(self):
        still = TMP / "still.png"; still.write_bytes(b"png")
        done = Controller([hf.Completed()], {"video": {"url": "https://cdn.example.test/out.mp4"}, "duration": 5})
        self.client.controllers.append(done)
        p = self._provider()
        res = p.do_video({"model": "alibaba/wan-3.0/image-to-video", "prompt": "talking", "out_dir": str(TMP),
                          "video_seconds": 5, "inputs": {"still": str(still)},
                          "params": {"generate_audio": False, "cfg_scale": 0.5, "negative_prompt": "blur"}})
        model, args = self.client.submits[0]
        self.assertEqual(model, "alibaba/wan-3.0/image-to-video")
        self.assertEqual(args["image_url"], "https://cdn.example.test/still.png")
        self.assertEqual(args["duration"], 5)
        self.assertIs(args["generate_audio"], False)
        self.assertEqual(args["negative_prompt"], "blur")
        self.assertNotIn("cfg_scale", args)
        self.assertEqual(res["units_actual"], 5)
        self.assertTrue(res["output_path"].endswith("video.mp4"))

    def test_max_polls_raises_pending_without_fetching_result(self):
        ref = TMP / "face.png"; ref.write_bytes(b"png")
        queued = Controller([hf.Queued()], {"images": [{"url": "https://cdn.example.test/out.png"}]})
        self.client.controllers.append(queued)
        p = self._provider(max_polls=1)
        with self.assertRaises(HiggsfieldPending) as cm:
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP),
                        "refs": [str(ref)], "params": {}})
        self.assertEqual(cm.exception.status, "queued")
        self.assertEqual(cm.exception.request_id, "hf-req-1")
        self.assertEqual(queued.get_calls, 0)
        self.assertEqual(len(self.client.submits), 1)

    def test_resume_does_not_resubmit(self):
        done = Controller([hf.Completed()], {"images": [{"url": "https://cdn.example.test/out.png"}]}, "hf-req-9")
        self.client.controllers.append(done)
        p = self._provider(max_polls=1)
        res = p.poll("hf-req-9")
        saved = p.save_result("image", res, TMP / "resume")
        self.assertEqual(self.client.submits, [])
        self.assertEqual(self.client.resumes, ["hf-req-9"])
        self.assertEqual(saved["units_actual"], 1)

    def test_terminal_failures(self):
        ref = TMP / "face.png"; ref.write_bytes(b"png")
        self.client.controllers.append(Controller([hf.NSFW()], {}))
        p = self._provider()
        with self.assertRaises(NonRetryable):
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP),
                        "refs": [str(ref)], "params": {}})
        self.client.controllers.append(Controller([hf.Failed()], {}))
        with self.assertRaises(ProviderError):
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP),
                        "refs": [str(ref)], "params": {}})

    def test_auth_error_is_not_retried_by_the_provider(self):
        class Denied(Exception):
            def __init__(self):
                super().__init__("Invalid credentials")
                self.response = type("R", (), {"status_code": 401})()
        self.client.submit = lambda *a, **k: (_ for _ in ()).throw(Denied())
        ref = TMP / "face.png"; ref.write_bytes(b"png")
        p = self._provider()
        with self.assertRaises(NonRetryable) as cm:
            p.do_image({"model": "alibaba/qwen-image-3/edit", "prompt": "p", "out_dir": str(TMP),
                        "refs": [str(ref)], "params": {}})
        self.assertIn("401", str(cm.exception))

    def test_lipsync_refuses_until_a_real_model_is_set(self):
        video, audio = TMP / "v.mp4", TMP / "a.mp3"
        video.write_bytes(b"v"); audio.write_bytes(b"a")
        ctx = {"model": "unconfigured", "prompt": "", "out_dir": str(TMP), "params": {"sync_mode": "bounce"},
               "inputs": {"video": str(video), "voice": str(audio)}}
        p = self._provider()
        with self.assertRaises(NonRetryable) as cm:
            p.do_lipsync(ctx)
        self.assertIn("nothing was sent", str(cm.exception))
        self.assertEqual(self.client.submits, [])
        ctx["model"] = "vendor/lipsync"
        done = Controller([hf.Completed()], {"video": {"url": "https://cdn.example.test/lip.mp4"}, "duration": 3})
        self.client.controllers.append(done)
        res = p.do_lipsync(ctx)
        model, args = self.client.submits[0]
        self.assertEqual(model, "vendor/lipsync")
        self.assertEqual(args["video_url"], "https://cdn.example.test/v.mp4")
        self.assertEqual(args["audio_url"], "https://cdn.example.test/a.mp3")
        self.assertNotIn("sync_mode", args)
        self.assertTrue(res["output_path"].endswith("lipsync.mp4"))

    def test_prices_match_config_and_guard(self):
        c = load_config()
        for key, row in HIGGSFIELD_PRICES.items():
            cfg_row = c["prices"][key]
            self.assertEqual(cfg_row["usd_per_unit"], row["usd_per_unit"])
            self.assertEqual(cfg_row["unit"], row["unit"])
            self.assertGreater(row["usd_per_unit"], 0)
        self.assertEqual(cost(c, "hf_image", "image", 1, {}), 0.094)
        self.assertEqual(cost(c, "hf_video", "video", 5, {}), 1.0)

    def test_advance_polls_without_resubmitting(self):
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy")
        self.addCleanup(os.environ.pop, "FAL_KEY", None)
        self.addCleanup(os.environ.pop, "ELEVENLABS_API_KEY", None)
        ref = TMP / "face.png"; ref.write_bytes(b"png")
        c = cfg()
        c["_personas"]["marco"]["refs"] = [str(ref)]
        r = Runner(c, log=lambda m: None)
        self.addCleanup(r.close)
        r.db.add_attempt(job_id="t-lock", stage="still", attempt=1, mode="live", provider="fal", status="failed",
                         error='fal 403: {"detail":"User is locked. Reason: Exhausted balance."}',
                         est_cost_usd=0.05, actual_cost_usd=0.0, started_at="2099-01-01T00:00:00+00:00")
        live = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(live.close)
        jid = live.create_job({"id": "t-hf-adv", "persona": "marco", "template": "talking_head",
                               "script": "Quick test line."})
        self.assertEqual(live.advance(jid)["stage"], "brief")
        queued = Controller([hf.Queued()], {})
        done = Controller([hf.Completed()], {"images": [{"url": "https://cdn.example.test/still.png"}]})
        self.client.controllers.extend([queued, done])
        step = live.advance(jid)
        self.assertEqual(step["stage"], "still")
        self.assertTrue(step["pending"])
        self.assertEqual(live.db.latest(jid, "still")["status"], "running")
        self.assertEqual(len(self.client.submits), 1)
        self.assertEqual(self.client.submits[0][0], "alibaba/qwen-image-3/edit")
        step = live.advance(jid)
        self.assertEqual(step["action"], "poll")
        self.assertFalse(step["pending"])
        self.assertTrue(step["ok"])
        self.assertEqual(len(self.client.submits), 1)
        self.assertEqual(self.client.resumes, ["hf-req-1"])
        self.assertEqual(queued.get_calls, 0)
        self.assertEqual(done.get_calls, 1)
        run = live.db.latest(jid, "still")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["version"], 1)
        self.assertEqual(run["provider"], "higgsfield")
        self.assertTrue(Path(run["output_path"]).exists())

    def test_fallback_route_when_fal_locked(self):
        os.environ.update(FAL_KEY="dummy", ELEVENLABS_API_KEY="dummy")
        self.addCleanup(os.environ.pop, "FAL_KEY", None)
        self.addCleanup(os.environ.pop, "ELEVENLABS_API_KEY", None)
        c = cfg()
        c["_data_dir"] = TMP / "fallback"
        c["_db_file"] = TMP / "fallback" / "hf.db"
        opened = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(opened.close)
        self.assertIsNone(opened.fallback_route("still", c["stages"]["still"]))
        opened.db.add_attempt(job_id="t-lock", stage="still", attempt=1, mode="live", provider="fal", status="failed",
                              error='fal 403: {"detail":"User is locked. Reason: Exhausted balance."}',
                              est_cost_usd=0.05, actual_cost_usd=0.0, started_at="2099-01-01T00:00:00+00:00")
        locked = Runner(c, live=True, log=lambda m: None)
        self.addCleanup(locked.close)
        prov, sc = locked.provider_for("still")
        self.assertEqual((prov.name, sc["model"], sc["price_key"]),
                         ("higgsfield", "alibaba/qwen-image-3/edit", "hf_image"))
        self.assertEqual(locked.provider_for("video")[1]["model"], "alibaba/wan-3.0/image-to-video")
        self.assertEqual(locked.provider_for("lipsync")[1]["model"], "unconfigured")
        self.assertEqual(locked.provider_for("voice")[0].name, "elevenlabs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
