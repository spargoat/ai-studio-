#!/usr/bin/env python3
"""Download face models and a caption font into /tmp for a local checkout.

Not part of the Vercel build. The function fetches these at runtime (Blob
cache, then the public URL). Putting them in the app tree would ship them
inside the function bundle.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from studio.assets import MODELS, font_path, model_dir  # noqa: E402


def main():
    directory = model_dir()
    print("models ->", directory)
    for name in MODELS:
        path = directory / name
        print(f"  {name} {path.stat().st_size} bytes")
    print("font ->", font_path())


if __name__ == "__main__":
    main()
