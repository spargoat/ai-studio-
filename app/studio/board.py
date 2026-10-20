"""Board graph: typed ports, compile-to-stages, talking_head preset.

A board is one graph. Intel and Create are lenses, not two graphs. Compile
turns enabled nodes into the runner's stage list and wires into `needs`.
Disabled nodes pass a value through when an input type matches an output type.
"""
import copy

from .adintel import (FIXTURE_LABEL, LONGEVITY_NOTE, REFERENCE_LABEL, AdIntelError,
                      ad_by_id, funnel_for, market, query_ads)
from .config import load_template

# Port id, port type. Types match the board spec.
NODES = {
    "ad_feed": {"tab": "intel", "kind": "intel", "task": "feed", "inputs": [], "outputs": [("ad", "ad")]},
    "ad_breakdown": {"tab": "intel", "kind": "intel", "task": "breakdown",
                     "inputs": [("ad", "ad")], "outputs": [("ad", "ad")]},
    "funnel_route": {"tab": "intel", "kind": "intel", "task": "funnel",
                     "inputs": [("ad", "ad")], "outputs": [("page", "page")]},
    "teardown": {"tab": "intel", "kind": "intel", "task": "teardown", "needs_key": "llm",
                 "inputs": [("ad", "ad"), ("page", "page")], "outputs": [("brief", "brief")]},
    "market_timeline": {"tab": "intel", "kind": "intel", "task": "timeline",
                        "inputs": [], "outputs": [("ad", "ad")]},
    "brief": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "brief",
              "inputs": [("brief", "brief")], "outputs": [("brief", "brief")]},
    "script": {"tab": "create", "kind": "stage", "template": "talking_head_scripted", "stage_id": "script",
               "needs_key": "llm", "inputs": [("brief", "brief")], "outputs": [("script", "script"), ("hooks", "hooks")]},
    "voice": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "voice",
              "needs_key": "elevenlabs", "inputs": [("script", "script")], "outputs": [("audio", "audio")]},
    "video": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "video",
              "needs_key": "fal", "inputs": [("audio", "audio"), ("still", "still")], "outputs": [("video", "video")]},
}

# Mechanism only. No wardrobe, palette, or scene. The decode stays under these.
PATTERNS = {
    "loss-then-rule": {
        "pattern_summary": "Open on a loss the viewer already recognises, then give one rule that would have stopped it. The mechanism is problem, cost, single constraint.",
        "interpretation": "Keep the rule-before-action mechanism and write it in our persona's cadence. Do not borrow their sentences, people, or setting.",
        "decode": "The source names an unplanned size, then a stop that should have come first.",
    },
    "testimonial-bridge": {
        "pattern_summary": "A third party states a before and after, then the ad hands the viewer one next step. The mechanism is social proof into a single action.",
        "interpretation": "Use a proof-then-step shape with our own persona. The source's story stays in the decode.",
        "decode": "The source cuts from a customer's result to a landing page with one call to action.",
    },
    "curiosity-list": {
        "pattern_summary": "Withhold the list, number the items, and make the click the only way to see them. The mechanism is an open loop.",
        "interpretation": "An open loop can frame our own checklist. The source's items are reference only.",
        "decode": "The source promises a numbered list and resolves it on the landing page.",
    },
}
DEFAULT_PATTERN = {
    "pattern_summary": "The source moves from a felt problem to one concrete next step. The mechanism is that turn, nothing visual.",
    "interpretation": "Rebuild the turn in our persona's voice. Leave the source's wording in the decode.",
    "decode": "The source states a problem and points at a landing page.",
}


class BoardError(ValueError):
    pass


def _subst(value, variables):
    if not isinstance(value, str):
        return value
    out = value
    for key, val in (variables or {}).items():
        out = out.replace("{" + key + "}", "" if val is None else str(val))
    return out


def _ports(node_type, direction):
    spec = NODES.get(node_type)
    if not spec:
        raise BoardError(f"unknown node {node_type}")
    return spec["outputs"] if direction == "out" else spec["inputs"]


def port_type(node_type, direction, handle):
    for pid, typ in _ports(node_type, direction):
        if pid == handle:
            return typ
    raise BoardError(f"{node_type} has no {direction} port {handle}")


def _apply_disabled(nodes, edges):
    """Drop a disabled node when one input type matches one output type, and bridge the wires."""
    by_id = {n["id"]: n for n in nodes}
    for node in list(nodes):
        if node.get("enabled", True):
            continue
        spec = NODES[node["type"]]
        match = None
        for inn in spec["inputs"]:
            for out in spec["outputs"]:
                if inn[1] == out[1]:
                    match = (inn[0], out[0])
        if not match:
            continue
        inn, out = match
        incoming = [e for e in edges if e["target"] == node["id"] and e.get("targetHandle") == inn]
        outgoing = [e for e in edges if e["source"] == node["id"] and e.get("sourceHandle") == out]
        edges = [e for e in edges if e["source"] != node["id"] and e["target"] != node["id"]]
        for a in incoming:
            for b in outgoing:
                edges.append({"id": f"pass-{a['id']}-{b['id']}", "source": a["source"], "sourceHandle": a.get("sourceHandle"),
                              "target": b["target"], "targetHandle": b.get("targetHandle")})
        nodes = [n for n in nodes if n["id"] != node["id"]]
        by_id.pop(node["id"], None)
    return nodes, edges


def _topo(nodes, edges):
    ids = [n["id"] for n in nodes]
    indeg = {i: 0 for i in ids}
    nxt = {i: [] for i in ids}
    known = set(ids)
    for e in edges:
        if e["source"] not in known or e["target"] not in known:
            raise BoardError("a wire points at a missing node")
        nxt[e["source"]].append(e["target"])
        indeg[e["target"]] += 1
    queue = [i for i in ids if indeg[i] == 0]
    order = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for t in nxt[cur]:
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    if len(order) != len(ids):
        raise BoardError("board has a cycle")
    return order


def _stage_from_template(template_name, stage_id):
    for stage in load_template(template_name)["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise BoardError(f"{template_name} has no stage {stage_id}")


def compile_board(graph):
    """Nodes become stages, wires become needs. Raises BoardError on a bad wire."""
    nodes = copy.deepcopy(graph.get("nodes") or [])
    edges = copy.deepcopy(graph.get("edges") or [])
    variables = graph.get("variables") or {}
    if not nodes:
        raise BoardError("board has no nodes")
    for node in nodes:
        if node.get("type") not in NODES:
            raise BoardError(f"unknown node {node.get('type')}")
        node["config"] = {k: _subst(v, variables) for k, v in (node.get("config") or {}).items()}
    by_id = {n["id"]: n for n in nodes}
    for edge in edges:
        src, dst = by_id.get(edge.get("source")), by_id.get(edge.get("target"))
        if not src or not dst:
            raise BoardError("a wire points at a missing node")
        st = port_type(src["type"], "out", edge.get("sourceHandle"))
        dt = port_type(dst["type"], "in", edge.get("targetHandle"))
        if st != dt:
            raise BoardError(f"cannot connect {st} to {dt}")
    nodes, edges = _apply_disabled(nodes, edges)
    by_id = {n["id"]: n for n in nodes}
    stages = []
    for nid in _topo(nodes, edges):
        node = by_id[nid]
        spec = NODES[node["type"]]
        incoming = [e for e in edges if e["target"] == nid]
        needs, port_of = [], {}
        for edge in incoming:
            needs.append(edge["source"])
            port_of[edge["source"]] = edge.get("targetHandle")
        if spec["kind"] == "intel":
            stage = {"id": nid, "kind": "intel", "task": spec["task"], "needs": needs, "port_of": port_of,
                     "params": node.get("config") or {}, "review": False, "board_node": node["type"],
                     "disabled": not node.get("enabled", True)}
        else:
            stage = copy.deepcopy(_stage_from_template(spec["template"], spec["stage_id"]))
            stage.pop("needs", None)
            stage.update({"id": nid, "needs": needs, "port_of": port_of, "board_node": node["type"],
                          "disabled": not node.get("enabled", True)})
            stage.setdefault("uses", spec["stage_id"])
            if node["type"] == "video":
                for edge in incoming:
                    if edge.get("targetHandle") == "still":
                        stage.setdefault("input_alias", {})["still"] = edge["source"]
            params = stage.get("params") or {}
            if node.get("config", {}).get("framework"):
                params["framework"] = node["config"]["framework"]
            stage["params"] = params
        stages.append(stage)
    return stages


def job_brief(graph, stages, job_id=None):
    variables = graph.get("variables") or {}
    return {
        "id": job_id,
        "persona": variables.get("persona") or "marco",
        "template": "talking_head",
        "script": variables.get("script") or "",
        "setting": variables.get("setting") or "a bright, lived-in room",
        "topic": variables.get("topic") or variables.get("offer") or "the offer",
        "headline": variables.get("headline") or "",
        "cta": variables.get("cta") or "",
        "board_id": graph.get("id"),
        "board_stages": stages,
        "variables": variables,
    }


def _evidence(pack, fields):
    return {"source": pack["source"], "data_as_of": pack["data_as_of"], "origin": pack["origin"],
            "live": pack["live"], "label": pack["label"], "fields": fields}


def _pick_ad(pack, ad_id):
    ads = pack.get("ads") or []
    if ad_id:
        chosen = next((a for a in ads if a["id"] == ad_id), None)
        if chosen:
            return chosen
        # The query filter may hide the selected id; still resolve that record.
        return ad_by_id(ad_id)
    return ads[0] if ads else None


def _observed_ad(ad):
    if not ad:
        return {}
    return {k: ad.get(k) for k in ("id", "advertiser", "status", "first_seen", "last_seen", "longevity_days",
                                   "primary_text", "headline", "cta", "media", "landing_url", "creative_family", "platforms")}


def feed_artefact(config):
    pack = query_ads(config or {})
    chosen = _pick_ad(pack, (config or {}).get("ad_id"))
    return {
        "type": "ad",
        "ad": chosen,
        "evidence": _evidence(pack, {"ads": [_observed_ad(a) for a in pack["ads"]], "picked": _observed_ad(chosen)}),
        "inference": {"note": "Picking one ad out of the query is our choice, not a tracker field.",
                      "match_count": len(pack["ads"]), "picked_id": (chosen or {}).get("id")},
    }


def _ad_id_from(blob):
    if not isinstance(blob, dict):
        return None
    if isinstance(blob.get("ad"), dict) and blob["ad"].get("id"):
        return blob["ad"]["id"]
    fields = (blob.get("evidence") or {}).get("fields") or {}
    if isinstance(fields.get("picked"), dict):
        return fields["picked"].get("id")
    return fields.get("id")


def _blob_for(inputs, sdef, role):
    port_of = sdef.get("port_of") or {}
    for nid, port in port_of.items():
        if port == role and isinstance(inputs.get(nid), dict):
            return inputs[nid]
    for blob in inputs.values():
        if isinstance(blob, dict) and blob.get("type") == role:
            return blob
    return None


def breakdown_artefact(ad_blob):
    ad_id = _ad_id_from(ad_blob)
    ad = ad_by_id(ad_id) if ad_id else None
    # A fresh read so this node's source and data-as-of are its own, not copied off the wire.
    probe = query_ads({"advertiser": (ad or {}).get("advertiser")}) if ad else query_ads({})
    fields = _observed_ad(ad)
    return {
        "type": "ad",
        "ad": ad,
        "evidence": _evidence(probe, fields),
        "inference": {"note": "Family is the tracker's own grouping, copied into evidence. What we make of the run length is not.",
                      "longevity_note": LONGEVITY_NOTE},
    }


def funnel_artefact(ad_blob):
    ad_id = _ad_id_from(ad_blob)
    route = funnel_for(ad_id) if ad_id else {"ad_id": None, "steps": [], "landing_page": {}}
    probe = query_ads({})
    return {
        "type": "page",
        "page": route.get("landing_page") or {},
        "evidence": _evidence(probe, {"ad_id": ad_id, "steps": route.get("steps") or [], "landing_page": route.get("landing_page") or {}}),
        "inference": {"note": "The route steps and landing page are the tracker's map. Reading a step as persuasive is our inference, and this node does not add one."},
    }


def teardown_artefact(ad_blob, page_blob):
    ad_id = _ad_id_from(ad_blob)
    ad = ad_by_id(ad_id) if ad_id else (ad_blob or {}).get("ad")
    family = (ad or {}).get("creative_family") or ""
    pattern = PATTERNS.get(family) or DEFAULT_PATTERN
    probe = query_ads({})
    page = None
    if isinstance(page_blob, dict):
        page = (page_blob.get("evidence") or {}).get("fields") or page_blob.get("page")
    return {
        "type": "brief",
        "pattern_summary": pattern["pattern_summary"],
        "interpretation": pattern["interpretation"],
        "decode": pattern["decode"],
        "label": REFERENCE_LABEL,
        "ad": _observed_ad(ad) if ad else None,
        "evidence": _evidence(probe, {"ad": _observed_ad(ad) if ad else None, "funnel": page}),
        "inference": {"note": "Pattern summary and interpretation are ours. The decode is the source, kept below them as reference.",
                      "pattern_summary": pattern["pattern_summary"], "interpretation": pattern["interpretation"]},
    }


def timeline_artefact(config):
    pack = market()
    config = config or {}
    event = next((e for e in pack["events"] if e["id"] == config.get("event_id")), None)
    ad_id = (event or {}).get("ad_id") or config.get("ad_id") or ""
    if not event and pack["events"]:
        event = pack["events"][0]
        ad_id = ad_id or event.get("ad_id")
    ad = ad_by_id(ad_id) if ad_id else None
    return {
        "type": "ad",
        "ad": ad,
        "emitted_ad_id": (ad or {}).get("id"),
        "evidence": _evidence(pack, {"points": pack["points"], "events": pack["events"], "emitted_event": event}),
        "inference": {"note": "The September reach step-up sits beside two ads in market. That alignment is our reading, not a tracker field. Reach and the live count are the recorded series."},
    }


def intel_artefact(sdef, inputs):
    task = sdef.get("task")
    try:
        if task == "feed":
            return feed_artefact(sdef.get("params") or {})
        if task == "breakdown":
            return breakdown_artefact(_blob_for(inputs, sdef, "ad"))
        if task == "funnel":
            return funnel_artefact(_blob_for(inputs, sdef, "ad"))
        if task == "teardown":
            return teardown_artefact(_blob_for(inputs, sdef, "ad"), _blob_for(inputs, sdef, "page"))
        if task == "timeline":
            return timeline_artefact(sdef.get("params") or {})
    except AdIntelError:
        raise
    raise BoardError(f"unknown intel task {task}")


def artefact_summary(artefact):
    evidence = artefact.get("evidence") or {}
    inference = artefact.get("inference") or {}
    ad = artefact.get("ad") or {}
    return {
        "origin": evidence.get("origin"),
        "live": evidence.get("live"),
        "source": evidence.get("source"),
        "data_as_of": evidence.get("data_as_of"),
        "label": evidence.get("label"),
        "inference": inference.get("note") or "",
        "longevity_note": inference.get("longevity_note"),
        "reference_label": artefact.get("label"),
        "headline": ad.get("headline"),
        "pattern_summary": artefact.get("pattern_summary"),
        "interpretation": artefact.get("interpretation"),
    }


def preview_pack(config=None):
    """Same builders the stages use, so the canvas can show a read before a run."""
    config = config or {}
    feed = feed_artefact(config)
    breakdown = breakdown_artefact(feed)
    funnel = funnel_artefact(feed)
    teardown = teardown_artefact(breakdown, funnel)
    timeline = timeline_artefact(config)
    origin = (feed.get("evidence") or {}).get("origin")
    return {"origin": origin, "live": origin == "live",
            "label": (feed.get("evidence") or {}).get("label") or FIXTURE_LABEL,
            "feed": feed, "breakdown": breakdown, "funnel": funnel, "teardown": teardown, "timeline": timeline}


def talking_head_preset():
    """Slice-1 board. Create stages take their prompts, params and review gates from the YAML templates."""
    return {
        "variables": {
            "persona": "marco",
            "offer": "a stop-first checklist",
            "cta": "Learn the rule",
            "setting": "a quiet London flat, window light, plain wall behind",
            "topic": "naming the invalidation before sizing",
            "script": "Decide the stop before you size the trade. If you can't say where you're wrong, leave it.",
        },
        "nodes": [
            {"id": "timeline", "type": "market_timeline", "position": {"x": 0, "y": 0}, "enabled": True,
             "config": {"event_id": "ev_launch_northline", "ad_id": "rec_4c91e2"}},
            {"id": "ad_feed", "type": "ad_feed", "position": {"x": 380, "y": 0}, "enabled": True,
             "config": {"advertiser": "Northline Markets", "status": "live", "date_from": "2026-07-01",
                        "date_to": "2026-10-18", "longevity_min": "30", "ad_id": "rec_4c91e2"}},
            {"id": "ad_breakdown", "type": "ad_breakdown", "position": {"x": 380, "y": 280}, "enabled": True, "config": {}},
            {"id": "funnel", "type": "funnel_route", "position": {"x": 680, "y": 280}, "enabled": True, "config": {}},
            {"id": "teardown", "type": "teardown", "position": {"x": 380, "y": 560}, "enabled": True, "config": {}},
            {"id": "brief", "type": "brief", "position": {"x": 0, "y": 24}, "enabled": True, "config": {}},
            {"id": "script", "type": "script", "position": {"x": 290, "y": 24}, "enabled": True, "config": {"framework": "PAS"}},
            {"id": "voice", "type": "voice", "position": {"x": 580, "y": 24}, "enabled": True, "config": {}},
            {"id": "video", "type": "video", "position": {"x": 870, "y": 24}, "enabled": True, "config": {}},
        ],
        "edges": [
            {"id": "e-feed-breakdown", "source": "ad_feed", "sourceHandle": "ad", "target": "ad_breakdown", "targetHandle": "ad"},
            {"id": "e-feed-funnel", "source": "ad_feed", "sourceHandle": "ad", "target": "funnel", "targetHandle": "ad"},
            {"id": "e-breakdown-teardown", "source": "ad_breakdown", "sourceHandle": "ad", "target": "teardown", "targetHandle": "ad"},
            {"id": "e-funnel-teardown", "source": "funnel", "sourceHandle": "page", "target": "teardown", "targetHandle": "page"},
            {"id": "e-teardown-brief", "source": "teardown", "sourceHandle": "brief", "target": "brief", "targetHandle": "brief"},
            {"id": "e-brief-script", "source": "brief", "sourceHandle": "brief", "target": "script", "targetHandle": "brief"},
            {"id": "e-script-voice", "source": "script", "sourceHandle": "script", "target": "voice", "targetHandle": "script"},
            {"id": "e-voice-video", "source": "voice", "sourceHandle": "audio", "target": "video", "targetHandle": "audio"},
        ],
    }


def ensure_presets(db):
    if not db.board("talking_head"):
        db.save_board("talking_head", "Talking head", talking_head_preset(), preset=True)
