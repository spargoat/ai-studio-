"""Vercel entry point. The Flask app serves the UI and the Inngest endpoint.

Set the Vercel project root to this directory (`app/`). Inngest invokes
`/api/inngest`; job rows stay in the studio database.
"""
import os

from studio.config import load_config
from studio.web import create_app

app = create_app(load_config(), live=os.environ.get("STUDIO_LIVE") == "1")
