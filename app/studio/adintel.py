"""Read-only client for the ads-tracker API.

The tracker stays a separate repo. This module calls its read endpoints when
ADINTEL_API_URL and ADINTEL_API_SECRET are both set. Otherwise it serves a
fixture of recorded sample rows. Fixture results are labelled and never
reported as a live read. The secret is sent as a bearer token and is never
logged or returned.
"""
import os

import requests

FIXTURE_AS_OF = "2026-10-18"
FIXTURE_SOURCE = "ads-tracker fixture"
FIXTURE_LABEL = "Fixture data — recorded sample, not a live tracker read"
LIVE_SOURCE = "ads-tracker"
LIVE_LABEL = "Live ads-tracker read"
LONGEVITY_NOTE = "a signal, not proof of spend or results"
REFERENCE_LABEL = "pattern reference for ideation only, NOT copy-blueprint"

# Recorded-style rows. Status "live"/"removed" is the ad's market state, not
# whether this process is talking to the tracker.
FIXTURE_ADS = [
    {
        "id": "rec_4c91e2",
        "advertiser": "Northline Markets",
        "status": "live",
        "first_seen": "2026-08-02",
        "last_seen": "2026-10-17",
        "longevity_days": 76,
        "primary_text": "Most accounts don't blow up on the idea. They blow up on size. If you can't point at the price that proves you wrong, you don't have a trade yet.",
        "headline": "Name the stop first",
        "cta": "Learn more",
        "media": [{"type": "video", "url": "https://example.test/ads/northline-stop-first.mp4"}],
        "landing_url": "https://northlinemarkets.example/stop-first",
        "creative_family": "loss-then-rule",
        "platforms": ["facebook", "instagram"],
    },
    {
        "id": "rec_b17d04",
        "advertiser": "Harbor & Co",
        "status": "live",
        "first_seen": "2026-09-26",
        "last_seen": "2026-10-17",
        "longevity_days": 21,
        "primary_text": "I kept a note of the last trade I walked away from. That was the week the account stopped leaking.",
        "headline": "The trade I didn't take",
        "cta": "See the note",
        "media": [{"type": "video", "url": "https://example.test/ads/harbor-note.mp4"}],
        "landing_url": "https://harborandco.example/note",
        "creative_family": "testimonial-bridge",
        "platforms": ["instagram"],
    },
    {
        "id": "rec_77aa10",
        "advertiser": "Fieldnote",
        "status": "removed",
        "first_seen": "2026-09-01",
        "last_seen": "2026-10-11",
        "longevity_days": 40,
        "primary_text": "Seven checks before you put a ticket on. Number four is the one people skip.",
        "headline": "Seven checks",
        "cta": "Open the list",
        "media": [{"type": "image", "url": "https://example.test/ads/fieldnote-seven.jpg"}],
        "landing_url": "https://fieldnote.example/seven",
        "creative_family": "curiosity-list",
        "platforms": ["facebook"],
    },
]

FIXTURE_FUNNELS = {
    "rec_4c91e2": {
        "steps": [
            {"n": 1, "kind": "ad", "label": "In-feed video"},
            {"n": 2, "kind": "click", "label": "Link click"},
            {"n": 3, "kind": "landing", "label": "Stop-first checklist", "url": "https://northlinemarkets.example/stop-first"},
        ],
        "landing_page": {"url": "https://northlinemarkets.example/stop-first", "title": "Stop-first checklist"},
    },
    "rec_b17d04": {
        "steps": [
            {"n": 1, "kind": "ad", "label": "In-feed video"},
            {"n": 2, "kind": "click", "label": "Link click"},
            {"n": 3, "kind": "landing", "label": "The note", "url": "https://harborandco.example/note"},
        ],
        "landing_page": {"url": "https://harborandco.example/note", "title": "The note"},
    },
    "rec_77aa10": {
        "steps": [
            {"n": 1, "kind": "ad", "label": "Static image"},
            {"n": 2, "kind": "click", "label": "Link click"},
            {"n": 3, "kind": "landing", "label": "Numbered list", "url": "https://fieldnote.example/seven"},
        ],
        "landing_page": {"url": "https://fieldnote.example/seven", "title": "Seven checks"},
    },
}

# Observed series. Launch and removal markers match the ad rows above.
FIXTURE_POINTS = [
    {"date": "2026-08-03", "reach": 42000, "live_ads": 1},
    {"date": "2026-08-17", "reach": 61000, "live_ads": 1},
    {"date": "2026-08-31", "reach": 74000, "live_ads": 1},
    {"date": "2026-09-07", "reach": 98000, "live_ads": 2},
    {"date": "2026-09-21", "reach": 151000, "live_ads": 2},
    {"date": "2026-10-05", "reach": 142000, "live_ads": 2},
    {"date": "2026-10-17", "reach": 128000, "live_ads": 2},
]
FIXTURE_EVENTS = [
    {"id": "ev_launch_northline", "date": "2026-08-02", "kind": "launched", "ad_id": "rec_4c91e2", "label": "Northline Markets launched"},
    {"id": "ev_launch_fieldnote", "date": "2026-09-01", "kind": "launched", "ad_id": "rec_77aa10", "label": "Fieldnote launched"},
    {"id": "ev_launch_harbor", "date": "2026-09-26", "kind": "launched", "ad_id": "rec_b17d04", "label": "Harbor & Co launched"},
    {"id": "ev_removed_fieldnote", "date": "2026-10-11", "kind": "removed", "ad_id": "rec_77aa10", "label": "Fieldnote removed"},
]


class AdIntelError(RuntimeError):
    pass


def configured():
    return bool(os.environ.get("ADINTEL_API_URL") and os.environ.get("ADINTEL_API_SECRET"))


def _envelope(origin, data_as_of=None):
    if origin == "live":
        return {"origin": "live", "live": True, "label": LIVE_LABEL, "source": LIVE_SOURCE,
                "data_as_of": data_as_of or FIXTURE_AS_OF}
    return {"origin": "fixture", "live": False, "label": FIXTURE_LABEL, "source": FIXTURE_SOURCE,
            "data_as_of": FIXTURE_AS_OF}


def _get(path, params=None):
    base = (os.environ.get("ADINTEL_API_URL") or "").rstrip("/")
    secret = os.environ.get("ADINTEL_API_SECRET") or ""
    query = {k: v for k, v in (params or {}).items() if v not in ("", None)}
    try:
        res = requests.get(base + path, params=query, timeout=20,
                           headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"})
    except requests.RequestException as e:
        raise AdIntelError(f"ads-tracker read failed: {type(e).__name__}") from None
    if res.status_code >= 400:
        raise AdIntelError(f"ads-tracker read failed ({res.status_code})")
    try:
        return res.json()
    except ValueError:
        raise AdIntelError("ads-tracker read failed: response was not JSON") from None


def _rows(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("ads", "records", "data"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def normalize_ad(raw):
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "").lower()
    if status in ("active", "running"):
        status = "live"
    elif status in ("inactive", "deleted", "paused"):
        status = "removed"
    media = raw.get("media")
    if isinstance(media, str):
        media = [{"type": "unknown", "url": media}]
    if not media and raw.get("media_url"):
        media = [{"type": raw.get("media_type") or "unknown", "url": raw["media_url"]}]
    ad = {
        "id": str(raw.get("id") or raw.get("ad_id") or ""),
        "advertiser": raw.get("advertiser") or raw.get("page_name") or "",
        "status": status or "live",
        "first_seen": raw.get("first_seen") or raw.get("start_date") or "",
        "last_seen": raw.get("last_seen") or raw.get("end_date") or "",
        "longevity_days": raw.get("longevity_days") if raw.get("longevity_days") is not None else raw.get("days"),
        "primary_text": raw.get("primary_text") or raw.get("body") or raw.get("copy") or "",
        "headline": raw.get("headline") or "",
        "cta": raw.get("cta") or raw.get("call_to_action") or "",
        "media": media or [],
        "landing_url": raw.get("landing_url") or raw.get("destination") or "",
        "creative_family": raw.get("creative_family") or raw.get("family") or "",
        "platforms": raw.get("platforms") or [],
    }
    return ad if ad["id"] else None


def _match(ad, filters):
    filters = filters or {}
    advertiser = (filters.get("advertiser") or "").strip().lower()
    if advertiser and advertiser not in (ad.get("advertiser") or "").lower():
        return False
    status = (filters.get("status") or "").strip().lower()
    if status in ("live", "removed") and ad.get("status") != status:
        return False
    if filters.get("date_from") and ad.get("last_seen") and ad["last_seen"] < filters["date_from"]:
        return False
    if filters.get("date_to") and ad.get("first_seen") and ad["first_seen"] > filters["date_to"]:
        return False
    try:
        minimum = int(filters.get("longevity_min") or filters.get("longevity_min_days") or 0)
    except (TypeError, ValueError):
        minimum = 0
    if minimum and int(ad.get("longevity_days") or 0) < minimum:
        return False
    return True


def _as_of(body):
    if isinstance(body, dict):
        return body.get("data_as_of") or body.get("as_of")
    return None


def query_ads(filters=None):
    filters = filters or {}
    if not configured():
        pack = _envelope("fixture")
        pack["ads"] = [dict(a) for a in FIXTURE_ADS if _match(a, filters)]
        return pack
    body = _get("/ads", {
        "advertiser": filters.get("advertiser"),
        "date_from": filters.get("date_from"),
        "date_to": filters.get("date_to"),
        "status": filters.get("status"),
        "longevity_min": filters.get("longevity_min") or filters.get("longevity_min_days"),
    })
    pack = _envelope("live", _as_of(body))
    pack["ads"] = [a for a in (normalize_ad(r) for r in _rows(body)) if a and _match(a, filters)]
    return pack


def ad_by_id(ad_id):
    if not ad_id:
        return None
    if not configured():
        return next((dict(a) for a in FIXTURE_ADS if a["id"] == ad_id), None)
    body = _get(f"/ads/{ad_id}")
    raw = body.get("ad") if isinstance(body, dict) and isinstance(body.get("ad"), dict) else body
    return normalize_ad(raw)


def funnel_for(ad_id):
    ad = ad_by_id(ad_id) or {}
    if not configured():
        mapped = FIXTURE_FUNNELS.get(ad_id) or {"steps": [], "landing_page": {"url": ad.get("landing_url") or "", "title": ""}}
        return {"ad_id": ad_id, **mapped}
    body = _get(f"/ads/{ad_id}/funnel")
    if not isinstance(body, dict):
        body = {}
    steps = body.get("steps") or body.get("route") or []
    landing = body.get("landing_page") or {"url": ad.get("landing_url") or body.get("url") or "", "title": body.get("landing_title") or ""}
    return {"ad_id": ad_id, "steps": steps, "landing_page": landing}


def market():
    if not configured():
        pack = _envelope("fixture")
        pack["points"] = [dict(p) for p in FIXTURE_POINTS]
        pack["events"] = [dict(e) for e in FIXTURE_EVENTS]
        return pack
    body = _get("/market")
    if not isinstance(body, dict):
        body = {}
    pack = _envelope("live", _as_of(body))
    points, events = [], []
    for raw in body.get("points") or body.get("series") or []:
        if not isinstance(raw, dict) or not raw.get("date"):
            continue
        points.append({"date": raw["date"], "reach": raw.get("reach") or 0, "live_ads": raw.get("live_ads") or raw.get("live") or 0})
    for raw in body.get("events") or []:
        if not isinstance(raw, dict):
            continue
        events.append({"id": str(raw.get("id") or raw.get("date") or ""), "date": raw.get("date") or "",
                       "kind": raw.get("kind") or "event", "ad_id": raw.get("ad_id") or "",
                       "label": raw.get("label") or raw.get("kind") or "event"})
    pack["points"] = points
    pack["events"] = events
    return pack


FIXTURE_INFERENCE = (
    "The September reach step-up sits beside two ads in market. That alignment is our reading, "
    "not a tracker field. Reach and the live count are the recorded series."
)
LIVE_INFERENCE = (
    "Any alignment between reach and the live-ad count is our reading, not a tracker field. "
    "Reach and the live count are the recorded series."
)


def timeline_view(event_id=None):
    """Market series plus the ad a clicked event emits. Evidence and inference stay apart."""
    pack = market()
    events = pack.get("events") or []
    event = next((e for e in events if e.get("id") == event_id), None)
    if event is None and events:
        event = events[0]
    ad = ad_by_id((event or {}).get("ad_id")) if event else None
    evidence = {
        "source": pack.get("source"),
        "data_as_of": pack.get("data_as_of"),
        "origin": pack.get("origin"),
        "live": pack.get("live"),
        "label": pack.get("label"),
        "points": pack.get("points") or [],
        "events": events,
        "emitted_event": event,
        "ad": ad,
    }
    inference = {"note": FIXTURE_INFERENCE if pack.get("origin") != "live" else LIVE_INFERENCE}
    if ad and ad.get("longevity_days") is not None:
        inference["longevity_note"] = LONGEVITY_NOTE
    return {
        "origin": pack.get("origin"),
        "live": pack.get("live"),
        "label": pack.get("label"),
        "emitted_ad_id": (ad or {}).get("id"),
        "evidence": evidence,
        "inference": inference,
    }
