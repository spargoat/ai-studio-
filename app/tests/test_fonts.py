"""Caption text must render without a system fontconfig."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")

from studio.assets import FONT_FAMILY, font_dir, font_file
from studio.compose import caption_face, compose
from studio.media import ff
from studio.providers.mock import MockProvider, _label


class T(unittest.TestCase):
    def test_mock_still_and_captions_without_fontconfig(self):
        saved = {k: os.environ.get(k) for k in ("FONTCONFIG_FILE", "FONTCONFIG_PATH")}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        bundled = font_dir()
        self.assertTrue((bundled / "DejaVuSans.ttf").is_file())
        self.assertTrue((bundled / "DejaVuSans-Bold.ttf").is_file())
        for label, value in (("unset", None), ("empty", "")):
            if value is None:
                os.environ.pop("FONTCONFIG_FILE", None)
                os.environ.pop("FONTCONFIG_PATH", None)
            else:
                os.environ["FONTCONFIG_FILE"] = ""
                os.environ["FONTCONFIG_PATH"] = ""
            with self.subTest(fontconfig=label):
                self._render()

    def _render(self):
        out = Path(tempfile.mkdtemp())
        label = _label(["DRY RUN"])
        self.assertIn(f"fontfile='{font_file()}'", label)
        self.assertNotIn("fontfile='None'", label)
        res = MockProvider("mock", {}).do_image({
            "out_dir": str(out), "persona": "marco", "persona_display": "Marco",
            "model": "mock-model", "refs": [], "params": {"num_images": 1},
            "stage": "still", "attempt": 1, "fail_inject": {},
        })
        still = Path(res["output_path"])
        self.assertGreater(still.stat().st_size, 100)
        video = out / "clip.mp4"
        ff(["-f", "lavfi", "-i", "color=c=0x333333:s=270x480:d=1:r=10",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)])
        fonts, family, face = caption_face({
            "font_name": "TWF Caption",
            "fonts_dir": "/workspace/creative/fonts/twf_caption",
        })
        self.assertEqual(fonts, font_dir())
        self.assertEqual(family, FONT_FAMILY)
        self.assertTrue(face.endswith("DejaVuSans-Bold.ttf"))
        result = compose(video, out / "comp", {
            "captions": {"enabled": True, "words_per_chunk": 2, "font_name": "TWF Caption",
                         "fonts_dir": "/workspace/creative/fonts/twf_caption"},
            "overlays": [{"text": "Hello", "start": 0, "end": 1, "pos": "top"}],
            "formats": ["9:16"],
            "framing": {"9:16": "pad"},
        }, script="Hello there friend", fast=True)
        produced = Path(next(iter(result["exports"].values())))
        self.assertGreater(produced.stat().st_size, 500, produced)
        ass = (out / "comp" / "captions_9x16.ass").read_text()
        self.assertIn("DejaVu Sans", ass)
        self.assertNotIn("TWF Caption", ass)
        self.assertGreater(result["words"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
