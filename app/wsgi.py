"""Vercel Flask entrypoint. The React build is static; this serves /api and the password gate.

`/api/inngest` is registered by create_app and is not behind the studio password.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio.config import load_config
from studio.web import create_app

app = create_app(load_config(), live=os.environ.get("STUDIO_LIVE") == "1")
