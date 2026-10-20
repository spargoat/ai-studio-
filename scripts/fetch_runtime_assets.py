#!/usr/bin/env python3
"""Download face models, a caption font, and a static ffmpeg into the app tree.

Run from the repo root during the Vercel build. Files land in gitignored
directories (app/models, app/fonts, app/bin) and are bundled with the function.
ffmpeg stays compressed; the function unpacks it to /tmp on first use.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from studio.assets import FFMPEG_URL, MODELS, font_path, model_dir  # noqa: E402
from studio.assets import _download  # noqa: E402


def main():
    print("models ->", model_dir())
    for name in MODELS:
        path = ROOT / "app" / "models" / name
        print(f"  {name} {path.stat().st_size} bytes")
    font = font_path()
    print("font ->", font)
    archive = ROOT / "app" / "bin" / "ffmpeg-linux64.tar.xz"
    if not archive.exists() or archive.stat().st_size < 1_000_000:
        print("ffmpeg ->", archive)
        _download(FFMPEG_URL, archive)
    print(f"  ffmpeg archive {archive.stat().st_size} bytes")


if __name__ == "__main__":
    main()
