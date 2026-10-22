"""Board graph: typed ports, compile-to-stages, talking_head preset.

A board is one graph. Intel and Create are lenses, not two graphs. Compile
turns enabled nodes into the runner's stage list and wires into `needs`.
Disabled nodes pass a value through when an input type matches an output type.
"""
import copy

from .adintel import (FIXTURE_LABEL, LONGEVITY_NOTE, REFERENCE_LABEL, AdIntelError, AdIntelNotFound,
                      ad_by_id, clean_step_label, funnel_for, market, query_ads)
from .config import load_template

# Port id, port type. Types match the board spec.
NODES = {
    "ad_feed": {"tab": "intel", "kind": "intel", "task": "feed", "inputs": [], "outputs": [("ad", "ad")]},
    "ad_breakdown": {"tab": "intel", "kind": "intel", "task": "breakdown",
                     "inputs": [("ad", "ad")], "outputs": [("ad", "ad")]},
    "funnel_route": {"tab": "intel", "kind": "intel", "task": "funnel",
                     "inputs": [("ad", "ad")], "outputs": [("page", "page")]},
    "teardown": {"tab": "intel", "kind": "intel", "task": "teardown",
                 "inputs": [("ad", "ad"), ("page", "page")], "outputs": [("brief", "brief")]},
    "market_timeline": {"tab": "intel", "kind": "intel", "task": "timeline",
                        "inputs": [], "outputs": [("ad", "ad")]},
    "funnel_step": {"tab": "intel", "kind": "intel", "task": "step",
                    "inputs": [("step", "step")], "outputs": [("step", "step"), ("page", "page")]},
    "brief": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "brief",
              "inputs": [("brief", "brief"), ("source", "step")], "outputs": [("brief", "brief")]},
    "landing": {"tab": "create", "kind": "intel", "task": "landing",
                "inputs": [("brief", "brief"), ("source", "step")], "outputs": [("page", "page")]},
    "script": {"tab": "create", "kind": "stage", "template": "talking_head_scripted", "stage_id": "script",
               "needs_key": "llm", "inputs": [("brief", "brief"), ("source", "step")],
               "outputs": [("script", "script"), ("hooks", "hooks")]},
    "voice": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "voice",
              "needs_key": "elevenlabs", "inputs": [("script", "script")], "outputs": [("audio", "audio")]},
    "still": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "still",
              "needs_key": "fal", "inputs": [("brief", "brief")], "outputs": [("still", "still")]},
    "video": {"tab": "create", "kind": "stage", "template": "talking_head", "stage_id": "video",
              "needs_key": "fal", "inputs": [("audio", "audio"), ("still", "still")], "outputs": [("video", "video")]},
    # Planning objects persist on the graph but never become paid or dry-run stages.
    "note": {"tab": "create", "kind": "canvas", "inputs": [], "outputs": []},
    "quick_link": {"tab": "create", "kind": "canvas", "inputs": [], "outputs": []},
    "funnel_stage": {"tab": "create", "kind": "canvas", "inputs": [("flow", "flow")], "outputs": [("flow", "flow")]},
    "ad_reference": {"tab": "intel", "kind": "canvas", "inputs": [], "outputs": []},
    "kit_master": {"tab": "create", "kind": "canvas", "inputs": [], "outputs": []},
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
        if spec["kind"] == "canvas":
            continue
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
            cfg = node.get("config") or {}
            if node["type"] == "still":
                if cfg.get("prompt"):
                    stage["prompt"] = cfg["prompt"]
                for key in ("aspect_ratio", "resolution"):
                    if cfg.get(key):
                        params[key] = cfg[key]
            if cfg.get("framework"):
                params["framework"] = cfg["framework"]
            if node["type"] == "video":
                if cfg.get("aspect"):
                    params["aspect"] = cfg["aspect"]
                if cfg.get("format"):
                    params["format"] = cfg["format"]
            stage["params"] = params
        stages.append(stage)
    if not stages:
        raise BoardError("Add an executable block before running this board")
    return stages


def _enabled(graph, node_type):
    return next((n for n in (graph.get("nodes") or []) if n.get("type") == node_type and n.get("enabled", True)), None)


def scaffold_script(graph, stages):
    """A replicate scaffold adds a script block with no copy. Dry run still needs words.

    The talking-head template has a voice stage and no script stage, so an empty
    brief.script is rejected before the script block can write one.
    """
    variables = graph.get("variables") or {}
    script_cfg = (_enabled(graph, "script") or {}).get("config") or {}
    script = (script_cfg.get("script") or "").strip() or (variables.get("script") or "").strip()
    if script:
        return script
    if not any(s.get("kind") == "script" or s.get("board_node") == "script" for s in stages):
        return ""
    labels = []
    for node in graph.get("nodes") or []:
        if node.get("type") != "funnel_step":
            continue
        label = clean_step_label((node.get("config") or {}).get("label") or "", limit=80)
        if label:
            labels.append(label)
    route = ". ".join(labels[:6])
    if route:
        return f"Say this in our voice. The route is: {route}."
    return "Name the offer, then the one next step."


def job_brief(graph, stages, job_id=None):
    """Create-block fields win over the board variables when the block has a value."""
    variables = graph.get("variables") or {}
    brief_cfg = (_enabled(graph, "brief") or {}).get("config") or {}
    voice_cfg = (_enabled(graph, "voice") or {}).get("config") or {}
    video_cfg = (_enabled(graph, "video") or {}).get("config") or {}
    script = scaffold_script(graph, stages)
    offer = brief_cfg.get("offer") or variables.get("offer") or ""
    angle = brief_cfg.get("angle") or ""
    return {
        "id": job_id,
        "persona": brief_cfg.get("persona") or variables.get("persona") or "marco",
        "template": "talking_head",
        "script": script,
        "setting": variables.get("setting") or "a bright, lived-in room",
        "topic": angle or variables.get("topic") or offer or "the offer",
        "headline": variables.get("headline") or "",
        "cta": variables.get("cta") or "",
        "offer": offer,
        "angle": angle,
        "length": brief_cfg.get("length") or "30",
        "platform": brief_cfg.get("platform") or "instagram",
        "voice_name": voice_cfg.get("voice") or "",
        "aspect": video_cfg.get("aspect") or "9:16",
        "format": video_cfg.get("format") or "talking_head",
        "board_id": graph.get("id"),
        "board_stages": stages,
        "variables": variables,
    }


def resolve_voice_id(personas, persona, voice_name):
    """A voiceover block's voice name picks that voice. Otherwise the persona's own voice."""
    wanted = (voice_name or "").strip().lower()
    if wanted:
        for row in (personas or {}).values():
            if (row.get("voice_name") or "").strip().lower() == wanted and row.get("voice_id"):
                return row["voice_id"]
    return ((personas or {}).get(persona) or {}).get("voice_id")


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
        try:
            return ad_by_id(ad_id) or (ads[0] if ads else None)
        except AdIntelNotFound:
            return ads[0] if ads else None
    return ads[0] if ads else None


def _observed_ad(ad):
    if not ad:
        return {}
    return {k: ad.get(k) for k in ("id", "advertiser", "status", "status_label", "first_seen", "last_seen", "longevity_days",
                                   "primary_text", "headline", "cta", "media", "format", "landing_url", "library_url",
                                   "creative_family", "platforms", "page_id", "funnel", "assigned_funnel", "as_of")}


def _panels(ad):
    """Six cards. Evidence is what the record states. Inference is our reading, kept in its own list."""
    ad = ad or {}
    media = ad.get("media") if isinstance(ad.get("media"), list) and ad.get("media") else [{}]
    kind = ad.get("format") or (media[0] or {}).get("type") or "unknown"
    family = ad.get("creative_family") or ""
    pattern = PATTERNS.get(family) or DEFAULT_PATTERN
    days = ad.get("longevity_days")
    platforms = ", ".join(ad.get("platforms") or []) or "platform not recorded"
    evidence = [
        {"id": "hook", "text": ad.get("headline") or "Not stated in the record."},
        {"id": "angle", "text": family or "No creative family on the record."},
        {"id": "offer", "text": "No offer, price, or promise is in the public record."},
        {"id": "proof", "text": f"{days} days on the record. {LONGEVITY_NOTE}" if days is not None else "No run length on the record."},
        {"id": "cta", "text": ad.get("cta") or "No call to action on the record."},
        {"id": "format", "text": f"{kind} · {platforms}"},
    ]
    inference = [
        {"id": "hook", "text": pattern["decode"]},
        {"id": "angle", "text": pattern["pattern_summary"]},
        {"id": "offer", "text": "Do not invent an offer from the mechanism."},
        {"id": "proof", "text": "Run length is not proof the ad worked."},
        {"id": "cta", "text": "The button text is the only observed next step."},
        {"id": "format", "text": "Format is the media type on the record, not a production spec."},
    ]
    return evidence, inference


def _waterfall(ads):
    # Route reads are requested for the ad the user opens. A 40-ad scan must
    # remain one paginated list call rather than 40 detail and route calls.
    return [_observed_ad(ad) for ad in (ads or [])]


def feed_artefact(config):
    pack = query_ads(config or {})
    chosen = _pick_ad(pack, (config or {}).get("ad_id"))
    return {
        "type": "ad",
        "ad": chosen,
        "evidence": _evidence(pack, {"ads": [_observed_ad(a) for a in pack["ads"]], "picked": _observed_ad(chosen),
                                     "waterfall": _waterfall(pack["ads"]), "matched": pack.get("matched"),
                                     "limit": pack.get("limit"), "offset": pack.get("offset")}),
        "inference": {"note": "Picking one ad out of the query is our choice, not a tracker field.",
                      "match_count": pack.get("matched", len(pack["ads"])), "picked_id": (chosen or {}).get("id"),
                      "requested_id": (config or {}).get("ad_id")},
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
    evidence_panels, inference_panels = _panels(ad)
    fields["panels"] = evidence_panels
    return {
        "type": "ad",
        "ad": ad,
        "evidence": _evidence(probe, fields),
        "inference": {"note": "Family is the tracker's own grouping, copied into evidence. What we make of the run length is not.",
                      "longevity_note": LONGEVITY_NOTE, "panels": inference_panels},
    }


def funnel_artefact(ad_blob):
    ad_id = _ad_id_from(ad_blob)
    route = funnel_for(ad_id) if ad_id else {"ad_id": None, "steps": [], "landing_page": {}}
    probe = query_ads({})
    return {
        "type": "page",
        "page": route.get("landing_page") or {},
        "evidence": _evidence(probe, {"ad_id": ad_id, "funnel_id": route.get("funnel_id"),
                                    "steps": route.get("steps") or [], "landing_page": route.get("landing_page") or {},
                                    "note": route.get("note")}),
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
    evidence_panels, inference_panels = _panels(ad)
    return {
        "type": "brief",
        "pattern_summary": pattern["pattern_summary"],
        "interpretation": pattern["interpretation"],
        "decode": pattern["decode"],
        "label": REFERENCE_LABEL,
        "ad": _observed_ad(ad) if ad else None,
        "evidence": _evidence(probe, {"ad": _observed_ad(ad) if ad else None, "funnel": page, "panels": evidence_panels}),
        "inference": {"note": "Pattern summary and interpretation are ours. The decode is the source, kept below them as reference.",
                      "pattern_summary": pattern["pattern_summary"], "interpretation": pattern["interpretation"],
                      "panels": inference_panels},
    }


def timeline_artefact(config):
    pack = market()
    config = config or {}
    event = next((e for e in pack["events"] if e["id"] == config.get("event_id")), None)
    ad_id = ((event or {}).get("ad_id") or config.get("ad_id") or "") if pack["points"] else ""
    if not event and pack["events"]:
        event = pack["events"][0]
        ad_id = ad_id or event.get("ad_id")
    ad = ad_by_id(ad_id) if ad_id else None
    return {
        "type": "ad",
        "ad": ad,
        "emitted_ad_id": (ad or {}).get("id"),
        "evidence": _evidence(pack, {"points": pack["points"], "events": pack["events"], "emitted_event": event}),
        "inference": {"note": ("The September reach step-up sits beside two ads in market. That alignment is our reading, not a tracker field. Reach and the live count are the recorded series."
                               if pack["points"] else pack.get("note") or "No timeline is recorded in this read.")},
    }


def step_artefact(config):
    config = config or {}
    label = clean_step_label(config.get("label") or "")
    fields = {k: config.get(k) for k in ("n", "kind", "label", "url", "ad_id", "state", "source")}
    fields["label"] = label
    return {
        "type": "step",
        "page": {"url": config.get("url") or "", "title": label},
        "evidence": {"source": "board", "origin": "copy", "live": False,
                     "label": "Copied from the funnel read onto the canvas",
                     "data_as_of": config.get("data_as_of") or "",
                     "fields": fields},
        "inference": {"note": "Placing the step on the canvas does not add a reading."},
    }


def landing_artefact(config, inputs):
    brief = {}
    for blob in (inputs or {}).values():
        if isinstance(blob, dict) and isinstance(blob.get("brief"), dict):
            brief = blob["brief"]
            break
    offer = (config or {}).get("offer") or brief.get("offer") or ""
    angle = (config or {}).get("angle") or brief.get("angle") or ""
    return {
        "type": "page",
        "page": {"title": "Landing outline", "url": ""},
        "evidence": {"source": "board", "origin": "local", "live": False,
                     "label": "Our outline, not a tracker page", "data_as_of": "",
                     "fields": {"offer": offer, "angle": angle, "sections": ["hook", "mechanism", "next step"]}},
        "inference": {"note": "The section list is our scaffold. It is not the source landing page."},
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
        if task == "step":
            return step_artefact(sdef.get("params") or {})
        if task == "landing":
            return landing_artefact(sdef.get("params") or {}, inputs)
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


def preview_config(graph):
    """Feed filters plus the timeline marker, which is what the canvas asks the read API for."""
    nodes = (graph or {}).get("nodes") or []
    feed = next((n for n in nodes if n.get("type") == "ad_feed"), None)
    timeline = next((n for n in nodes if n.get("type") == "market_timeline"), None)
    config = dict((feed or {}).get("config") or {})
    event_id = ((timeline or {}).get("config") or {}).get("event_id") or ""
    if event_id:
        config["event_id"] = event_id
    return config


def preview_pack(config=None):
    """Same builders the stages use, so the canvas can show a read before a run.

    A selected timeline marker keeps the series and passes that event's ad to
    Breakdown, Funnel and Teardown. The feed keeps its own query.
    """
    config = dict(config or {})
    feed_config = {k: v for k, v in config.items() if k != "event_id"}
    timeline = timeline_artefact(config)
    emitted = timeline.get("ad") if config.get("event_id") else None
    source = {"type": "ad", "ad": emitted} if emitted else None
    feed = feed_artefact(feed_config)
    breakdown = breakdown_artefact(source or feed)
    funnel = funnel_artefact(source or feed)
    teardown = teardown_artefact(breakdown, funnel)
    origin = (feed.get("evidence") or {}).get("origin")
    return {"origin": origin, "live": origin == "live",
            "label": (feed.get("evidence") or {}).get("label") or FIXTURE_LABEL,
            "feed": feed, "breakdown": breakdown, "funnel": funnel, "teardown": teardown, "timeline": timeline}


# Slice-1 stacked the two lenses on top of each other because only one showed at a time.
# v3 shows Intel and Create together, so the preset sits in two columns.
_SLICE1_POSITIONS = {
    "timeline": (0, 0), "ad_feed": (380, 0), "ad_breakdown": (380, 280), "funnel": (680, 280),
    "teardown": (380, 560), "brief": (0, 24), "script": (290, 24), "voice": (580, 24), "video": (870, 24),
}
# First v3 pass stacked the cards on top of each other. This draft is migrated forward too.
_V3_DRAFT_POSITIONS = {
    "timeline": (36, 28), "ad_feed": (36, 330), "funnel": (36, 560),
    "ad_breakdown": (36, 800), "teardown": (300, 800),
    "brief": (760, 40), "script": (760, 300), "voice": (1020, 40), "video": (1020, 320),
}
# The first v3 column fit short cards. Deeper blocks need the rows further apart.
_V3_TIGHT = {
    "timeline": (24, 20), "ad_feed": (24, 420), "funnel": (24, 780),
    "ad_breakdown": (24, 1050), "teardown": (292, 1050),
    "brief": (620, 20), "script": (620, 220),
    "voice": (880, 20), "video": (880, 230),
}
_V3_POSITIONS = {
    "timeline": {"x": 24, "y": 20}, "ad_feed": {"x": 24, "y": 460}, "funnel": {"x": 24, "y": 1020},
    "ad_breakdown": {"x": 24, "y": 1400}, "teardown": {"x": 340, "y": 1400},
    "brief": {"x": 720, "y": 20}, "script": {"x": 720, "y": 460},
    "voice": {"x": 1020, "y": 20}, "video": {"x": 1020, "y": 460},
}


def migrate_talking_head_layout(graph):
    """Shift an untouched slice-1 preset into the v3 columns.

    Returns a new graph, or None when any preset node was already moved or is missing.
    Edges, config and any extra nodes stay as they are.
    """
    nodes = (graph or {}).get("nodes") or []
    by_id = {n.get("id"): n for n in nodes}

    def matches(layout):
        if not set(layout) <= set(by_id):
            return False
        for nid, (x, y) in layout.items():
            pos = by_id[nid].get("position") or {}
            try:
                same = float(pos.get("x")) == float(x) and float(pos.get("y")) == float(y)
            except (TypeError, ValueError):
                return False
            if not same:
                return False
        return True

    if not any(matches(layout) for layout in (_SLICE1_POSITIONS, _V3_DRAFT_POSITIONS, _V3_TIGHT)):
        return None
    moved = copy.deepcopy(graph)
    for node in moved["nodes"]:
        if node.get("id") in _V3_POSITIONS:
            node["position"] = dict(_V3_POSITIONS[node["id"]])
    return moved


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
            {"id": "timeline", "type": "market_timeline", "position": dict(_V3_POSITIONS["timeline"]), "enabled": True,
             "config": {"event_id": "ev_launch_northline", "ad_id": "rec_4c91e2"}},
            {"id": "ad_feed", "type": "ad_feed", "position": dict(_V3_POSITIONS["ad_feed"]), "enabled": True,
             "config": {"advertiser": "Northline Markets", "status": "", "date_from": "",
                        "date_to": "", "longevity_min": "", "ad_id": "rec_4c91e2"}},
            {"id": "ad_breakdown", "type": "ad_breakdown", "position": dict(_V3_POSITIONS["ad_breakdown"]), "enabled": True, "config": {}},
            {"id": "funnel", "type": "funnel_route", "position": dict(_V3_POSITIONS["funnel"]), "enabled": True, "config": {}},
            {"id": "teardown", "type": "teardown", "position": dict(_V3_POSITIONS["teardown"]), "enabled": True, "config": {}},
            {"id": "brief", "type": "brief", "position": dict(_V3_POSITIONS["brief"]), "enabled": True, "config": {}},
            {"id": "script", "type": "script", "position": dict(_V3_POSITIONS["script"]), "enabled": True, "config": {"framework": "PAS"}},
            {"id": "voice", "type": "voice", "position": dict(_V3_POSITIONS["voice"]), "enabled": True, "config": {}},
            {"id": "video", "type": "video", "position": dict(_V3_POSITIONS["video"]), "enabled": True, "config": {}},
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
    row = db.board("talking_head")
    if not row:
        db.save_board("talking_head", "Talking head", talking_head_preset(), preset=True)
        return
    migrated = migrate_talking_head_layout(row.get("graph") or {})
    if migrated:
        db.save_board("talking_head", row.get("name") or "Talking head", migrated, preset=True)
