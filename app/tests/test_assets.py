"""Runtime assets stay out of the function bundle and come from Blob when present."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
os.environ.pop("STUDIO_FETCH_FFMPEG", None)
os.environ.pop("VERCEL", None)

from studio import storage
from studio.assets import cached_download, ffmpeg_bin, model_dir
from studio.compose import face_center
from studio import score


class Resp:
    def __init__(self, payload, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class HTTP:
    def __init__(self, blobs=None, body=b"x" * 20000):
        self.calls = []
        self.blobs = blobs or []
        self.body = body

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if "prefix" in (kw.get("params") or {}):
            return Resp({"blobs": self.blobs})
        return Resp({}, content=self.body)

    def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return Resp({"url": "https://abc123.public.blob.vercel-storage.com/" + kw["params"]["pathname"]})


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["BLOB_READ_WRITE_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)

    def test_blob_hit_skips_public_url(self):
        blob = "https://abc123.public.blob.vercel-storage.com/runtime/yunet.onnx"
        http = HTTP(blobs=[{"pathname": "runtime/yunet.onnx", "url": blob}])
        dest = self.tmp / "yunet.onnx"
        cached_download(dest, "https://example.invalid/yunet.onnx", "runtime/yunet.onnx", 10000, session=http)
        gets = [c for c in http.calls if c[0] == "GET"]
        self.assertEqual(gets[0][2]["params"]["prefix"], "runtime/yunet.onnx")
        self.assertEqual(gets[1][1], blob)
        self.assertEqual(dest.read_bytes(), http.body)
        self.assertFalse(any(c[0] == "PUT" for c in http.calls))
        cached_download(dest, "https://example.invalid/yunet.onnx", "runtime/yunet.onnx", 10000, session=http)
        self.assertEqual(len(http.calls), 2)

    def test_blob_miss_downloads_public_then_stores(self):
        http = HTTP()
        dest = self.tmp / "sface.onnx"
        cached_download(dest, "https://example.invalid/sface.onnx", "runtime/sface.onnx", 10000, session=http)
        self.assertEqual(http.calls[1][1], "https://example.invalid/sface.onnx")
        puts = [c for c in http.calls if c[0] == "PUT"]
        self.assertEqual(puts[0][2]["params"]["pathname"], "runtime/sface.onnx")
        self.assertEqual(puts[0][2]["headers"]["authorization"], "Bearer test-token")
        self.assertEqual(dest.read_bytes(), http.body)

    def test_local_models_and_ffmpeg_stay_offline(self):
        os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
        directory = model_dir()
        self.assertTrue((directory / "yunet.onnx").stat().st_size > 10000)
        self.assertTrue((directory / "sface.onnx").stat().st_size > 10000)
        os.environ.pop("VERCEL", None)
        os.environ.pop("STUDIO_FETCH_FFMPEG", None)
        path = ffmpeg_bin("ffmpeg")
        self.assertTrue(path == "ffmpeg" or Path(path).is_file())
        self.assertFalse((Path("/tmp/studio-bin") / "ffmpeg-linux64.tar.xz").exists())

    def test_face_center_without_opencv_uses_no_detector(self):
        with patch.object(score, "available", return_value=False):
            self.assertIsNone(face_center(self.tmp / "nope.mp4"))

    def test_opencv_optional_import(self):
        self.assertTrue(score.available())
        os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
        self.assertIsNone(storage.blob_url("runtime/yunet.onnx"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
