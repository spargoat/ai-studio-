"""Intel in the original UI. The market timeline is a read, not a canvas.
Fixture data is never labelled live. A failed live read does not fall back.
Connections expose a mask, never the secret. A new job stays a dry run.
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for k in ("ADINTEL_API_URL", "ADINTEL_API_SECRET", "FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS",
          "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)

from studio.adintel import FIXTURE_LABEL, LONGEVITY_NOTE, AdIntelError, market, timeline_view
from studio.config import connection_status, load_config
from studio.web import create_app

TMP = Path(tempfile.mkdtemp(prefix="intel-"))


def cfg():
    c = load_config()
    c["_data_dir"] = TMP
    c["_db_file"] = TMP / "studio.db"
    return c


class T(unittest.TestCase):
    def test_fixture_keeps_evidence_and_inference_apart(self):
        view = timeline_view(None)
        self.assertEqual(view["origin"], "fixture")
        self.assertIs(view["live"], False)
        self.assertEqual(view["label"], FIXTURE_LABEL)
        evidence, inference = view["evidence"], view["inference"]
        self.assertIs(evidence["live"], False)
        self.assertEqual(evidence["source"], "ads-tracker fixture")
        self.assertIn("data_as_of", evidence)
        self.assertIn("points", evidence)
        self.assertTrue(evidence["events"])
        self.assertNotIn("points", inference)
        self.assertNotIn("events", inference)
        self.assertIn("reading", inference["note"])
        self.assertNotIn(inference["note"], json.dumps(evidence))
        self.assertEqual(inference["longevity_note"], LONGEVITY_NOTE)
        self.assertNotIn("longevity_note", evidence)

    def test_event_click_emits_that_ad(self):
        view = timeline_view("ev_removed_fieldnote")
        self.assertEqual(view["emitted_ad_id"], "rec_77aa10")
        self.assertEqual(view["evidence"]["ad"]["headline"], "Seven checks")
        self.assertEqual(view["evidence"]["ad"]["status"], "removed")
        self.assertEqual(view["evidence"]["emitted_event"]["label"], "Fieldnote removed")
        self.assertNotIn("rec_77aa10", json.dumps(view["inference"]))

    def test_live_failure_does_not_use_the_fixture(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "ADINTEL_API_URL", None)
        self.addCleanup(os.environ.pop, "ADINTEL_API_SECRET", None)

        class Boom:
            status_code = 503
            def json(self):
                return {}

        with patch("studio.adintel.requests.get", return_value=Boom()):
            with self.assertRaises(AdIntelError):
                market()
        self.assertNotIn("super-secret-value", str(AdIntelError("x")))

    def test_live_read_sends_the_bearer_and_stays_live(self):
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "ADINTEL_API_URL", None)
        self.addCleanup(os.environ.pop, "ADINTEL_API_SECRET", None)

        class Resp:
            status_code = 200
            def json(self):
                return {"data_as_of": "2026-10-19", "points": [{"date": "2026-10-01", "reach": 10, "live_ads": 1}],
                        "events": [{"id": "e1", "date": "2026-10-01", "kind": "launched", "ad_id": "live1", "label": "Live launch"}]}

        def fake_get(url, params=None, timeout=None, headers=None):
            if url.endswith("/market"):
                return Resp()
            ad = Resp()
            ad.json = lambda: {"id": "live1", "advertiser": "Northline", "status": "active", "headline": "From the API"}
            return ad

        with patch("studio.adintel.requests.get", side_effect=fake_get) as get:
            view = timeline_view("e1")
        self.assertEqual(view["origin"], "live")
        self.assertIs(view["evidence"]["live"], True)
        self.assertEqual(view["emitted_ad_id"], "live1")
        self.assertEqual(view["evidence"]["ad"]["status"], "live")
        self.assertNotIn("September", view["inference"]["note"])
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer super-secret-value")
        self.assertNotIn("super-secret-value", json.dumps(view))
        self.assertNotIn("tracker.example", json.dumps(view))

    def test_connections_mask_the_secret(self):
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        os.environ["ADINTEL_API_URL"] = "https://tracker.example/private"
        self.addCleanup(os.environ.pop, "ADINTEL_API_SECRET", None)
        self.addCleanup(os.environ.pop, "ADINTEL_API_URL", None)
        rows = connection_status(cfg())
        blob = json.dumps(rows)
        self.assertNotIn("super-secret-value", blob)
        self.assertNotIn("tracker.example", blob)
        secret = next(r for r in rows if r["id"] == "adintel_secret")
        self.assertTrue(secret["set"])
        self.assertEqual(secret["mask"], "••••••••")

    def test_http_timeline_settings_and_dry_default(self):
        os.environ["STUDIO_ONE_STAGE"] = "1"
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "STUDIO_ONE_STAGE", None)
        self.addCleanup(os.environ.pop, "ADINTEL_API_SECRET", None)
        client = create_app(cfg()).test_client()
        timeline = client.get("/api/intel/timeline?event_id=ev_launch_northline")
        self.assertEqual(timeline.status_code, 200, timeline.data)
        body = timeline.get_json()
        self.assertEqual(body["origin"], "fixture")
        self.assertIs(body["live"], False)
        self.assertEqual(body["emitted_ad_id"], "rec_4c91e2")
        self.assertNotIn(b"super-secret-value", timeline.data)
        settings = client.get("/api/settings")
        payload = settings.get_json()
        self.assertTrue(all(set(k) == {"provider", "env", "set", "optional"} for k in payload["keys"]))
        secret = next(r for r in payload["connections"] if r["id"] == "adintel_secret")
        self.assertEqual(secret["mask"], "••••••••")
        self.assertNotIn(b"super-secret-value", settings.data)
        meta = client.get("/api/meta").get_json()
        self.assertIn("fal", meta["template_keys"]["talking_head"])
        self.assertIn("elevenlabs", meta["template_keys"]["talking_head"])
        self.assertIn("llm", meta["template_keys"]["talking_head_scripted"])
        created = client.post("/api/jobs", json={"brief": {"persona": "marco", "template": "talking_head", "script": "Stop first.", "id": "t-intel-dry"}})
        self.assertEqual(created.status_code, 201, created.data)
        detail = client.get("/api/jobs/t-intel-dry").get_json()
        self.assertEqual(detail["job"]["mode"], "dry")
        by = {s["id"]: s for s in detail["stages"]}
        self.assertEqual(by["still"]["key_provider"], "fal")
        self.assertEqual(by["voice"]["key_provider"], "elevenlabs")
        self.assertIsNone(by["brief"]["key_provider"])
        os.environ["ADINTEL_API_URL"] = "https://tracker.example"
        os.environ["ADINTEL_API_SECRET"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "ADINTEL_API_URL", None)

        class Down:
            status_code = 500
            def json(self):
                return {"error": "no"}

        with patch("studio.adintel.requests.get", return_value=Down()):
            failed = client.get("/api/intel/timeline")
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn(b"Fixture", failed.data)
        self.assertNotIn(b"Northline", failed.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
