"""Scratch space on Vercel: a small /tmp budget, and an export zip that names the cut."""
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")

from studio import storage
from studio.assets import _unpack_ffmpeg, model_dir
from studio.config import load_config
from studio.persona import refs_for_stage
from studio.runner import Runner


def _cfg(root):
    c = load_config()
    c["_data_dir"] = root
    c["_db_file"] = root / "studio.db"
    return c


class HTTP:
    def __init__(self, body):
        self.body = body

    def get(self, url, timeout=None, stream=None):
        body = self.body

        class R:
            content = body

            def raise_for_status(self):
                return None

        return R()


class Tmp(unittest.TestCase):
    def setUp(self):
        self._env = {}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set(self, key, value):
        self._env.setdefault(key, os.environ.get(key))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def test_only_the_still_downloads_face_refs(self):
        personas = load_config()["_personas"]
        for kind in ("brief", "voice", "video", "lipsync", "finish", "compose", "script"):
            self.assertEqual(refs_for_stage(personas["marco"]["refs"], kind), [])
            self.assertEqual(refs_for_stage(personas["callum"]["refs"], kind), [])
        image = refs_for_stage(personas["marco"]["refs"], "image")
        self.assertEqual([e["role"] for e in image], ["identity_primary", "identity_secondary"])
        self.assertFalse(any("outfit" in (e.get("name") or "") for e in image))
        callum = refs_for_stage(personas["callum"]["refs"], "image")
        self.assertEqual([e["role"] for e in callum], ["identity_primary"])
        self.assertNotIn("handwriting", {e["role"] for e in callum})

    def test_small_tmp_budget(self):
        root = Path(tempfile.mkdtemp())
        self._set("STUDIO_TMP", str(root))
        self._set("STUDIO_TMP_BUDGET", "1000")
        small = "https://abc.public.blob.vercel-storage.com/refs/maksim/identity_c1_frontal.jpg"
        storage.local(small, getter=HTTP(b"x" * 400))
        outfit = "https://abc.public.blob.vercel-storage.com/refs/marco/outfit_build_only.png"
        with self.assertRaises(OSError) as caught:
            storage.local(outfit, getter=HTTP(b"y" * 5000))
        self.assertEqual(caught.exception.errno, 28)
        self.assertIn("tmp free", str(caught.exception))
        self.assertLess(storage.tmp_free(), 1000)
        for path in root.glob("*"):
            path.unlink()
        storage.local(small + "?again", getter=HTTP(b"z" * 400))
        self.assertGreater(storage.tmp_free(), 0)

        data = Path(tempfile.mkdtemp())
        self._set("STUDIO_TMP_BUDGET", None)
        seen = []
        real = storage.local

        def spy(path, getter=None, sha256=None):
            seen.append(str(path))
            return real(path, getter=getter, sha256=sha256)

        runner = Runner(_cfg(data), log=lambda m: None)
        self.addCleanup(runner.close)
        jid = runner.create_job({"id": "t-tmp", "persona": "maksim", "template": "talking_head",
                                 "script": "One short line."})
        self.assertEqual(runner.advance(jid)["stage"], "brief")
        with patch("studio.storage.local", spy):
            voice = runner.advance(jid, only="voice")
        self.assertTrue(voice["ok"], voice)
        blob = " ".join(seen)
        self.assertNotIn("outfit_build_only", blob)
        self.assertNotIn("handwriting", blob)
        self.assertNotIn("face_sheet_secondary", blob)
        request = next((data / "jobs" / jid).rglob("request.json"))
        body = request.read_text()
        self.assertNotIn("outfit_build_only", body)
        self.assertNotIn("handwriting", body)
        self.assertEqual(json.loads(body)["refs"], [])

    def test_failed_stage_logs_free_tmp(self):
        data = Path(tempfile.mkdtemp())
        self._set("STUDIO_TMP_BUDGET", "500000")
        self._set("STUDIO_TMP", str(data / "scratch"))
        runner = Runner(_cfg(data), log=lambda m: None, fail_inject={"voice": 5})
        self.addCleanup(runner.close)
        jid = runner.create_job({"id": "t-fail", "persona": "marco", "template": "talking_head",
                                 "script": "One short line."})
        runner.advance(jid)
        voice = runner.advance(jid, only="voice")
        self.assertFalse(voice["ok"])
        self.assertIn("tmp free", voice["error"])
        self.assertTrue(any("tmp free" in e["message"] for e in runner.db.events(jid)))

    def test_unpack_drops_archive_and_models_download_once(self):
        dest = Path(tempfile.mkdtemp())
        self._set("STUDIO_FFMPEG_DIR", str(dest))
        archive = dest / "ffmpeg-linux64.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            for name, payload in (("bin/ffmpeg", b"ffmpeg-bin"), ("bin/ffprobe", b"ffprobe-bin"),
                                  ("bin/ffplay", b"x" * 2000)):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(payload))
        _unpack_ffmpeg(archive)
        self.assertFalse(archive.exists())
        self.assertEqual((dest / "ffmpeg").read_bytes(), b"ffmpeg-bin")
        self.assertEqual((dest / "ffprobe").read_bytes(), b"ffprobe-bin")
        self.assertFalse((dest / "ffplay").exists())

        models = Path(tempfile.mkdtemp())
        self._set("STUDIO_MODEL_DIR", str(models))
        calls = []

        def fake(path, url, pathname, min_size, session=None):
            calls.append(pathname)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"m" * min_size)
            return Path(path)

        from studio import assets
        with patch.object(assets, "APP_DIR", Path(tempfile.mkdtemp())), patch.object(assets, "cached_download", fake):
            first = model_dir()
            second = model_dir()
        self.assertEqual(first, second)
        self.assertEqual(calls, ["runtime/yunet.onnx", "runtime/sface.onnx"])

    def test_publish_removes_the_job_directory(self):
        data = Path(tempfile.mkdtemp())
        self._set("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_abc123_secret")
        runner = Runner(_cfg(data), log=lambda m: None)
        self.addCleanup(runner.close)

        class R:
            status_code = 200
            text = ""
            content = b""

            def __init__(self, url):
                self._url = url

            def raise_for_status(self):
                return None

            def json(self):
                return {"url": self._url}

        class HTTPPut:
            def put(self, url, **kw):
                data = kw.get("data")
                if hasattr(data, "read"):
                    data.read()
                name = kw["params"]["pathname"]
                return R("https://abc123.public.blob.vercel-storage.com/" + name)

            def get(self, url, **kw):
                return R("")

        jid = runner.create_job({"id": "t-pub", "persona": "marco", "template": "talking_head",
                                 "script": "One short line."})
        with patch("studio.storage.requests", HTTPPut()):
            brief = runner.advance(jid)
        self.assertTrue(brief["ok"], brief)
        run = runner.db.latest(jid, "brief")
        self.assertTrue(str(run["output_path"]).startswith("https://"))
        self.assertFalse((data / "jobs" / jid).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
