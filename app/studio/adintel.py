"""Read-only client for the ads-tracker API.

The tracker stays a separate repo. This module adapts its /api/board read
contract when ADINTEL_API_URL and ADINTEL_API_SECRET are both set. Otherwise
it serves labelled fixtures. No failed live read falls back to a fixture.
"""
import os
import re
from urllib.parse import quote

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
        "id": "rec_n2a81c",
        "advertiser": "Northline Markets",
        "status": "live",
        "first_seen": "2026-09-12",
        "last_seen": "2026-10-17",
        "longevity_days": 35,
        "primary_text": "Write the price that proves you wrong on the ticket before you write the size.",
        "headline": "The price on the ticket",
        "cta": "See the rule",
        "media": [{"type": "video", "url": "https://example.test/ads/northline-ticket.mp4"}],
        "landing_url": "https://northlinemarkets.example/ticket",
        "creative_family": "loss-then-rule",
        "platforms": ["facebook"],
    },
    {
        "id": "rec_n39c0e",
        "advertiser": "Northline Markets",
        "status": "removed",
        "first_seen": "2026-07-20",
        "last_seen": "2026-08-18",
        "longevity_days": 29,
        "primary_text": "A bigger size does not fix a trade that had no stop.",
        "headline": "Size is not a plan",
        "cta": "Read the note",
        "media": [{"type": "image", "url": "https://example.test/ads/northline-size.jpg"}],
        "landing_url": "https://northlinemarkets.example/size",
        "creative_family": "loss-then-rule",
        "platforms": ["instagram"],
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
    "rec_n2a81c": {
        "steps": [
            {"n": 1, "kind": "ad", "label": "In-feed video"},
            {"n": 2, "kind": "click", "label": "Link click"},
            {"n": 3, "kind": "landing", "label": "Ticket rule", "url": "https://northlinemarkets.example/ticket"},
        ],
        "landing_page": {"url": "https://northlinemarkets.example/ticket", "title": "Ticket rule"},
    },
    "rec_n39c0e": {
        "steps": [
            {"n": 1, "kind": "ad", "label": "Static image"},
            {"n": 2, "kind": "click", "label": "Link click"},
            {"n": 3, "kind": "landing", "label": "Size note", "url": "https://northlinemarkets.example/size"},
        ],
        "landing_page": {"url": "https://northlinemarkets.example/size", "title": "Size note"},
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


class AdIntelNotFound(AdIntelError):
    pass


def configured():
    return bool(os.environ.get("ADINTEL_API_URL") and os.environ.get("ADINTEL_API_SECRET"))


def _envelope(origin, data_as_of=None, source=None):
    if origin == "live":
        return {"origin": "live", "live": True, "label": LIVE_LABEL,
                "source": f"{LIVE_SOURCE} · {source}" if source else LIVE_SOURCE,
                "data_as_of": data_as_of}
    return {"origin": "fixture", "live": False, "label": FIXTURE_LABEL, "source": FIXTURE_SOURCE,
            "data_as_of": FIXTURE_AS_OF}


def _get(path, params=None):
    base = (os.environ.get("ADINTEL_API_URL") or "").rstrip("/")
    if not base.endswith("/api/board"):
        base += "/api/board"
    secret = os.environ.get("ADINTEL_API_SECRET") or ""
    query = {k: v for k, v in (params or {}).items() if v not in ("", None)}
    try:
        res = requests.get(base + path, params=query, timeout=20,
                           headers={"x-adintel-secret": secret, "Accept": "application/json"})
    except requests.RequestException as e:
        raise AdIntelError(f"ads-tracker read failed: {type(e).__name__}") from None
    if res.status_code == 404:
        raise AdIntelNotFound("This record is not in the current tracker snapshot")
    if res.status_code >= 400:
        raise AdIntelError(f"ads-tracker read failed ({res.status_code})")
    try:
        body = res.json()
    except ValueError:
        raise AdIntelError("ads-tracker read failed: response was not JSON") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise AdIntelError("ads-tracker read failed: invalid board API response")
    return body


def _meta(body):
    evidence = body.get("evidence") or {}
    return _envelope("live", evidence.get("asOf"), evidence.get("source"))


def normalize_ad(raw):
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "unknown").lower()
    if status not in ("live", "stopped", "missing", "removed", "unknown"):
        status = "unknown"
    media = raw.get("media")
    if isinstance(media, str):
        media = [{"type": "unknown", "url": media}]
    if not media and raw.get("media_url"):
        media = [{"type": raw.get("media_type") or "unknown", "url": raw["media_url"]}]
    platforms = raw.get("platforms") or []
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",") if p.strip()]
    ad = {
        "id": str(raw.get("id") or raw.get("ad_id") or ""),
        "advertiser": raw.get("advertiser") or raw.get("page_name") or "",
        "status": status,
        "status_label": raw.get("statusLabel") or status,
        "first_seen": raw.get("firstSeen") or raw.get("first_seen") or "",
        "last_seen": raw.get("lastObserved") or raw.get("last_seen") or "",
        "longevity_days": raw.get("daysRunning") if raw.get("daysRunning") is not None else raw.get("longevity_days"),
        "primary_text": raw.get("copy") or raw.get("primary_text") or "",
        "headline": raw.get("headline") or "",
        "cta": raw.get("cta") or raw.get("call_to_action") or "",
        "media": media or [],
        "format": raw.get("format") or "",
        "landing_url": raw.get("destinationUrl") or raw.get("landing_url") or "",
        "library_url": raw.get("libraryUrl") or "",
        "creative_family": raw.get("treatment") or raw.get("creative_family") or "",
        "platforms": platforms,
        "page_id": raw.get("pageId") or raw.get("page_id") or "",
        "funnel": raw.get("funnel") or "",
        "assigned_funnel": raw.get("assignedFunnel") or "",
        "funnel_id": str(raw.get("funnelId") or raw.get("assignedFunnelId") or ""),
        "as_of": raw.get("asOf"),
    }
    return ad if ad["id"] else None


def _match(ad, filters):
    filters = filters or {}
    advertiser = (filters.get("advertiser") or "").strip().lower()
    if advertiser and advertiser not in (ad.get("advertiser") or "").lower():
        return False
    status = (filters.get("status") or "").strip().lower()
    if status in ("live", "stopped", "missing", "removed", "unknown") and ad.get("status") != status:
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


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_SVG_BLOCK = re.compile(r"<svg\b[^>]*>.*?</svg>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_CSS_RULE = re.compile(r"[.#][\w-]+[^{}]*\{[^{}]*\}")


def clean_step_label(label, limit=48):
    """Page titles scraped from the live site include CSS and SVG rules. Keep the words."""
    text = _STYLE_BLOCK.sub(" ", str(label or ""))
    text = _SVG_BLOCK.sub(" ", text)
    text = _TAG.sub(" ", text)
    previous = None
    while previous != text:
        previous = text
        text = _CSS_RULE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    if text.isdigit() and len(text) >= 6:
        return "Ad creative"
    if limit and len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _step_label(raw):
    """A numeric ad id is not a step name. Keep the role people can read."""
    title = str((raw or {}).get("title") or "").strip()
    nxt = str((raw or {}).get("next") or "").strip()
    role = str((raw or {}).get("role") or "").strip()
    label = title or nxt or role or "Untitled step"
    if label.isdigit() and len(label) >= 6:
        return "Ad creative" if role in ("family", "ad", "") else role.replace("_", " ").title()
    return clean_step_label(label) or "Untitled step"


def query_ads(filters=None):
    filters = filters or {}
    try:
        limit = min(max(int(filters.get("limit") or 40), 1), 200)
        offset = max(int(filters.get("offset") or 0), 0)
    except (TypeError, ValueError):
        raise AdIntelError("limit and offset must be numbers") from None
    if not configured():
        pack = _envelope("fixture")
        wanted = (filters.get("page_id") or filters.get("pageId") or "").strip()
        matches = []
        for ad in FIXTURE_ADS:
            if wanted and (ad.get("page_id") or "") != wanted:
                continue
            if _match(ad, filters):
                matches.append(dict(ad))
        pack["ads"] = matches[offset:offset + limit]
        pack.update(matched=len(matches), limit=limit, offset=offset)
        return pack
    # advertiser is a substring on the tracker. pageId pins one account when the
    # name is a prefix of another (James Riston also matches James Riston EU 2).
    params = {
        "advertiser": filters.get("advertiser"),
        "pageId": filters.get("page_id") or filters.get("pageId"),
        "from": filters.get("date_from"),
        "to": filters.get("date_to"),
        "status": filters.get("status"),
        "min_days": filters.get("longevity_min") or filters.get("longevity_min_days"),
        "limit": limit,
        "offset": offset,
    }
    try:
        body = _get("/ads", params)
    except AdIntelError as e:
        if params.get("pageId") and "(400)" in str(e):
            params.pop("pageId", None)
            body = _get("/ads", params)
        else:
            raise
    pack = _meta(body)
    pack["ads"] = [a for a in (normalize_ad(r) for r in (body.get("observed") or {}).get("ads") or []) if a]
    pack["matched"] = body.get("matched", len(pack["ads"]))
    pack["offset"] = body.get("offset", 0)
    pack["limit"] = body.get("limit", 40)
    # The tracker ignores pageId, or we dropped it after a 400. Do not present
    # another page's cards as this account. A page that already matches keeps
    # the tracker's matched total.
    return _pin_requested_page(pack, filters.get("page_id") or filters.get("pageId"))


def ad_by_id(ad_id):
    if not ad_id:
        return None
    if not configured():
        return next((dict(a) for a in FIXTURE_ADS if a["id"] == ad_id), None)
    body = _get(f"/ads/{quote(str(ad_id), safe='')}")
    return normalize_ad(body.get("observed"))


def _pin_requested_page(pack, wanted):
    """Keep the list on the page the caller asked for.

    Ads with no page id are left alone. When every returned ad is that page,
    the tracker's matched total stays. A mixed page is narrowed. A page that
    is entirely someone else's is an empty list, not the other account's cards.
    """
    wanted = str(wanted or "").strip()
    ads = pack.get("ads") or []
    if not wanted or not ads:
        return pack
    pages = [str(ad.get("page_id") or "") for ad in ads]
    if not any(pages):
        return pack
    if all(page == wanted for page in pages):
        return pack
    if wanted not in pages:
        pack["ads"] = []
        pack["matched"] = 0
        return pack
    pinned = [ad for ad in ads if str(ad.get("page_id") or "") == wanted]
    pack["ads"] = pinned
    pack["matched"] = len(pinned)
    return pack


def _funnel_id_for_advertiser(advertiser):
    """Directory join used when the ad record itself has no funnel id.

    Exact advertiser name, case-insensitive, and the funnel must already have
    ads. The row with the most ads wins. An empty Ethan Young funnel stays unassigned.
    """
    name = str(advertiser or "").strip().lower()
    if not name:
        return None
    best_id, best_ads = None, 0
    for row in directory().get("funnels") or []:
        names = [str(item).strip().lower() for item in (row.get("advertisers") or [])]
        if name not in names:
            continue
        try:
            count = int(row.get("ads") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0 or count <= best_ads:
            continue
        best_ads = count
        best_id = row.get("id") or row.get("name")
    return best_id


def _collapse_steps(steps, limit=12):
    """A stored route can repeat the same card thousands of times. Keep the first of each."""
    seen, out = set(), []
    for step in steps:
        key = (step.get("kind"), step.get("label"))
        if key in seen:
            continue
        seen.add(key)
        out.append(step)
        if len(out) >= limit:
            break
    return out


def _renumber(steps):
    out = []
    for n, step in enumerate(steps, 1):
        row = dict(step)
        row["n"] = n
        out.append(row)
    return out


def _present_steps(steps, ad_id, limit=12):
    wanted = str(ad_id or "")
    if wanted:
        owned = [step for step in steps if step.get("ad_id") and str(step["ad_id"]) == wanted]
        if owned:
            return _renumber(owned)
    return _renumber(_collapse_steps(steps, limit))


def funnel_for(ad_id):
    ad = ad_by_id(ad_id) or {}
    if not configured():
        mapped = FIXTURE_FUNNELS.get(ad_id) or {"steps": [], "landing_page": {"url": ad.get("landing_url") or "", "title": ""}}
        return {"ad_id": ad_id, **mapped}
    funnel_id = (ad.get("assigned_funnel") or ad.get("funnel") or ad.get("funnel_id") or "").strip()
    if not funnel_id:
        funnel_id = _funnel_id_for_advertiser(ad.get("advertiser"))
    if not funnel_id:
        return {"ad_id": ad_id, "funnel_id": None, "steps": [],
                "landing_page": {"url": ad.get("landing_url") or "", "title": ""},
                "note": "No funnel is assigned to this ad in the tracker."}
    return route_for_funnel(funnel_id, ad_id=ad_id, landing_url=ad.get("landing_url") or "")


def route_for_funnel(funnel_id, ad_id=None, landing_url=""):
    """Read one named tracker funnel without first resolving an ad."""
    if not configured():
        raise AdIntelError("Funnel directory requires a configured tracker")
    try:
        body = _get(f"/funnels/{quote(str(funnel_id), safe='')}/route")
    except AdIntelNotFound:
        return {"ad_id": ad_id, "funnel_id": funnel_id, "steps": [],
                "landing_page": {"url": landing_url, "title": ""},
                "note": "The assigned funnel is not in the current tracker snapshot."}
    steps = []
    for state in ("observed", "inferred", "unknown"):
        section = body.get(state) or {}
        for raw in (section.get("storedSteps") or []) + (section.get("laneSteps") or []):
            steps.append({"n": len(steps) + 1, "kind": raw.get("role") or "step",
                          "label": _step_label(raw),
                          "url": raw.get("url") or "", "state": state,
                          "source": raw.get("source") or "", "checked_on": raw.get("checkedOn"),
                          "lane_id": raw.get("laneId"), "branch": raw.get("branch"),
                          "ad_id": str(raw.get("adId") or raw.get("ad_id") or "")})
    if not steps and landing_url:
        steps.append({"n": 1, "kind": "destination", "label": "Destination URL on ad record",
                      "url": landing_url, "state": "observed", "source": "Ads.Destination URL"})
    steps = _present_steps(steps, ad_id)
    return {"ad_id": ad_id, "funnel_id": (body.get("funnel") or {}).get("id"), "steps": steps,
            "funnel_name": (body.get("funnel") or {}).get("name"),
            "landing_page": {"url": landing_url, "title": ""},
            "data_as_of": (body.get("evidence") or {}).get("asOf")}


def accounts():
    """Advertisers in the current read, with how many ads each one has. Same origin rules as query_ads."""
    if configured():
        body = _get("/advertisers")
        pack = _meta(body)
        pack["accounts"] = [{"advertiser": a.get("name") or a.get("pageId") or "Unknown",
                             "page_id": a.get("pageId"), "ads": a.get("ads") or 0,
                             "funnel": a.get("funnel") or "", "as_of": a.get("asOf")}
                            for a in (body.get("observed") or {}).get("advertisers") or []]
        return pack
    pack = query_ads({})
    counts, order = {}, []
    for ad in pack["ads"]:
        name = ad.get("advertiser") or "Unknown"
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
    pack["accounts"] = [{"advertiser": name, "ads": counts[name]} for name in order]
    return pack


def directory():
    """Stored funnel inventory. Older tracker deployments get a labelled partial list."""
    if configured():
        try:
            body = _get("/funnels")
        except AdIntelNotFound:
            partial = accounts()
            names = {}
            for account in partial["accounts"]:
                name = account.get("funnel")
                if name:
                    names.setdefault(name, []).append(account["advertiser"])
            partial["funnels"] = [{"id": name, "name": name, "advertisers": advertisers, "ads": None}
                                  for name, advertisers in sorted(names.items())]
            partial["coverage"] = "advertiser records only"
            return partial
        pack = _meta(body)
        pack["funnels"] = [dict(row) for row in (body.get("observed") or {}).get("funnels") or []]
        pack["coverage"] = "stored funnel directory"
        return pack
    pack = _envelope("fixture")
    pack["funnels"] = []
    pack["coverage"] = "fixture has no named funnel directory"
    return pack


def market():
    if not configured():
        pack = _envelope("fixture")
        pack["points"] = [dict(p) for p in FIXTURE_POINTS]
        pack["events"] = [dict(e) for e in FIXTURE_EVENTS]
        return pack
    # PR #98 does not expose historical reach or live-ad series. Do not invent
    # points from a current snapshot or show the fixture as live history.
    pack = _envelope("live", None)
    pack["points"] = []
    pack["events"] = []
    pack["note"] = "The live tracker API does not expose a market timeline yet."
    return pack
