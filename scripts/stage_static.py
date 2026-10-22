#!/usr/bin/env python3
"""Copy the Vite build's hashed assets to public/ so Vercel serves them as static files.

index.html stays in app/ui/dist. Flask sends it after the password gate.
public/assets is not behind that gate: the CDN serves those files before Flask.
The script exits if index.html points at a file the copy did not produce.
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "app" / "ui" / "dist"
PUBLIC = ROOT / "public"
ASSET_REF = re.compile(r"""(?:src|href)=["'](/assets/[^"']+)["']""")


def asset_refs(html):
    return ASSET_REF.findall(html)


def stage(dist, public):
    dist, public = Path(dist), Path(public)
    index = dist / "index.html"
    if not index.is_file():
        raise SystemExit(f"UI build missing at {index}. Run: cd app/ui && npm ci && npm run build")
    assets = dist / "assets"
    if not assets.is_dir():
        raise SystemExit(f"no assets directory in {dist}")
    refs = asset_refs(index.read_text())
    if not refs:
        raise SystemExit(f"{index} has no /assets references")
    missing_src = [ref for ref in refs if not (dist / ref.lstrip("/")).is_file()]
    if missing_src:
        raise SystemExit("index.html asset paths missing from the Vite build: " + ", ".join(missing_src))
    target = public / "assets"
    if target.exists():
        shutil.rmtree(target)
    public.mkdir(parents=True, exist_ok=True)
    shutil.copytree(assets, target)
    for path in dist.iterdir():
        if path.name in ("index.html", "assets"):
            continue
        dest = public / path.name
        if path.is_file():
            shutil.copy2(path, dest)
    missing = [ref for ref in refs if not (public / ref.lstrip("/")).is_file()]
    if missing:
        raise SystemExit("index.html asset paths missing from deployed output: " + ", ".join(missing))
    return refs


def main():
    refs = stage(DIST, PUBLIC)
    print(f"static -> {PUBLIC} ({sum(1 for _ in (PUBLIC / 'assets').rglob('*') if _.is_file())} asset files; {len(refs)} referenced)")


if __name__ == "__main__":
    main()
