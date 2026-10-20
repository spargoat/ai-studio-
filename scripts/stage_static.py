#!/usr/bin/env python3
"""Copy the Vite build's hashed assets to public/ so Vercel serves them as static files.

index.html stays in app/ui/dist. Flask sends it after the password gate.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "app" / "ui" / "dist"
PUBLIC = ROOT / "public"


def main():
    if not DIST.exists():
        raise SystemExit(f"UI build missing at {DIST}. Run: cd app/ui && npm ci && npm run build")
    assets = DIST / "assets"
    if not assets.is_dir():
        raise SystemExit(f"no assets directory in {DIST}")
    target = PUBLIC / "assets"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(assets, target)
    for path in DIST.iterdir():
        if path.name in ("index.html", "assets"):
            continue
        dest = PUBLIC / path.name
        if path.is_file():
            shutil.copy2(path, dest)
    print(f"static -> {PUBLIC} ({sum(1 for _ in target.rglob('*'))} asset files)")


if __name__ == "__main__":
    main()
