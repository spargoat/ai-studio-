"""Persona Blob refs, review buttons, export durability, and the ONNX scorer."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.pop("BLOB_READ_WRITE_TOKEN", None)

from studio.config import load_config
from studio.gates import show_review
from studio.images import prepare_for_fal
from studio.persona import entries, face_lock_entries, face_lock_locations
from studio import storage


def cfg():
    return load_config()


class Refs(unittest.TestCase):
    def test_face_lock_roles_and_primary(self):
        personas = cfg()["_personas"]
        marco = face_lock_entries(personas["marco"]["refs"])
        self.assertEqual([e["role"] for e in marco], ["identity_primary", "identity_secondary"])
        self.assertTrue(marco[0]["url"].endswith("face_front_v4.png"))
        self.assertIn("early to mid 20s", personas["marco"]["identity_ref_note"])
        self.assertTrue(all("early to mid 20s" in (e["note"] or "") for e in marco))
        self.assertNotIn("outfit", [e["role"] for e in marco])
        outfit = [e for e in entries(personas["marco"]["refs"]) if e["role"] == "outfit"]
        self.assertEqual(outfit[0]["name"], "outfit_build_only.png")

        maksim = face_lock_entries(personas["maksim"]["refs"])
        self.assertEqual([e["role"] for e in maksim],
                         ["identity_primary", "identity_secondary", "face_sheet_secondary"])
        self.assertNotEqual(maksim[0]["role"], "face_sheet_secondary")
        self.assertNotIn("outfit", [e["role"] for e in maksim])

        callum = face_lock_entries(personas["callum"]["refs"])
        self.assertEqual([e["role"] for e in callum], ["identity_primary"])
        roles = {e["role"] for e in entries(personas["callum"]["refs"])}
        self.assertEqual(roles, {"identity_primary", "character", "detail", "handwriting", "backstory"})
        self.assertTrue(all(e["sha256"] and e["url"].startswith("https://") for e in entries(personas["callum"]["refs"])))

    def test_face_sheet_alone_is_not_a_primary(self):
        raw = [{"role": "face_sheet_secondary", "url": "https://blob.vercel-storage.com/sheet.jpg", "sha256": "abc"}]
        self.assertEqual(face_lock_locations(raw), [])
        raw.insert(0, {"role": "identity_primary", "url": "https://blob.vercel-storage.com/front.jpg"})
        self.assertEqual(face_lock_locations(raw)[0], "https://blob.vercel-storage.com/front.jpg")
        self.assertEqual(face_lock_locations(raw)[-1], "https://blob.vercel-storage.com/sheet.jpg")

    def test_local_cache_checks_sha256(self):
        os.environ["STUDIO_TMP"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "STUDIO_TMP", None)
        body = b"face-bytes"
        digest = hashlib.sha256(body).hexdigest()

        class HTTP:
            def __init__(self):
                self.gets = 0

            def get(self, url, timeout=None):
                self.gets += 1
                return type("R", (), {"content": body, "raise_for_status": lambda self: None})()

        http = HTTP()
        url = "https://abc.public.blob.vercel-storage.com/refs/maksim/identity_c1_frontal.jpg"
        path = storage.local(url, getter=http, sha256=digest)
        self.assertEqual(Path(path).read_bytes(), body)
        self.assertEqual(storage.local(url, getter=http, sha256=digest), path)
        self.assertEqual(http.gets, 1)
        with self.assertRaises(storage.BlobError):
            storage.local(url + "?x=1", getter=http, sha256="0" * 64)

    def test_downscale_large_image(self):
        from PIL import Image
        src = Path(tempfile.mkdtemp()) / "outfit_build_only.png"
        Image.new("RGB", (3000, 1800), (20, 30, 40)).save(src)
        out = Path(prepare_for_fal(src))
        self.assertEqual(out.suffix, ".jpg")
        with Image.open(out) as im:
            self.assertLessEqual(max(im.size), 2048)
        small = Path(tempfile.mkdtemp()) / "face.jpg"
        Image.new("RGB", (640, 480), (1, 2, 3)).save(small, quality=80)
        self.assertEqual(prepare_for_fal(small), str(small))

    def test_classic_and_api_hide_review_after_approval(self):
        from studio.runner import Runner
        from studio.web import create_app
        data = Path(tempfile.mkdtemp())
        c = cfg()
        c["_data_dir"] = data
        c["_db_file"] = data / "studio.db"
        runner = Runner(c, log=lambda m: None)
        self.addCleanup(runner.close)
        jid = runner.create_job({"id": "t-rev", "persona": "marco", "template": "talking_head", "script": "One line."})
        self.assertEqual(runner.run_job(jid), "awaiting_review")
        client = create_app(c).test_client()
        page = client.get(f"/job/{jid}").data.decode()
        self.assertIn(f"/job/{jid}/review/still", page)
        self.assertIn(f"/job/{jid}/review/voice", page)
        self.assertNotIn(f"/job/{jid}/review/video", page)
        self.assertNotIn(f"/job/{jid}/review/lipsync", page)
        self.assertNotIn(f"/job/{jid}/review/finish", page)
        self.assertNotIn(f"/job/{jid}/review/compose", page)
        flags = {s["id"]: s["show_review"] for s in client.get(f"/api/jobs/{jid}").get_json()["stages"]}
        self.assertTrue(flags["still"] and flags["voice"])
        self.assertFalse(any(flags[s] for s in ("video", "lipsync", "finish", "compose")))
        runner.review(jid, "still", "approved", "ok")
        page = client.get(f"/job/{jid}").data.decode()
        self.assertNotIn(f"/job/{jid}/review/still", page)
        self.assertIn(f"/job/{jid}/review/voice", page)

    def test_review_buttons_only_when_pending(self):
        self.assertTrue(show_review("still", True, "succeeded", "pending"))
        self.assertTrue(show_review("voice", True, "succeeded", "pending"))
        self.assertTrue(show_review("script", True, "succeeded", "pending"))
        self.assertFalse(show_review("still", True, "succeeded", "approved"))
        self.assertFalse(show_review("still", True, "succeeded", "rejected"))
        self.assertFalse(show_review("still", False, "succeeded", "pending"))
        self.assertFalse(show_review("still", True, "failed", "pending"))
        for stage in ("video", "lipsync", "finish", "compose"):
            self.assertFalse(show_review(stage, True, "succeeded", "pending"))

    def test_persona_preview_redirects_to_blob(self):
        from studio.web import create_app
        c = cfg()
        c["_data_dir"] = Path(tempfile.mkdtemp())
        c["_db_file"] = c["_data_dir"] / "studio.db"
        client = create_app(c).test_client()
        page = client.get("/api/personas").get_json()
        marco = next(p for p in page if p["id"] == "marco")
        self.assertTrue(all(r["exists"] for r in marco["refs"]))
        self.assertEqual(marco["refs"][0]["role"], "identity_primary")
        self.assertIn("early to mid 20s", marco["identity_ref_note"])
        res = client.get("/api/personas/marco/ref/0")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.headers["Location"].endswith("/refs/marco/face_front_v4.png"))
        self.assertEqual(client.get("/api/personas/marco/ref/99").status_code, 404)
        callum = next(p for p in page if p["id"] == "callum")
        story = next(r for r in callum["refs"] if r["role"] == "backstory")
        self.assertFalse(story["image"])
        self.assertTrue(story["exists"])

    def test_mock_and_score_accept_a_url(self):
        from studio.providers.mock import MockProvider
        from studio import score
        photo = Path(__file__).resolve().parent.parent / "refs" / "maksim" / "01_front.jpg"
        url = "https://abc.public.blob.vercel-storage.com/refs/maksim/identity_c1_frontal.jpg"
        out = Path(tempfile.mkdtemp())
        with patch("studio.storage.local", return_value=str(photo)):
            res = MockProvider("mock", {}).do_image({
                "out_dir": str(out), "stage": "still", "attempt": 1, "fail_inject": {},
                "persona": "maksim", "persona_display": "Maksim", "model": "m",
                "params": {"num_images": 1}, "refs": [url], "ref_sha256": {},
            })
        self.assertTrue(res["meta"]["refs_used"])
        self.assertTrue(Path(res["output_path"]).stat().st_size > 1000)
        c = cfg()
        with patch("studio.score._cv", side_effect=ImportError):
            s = score.score_file(c, "maksim", str(photo), stage="still")
        self.assertGreater(s["identity"], 0.9)
        self.assertIsNotNone(s["visual"])
        with patch("studio.score._cv", side_effect=ImportError), \
             patch("studio.score_onnx.available", return_value=False):
            missing = score.score_file(c, "maksim", str(photo), stage="still")
        self.assertIsNone(missing["identity"])
        self.assertTrue(str(missing["identity_note"]).startswith("not scored"))

    def test_export_zip_is_uploaded_when_blob_is_on(self):
        from studio.web import create_app
        os.environ["BLOB_READ_WRITE_TOKEN"] = "vercel_blob_rw_abc123_secret"
        self.addCleanup(os.environ.pop, "BLOB_READ_WRITE_TOKEN", None)
        data = Path(tempfile.mkdtemp())
        c = cfg()
        c["_data_dir"] = data
        c["_db_file"] = data / "studio.db"
        blob = "https://abc123.public.blob.vercel-storage.com/exports/bundle-test.zip"
        app = create_app(c)
        client = app.test_client()
        with patch("studio.export.export", return_value=({"count": 1, "items": []}, str(data / "bundle-test.zip"))):
            (data / "bundle-test.zip").write_bytes(b"PK fake zip")
            with patch("studio.storage.put_file", return_value=blob) as put:
                res = client.post("/api/export", json={"ids": [1]})
        self.assertEqual(res.status_code, 200, res.data)
        body = res.get_json()
        self.assertEqual(body["url"], blob)
        self.assertEqual(body["zip"], blob)
        self.assertTrue(put.call_args[0][0].startswith("exports/"))
        self.assertEqual(put.call_args[0][2], "application/zip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
