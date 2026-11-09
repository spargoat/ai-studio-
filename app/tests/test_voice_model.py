"""Per-persona voice model. No ElevenLabs calls: the adapter is mocked."""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
for k in ("FAL_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS", "STUDIO_ALLOW_LIVE"):
    os.environ.pop(k, None)

from studio.config import load_config
from studio.providers.elevenlabs import ElevenLabsProvider
from studio.runner import Runner

TMP = Path(tempfile.mkdtemp())


def cfg():
    c = load_config()
    c["_data_dir"] = TMP / "voice-model"
    c["_db_file"] = c["_data_dir"] / "studio.db"
    return c


class VoiceModel(unittest.TestCase):
    def test_callum_uses_eleven_v3_and_the_others_keep_multilingual_v2(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        self.assertEqual(r.cfg["stages"]["voice"]["model"], "eleven_multilingual_v2")
        self.assertEqual(r.cfg["stages"]["voice"]["price_key"], "elevenlabs_multilingual_v2")
        callum = r.cfg["_personas"]["callum"]
        self.assertEqual(callum["voice_id"], "7WFWk7zmM49WQZguCHGx")
        self.assertEqual(callum["voice_name"], "Callum - draft (take M5, eleven_v3)")
        self.assertEqual(callum["voice_model"], "eleven_v3")
        self.assertEqual(callum["voice_settings"], {
            "stability": 0.45, "similarity_boost": 0.8, "style": 0.1, "use_speaker_boost": True,
        })
        expect = {
            "callum": ("eleven_v3", "elevenlabs_v3"),
            "marco": ("eleven_multilingual_v2", "elevenlabs_multilingual_v2"),
            "maksim": ("eleven_multilingual_v2", "elevenlabs_multilingual_v2"),
        }
        for persona, (model, price_key) in expect.items():
            brief = {"persona": persona, "template": "talking_head", "script": "One short line."}
            job = {"id": f"t-{persona}", "template": "talking_head", "brief": brief}
            _provider, sc = r.provider_for("voice", job=job)
            self.assertEqual(sc["provider"], "elevenlabs", persona)
            self.assertEqual(sc["model"], model, persona)
            self.assertEqual(sc["price_key"], price_key, persona)
            voice = next(row for row in r.estimate_job(dict(brief)) if row["stage"] == "voice")
            self.assertEqual(voice["model"], model, persona)
            self.assertEqual(voice["usd_per_unit"], 0.08, persona)

    def test_elevenlabs_posts_the_resolved_model_and_does_not_call_the_network(self):
        r = Runner(cfg(), log=lambda m: None)
        self.addCleanup(r.close)
        brief = {"persona": "callum", "template": "talking_head", "script": "One short line."}
        job = {"id": "t-post", "template": "talking_head", "brief": brief}
        _provider, sc = r.provider_for("voice", live=True, job=job)
        self.assertIsInstance(_provider, ElevenLabsProvider)
        posted = {}

        class Response:
            status_code = 200
            content = b"ID3fake"
            headers = {"character-cost": "16", "request-id": "req-test"}
            text = ""

            def json(self):
                return {}

        def post(url, params=None, json=None, headers=None, timeout=None):
            posted["url"] = url
            posted["json"] = json
            return Response()

        out = TMP / "voice-out"
        out.mkdir(parents=True, exist_ok=True)
        with patch("studio.providers.elevenlabs.requests.post", post), \
             patch("studio.providers.elevenlabs.duration", return_value=1.0), \
             patch("studio.providers.elevenlabs.get_key", return_value="test-key"):
            res = _provider.do_voice({
                "script": brief["script"], "model": sc["model"], "voice_id": r.cfg["_personas"]["callum"]["voice_id"],
                "voice_settings": r.cfg["_personas"]["callum"]["voice_settings"], "out_dir": str(out),
            })
        self.assertEqual(posted["json"]["model_id"], "eleven_v3")
        self.assertIn("/text-to-speech/7WFWk7zmM49WQZguCHGx", posted["url"])
        self.assertEqual(res["meta"]["voice_id"], "7WFWk7zmM49WQZguCHGx")
        self.assertEqual(posted["json"]["voice_settings"]["stability"], 0.45)


if __name__ == "__main__":
    unittest.main(verbosity=2)
