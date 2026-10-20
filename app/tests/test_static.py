"""The Vercel build copies Vite's hashed files and refuses a broken index.html."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("stage_static", ROOT / "scripts" / "stage_static.py")
stage_static = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_static)


class T(unittest.TestCase):
    def test_real_build_assets_are_copied(self):
        public = Path(tempfile.mkdtemp())
        refs = stage_static.stage(ROOT / "app" / "ui" / "dist", public)
        self.assertIn("/assets/index-CDkQMb1V.js", refs)
        self.assertIn("/assets/index-C4dCJhso.css", refs)
        for ref in refs:
            copied = public / ref.lstrip("/")
            self.assertTrue(copied.is_file(), ref)
            self.assertGreater(copied.stat().st_size, 0)
        self.assertFalse((public / "index.html").exists())

    def test_missing_referenced_asset_fails_the_build(self):
        dist = Path(tempfile.mkdtemp()) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<script type="module" src="/assets/index-MISSING.js"></script>\n'
            '<link rel="stylesheet" href="/assets/index-MISSING.css">\n'
        )
        (dist / "assets" / "index-MISSING.css").write_bytes(b"body{}")
        with self.assertRaises(SystemExit) as caught:
            stage_static.stage(dist, Path(tempfile.mkdtemp()))
        self.assertIn("index-MISSING.js", str(caught.exception))
        self.assertIn("Vite build", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
