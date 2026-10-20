#!/usr/bin/env python3
"""Uncompressed size of the Vercel Python function, the way the builder sums it.

Installs requirements.txt with pip --target (wheels only), then adds project
files that survive the Python builder's predefined excludes plus the
excludeFiles glob on app/wsgi.py. Prints the total in MB. That total is the
bundle size when it is under the builder's pack threshold (about 225 MB):
below the threshold every dependency is zipped in, and runtime install is
not used.
"""
import fnmatch
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# packages/python predefinedExcludes, plus vercel.json functions["app/wsgi.py"].excludeFiles
EXCLUDES = [
    ".git/**",
    ".gitignore",
    ".vercel/**",
    ".pnpm-store/**",
    "**/node_modules/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/public/**",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/package-lock.json",
    "app/ui/node_modules/**",
    "app/ui/src/**",
    "app/tests/**",
    "app/screens/**",
    "app/tools/**",
    "app/models/**",
    "app/bin/**",
    "app/data/**",
    "public/**",
    "**/.git/**",
    "**/__pycache__/**",
    "**/*.pyc",
]


def _match(rel, pattern):
    rel = rel.replace(os.sep, "/")
    if fnmatch.fnmatch(rel, pattern):
        return True
    # micromatch '**' also matches a prefix directory of the pattern
    if pattern.endswith("/**") and (rel == pattern[:-3] or rel.startswith(pattern[:-3] + "/")):
        return True
    if fnmatch.fnmatch(rel, pattern.lstrip("/")):
        return True
    return False


def excluded(rel):
    return any(_match(rel, pattern) for pattern in EXCLUDES)


def tree_size(root, prefix=""):
    total = 0
    files = 0
    root = Path(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        name = f"{prefix}/{rel}" if prefix else rel
        if excluded(name.lstrip("/")) or excluded(rel):
            continue
        total += path.stat().st_size
        files += 1
    return total, files


def main():
    vendor = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="studio-vendor-"))
    if not any(vendor.iterdir()) if vendor.exists() else True:
        vendor.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet", "--target", str(vendor),
            "--only-binary=:all:", "-r", str(ROOT / "requirements.txt"),
        ])
    vendor_bytes, vendor_files = tree_size(vendor)
    source_bytes, source_files = tree_size(ROOT)
    total = vendor_bytes + source_bytes
    print(f"vendor {vendor_files} files {vendor_bytes/1024/1024:.2f} MB  ({vendor})")
    print(f"source {source_files} files {source_bytes/1024/1024:.2f} MB")
    print(f"uncompressed bundle {total/1024/1024:.2f} MB")
    # largest vendor distributions
    by_top = {}
    for path in vendor.rglob("*"):
        if path.is_file() and not excluded(str(path.relative_to(vendor))):
            top = path.relative_to(vendor).parts[0]
            by_top[top] = by_top.get(top, 0) + path.stat().st_size
    for name, size in sorted(by_top.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        print(f"  {size/1024/1024:7.2f} MB  {name}")
    return 0 if total < 180 * 1024 * 1024 else 1


if __name__ == "__main__":
    sys.exit(main())
