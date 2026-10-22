"""`studio export`: bundle approved library items + captions into a ready-to-send folder (and .zip).
Offline only: copies files, converts .ass karaoke captions to .srt, writes post caption text + manifest. No uploads."""
import json, re, shutil, zipfile
from pathlib import Path
from . import library
from .config import load_config
from .db import now
from .names import filename_stem, purpose_name

_DLG = re.compile(r"^Dialogue:\s*\d+,([^,]+),([^,]+),(?:[^,]*,){6}(.*)$")


def _sec(t):
    h, m, s = t.strip().split(":"); return int(h) * 3600 + int(m) * 60 + float(s)


def _srt_t(x):
    ms = int(round(x * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def ass_to_srt(ass_path):
    """Karaoke ASS (one event per highlighted word) -> one SRT cue per caption line."""
    cues = []
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        m = _DLG.match(line)
        if not m:
            continue
        a, b = _sec(m[1]), _sec(m[2])
        txt = re.sub(r"\{[^}]*\}", "", m[3]).replace("\\N", " ").replace("\\n", " ").strip()
        if cues and cues[-1][2] == txt and a - cues[-1][1] < 0.05:
            cues[-1][1] = b
        else:
            cues.append([a, b, txt])
    return "".join(f"{i}\n{_srt_t(a)} --> {_srt_t(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1))


def _brief(job_dir):
    try:
        root = Path(job_dir)
    except TypeError:
        return {}
    if not root.exists():
        return {}
    p = next(iter(sorted(root.glob("brief/v*/brief.json"))), None)
    return (json.loads(p.read_text()).get("brief") or {}) if p else {}


def _caption_body(db, row, src):
    """Post text for caption.txt. The job brief wins; a local brief.json is the fallback.

    On Vercel the media file is a Blob cache download, so it does not sit next
    to brief/v1/brief.json. Reading only that path writes a one-byte file.
    """
    brief = {}
    job = db.job(row["job_id"]) if row.get("job_id") else None
    if job:
        brief = job.get("brief") or {}
    if not (brief.get("caption") or brief.get("post_caption") or brief.get("script")):
        parents = src.parents
        located = _brief(parents[2]) if len(parents) > 2 else {}
        if located:
            brief = located
    post = (brief.get("caption") or brief.get("post_caption") or brief.get("script") or "").strip()
    tags = brief.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    if tags:
        post = (post + "\n\n" + " ".join(str(t) for t in tags)).strip()
    return (post + "\n") if post else "\n"


def _export_name(stage, aspect, suffix):
    """compose_9x16.mp4, not the Blob cache hash the file was downloaded as."""
    if not aspect or aspect == "source":
        return f"{stage}{suffix}"
    tag = str(aspect).replace(":", "x")
    stem = "compose" if stage == "compose" else stage
    return f"{stem}_{tag}{suffix}"


def export(db, out_dir, persona=None, promoted=False, job=None, ids=None, zip_it=True, limit=500, live_only=False, stages=("finish", "compose")):
    rows = library.rows(db, persona=persona, promoted=promoted, limit=limit)
    if job:
        rows = [r for r in rows if r["job_id"] == job]
    if ids:                                                           # explicit selection: exactly those items
        rows = [r for r in library.rows(db, limit=100000) if r["id"] in set(ids)]
    best = {}
    for r in rows:                                                    # one per job+stage: promoted first, then newest version
        k = (r["job_id"], r["stage"]); b = best.get(k)
        if not b or (r["promoted"], r["version"]) > (b["promoted"], b["version"]):
            best[k] = r
    if not ids:
        rows = [r for r in rows if best[(r["job_id"], r["stage"])] is r]
    if stages:
        rows = [r for r in rows if r["stage"] in stages]
        comp = {r["job_id"] for r in rows if r["stage"] == "compose"}
        rows = [r for r in rows if not (r["stage"] == "finish" and r["job_id"] in comp)]   # compose supersedes finish
    if live_only:
        # compose is local ffmpeg (logged mode=dry); it counts as live when its job's paid stages ran live
        live_jobs = {r["job_id"] for r in library.rows(db, limit=100000) if r["mode"] == "live"} | \
                    {j["id"] for j in db.jobs() if j["mode"] == "live"}
        rows = [r for r in rows if r["mode"] == "live" or (r["stage"] == "compose" and r["job_id"] in live_jobs)]
    if ids:
        rows = [r for r in rows if r["id"] in set(ids)]
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    displays = {k: (v.get("display") or k) for k, v in (load_config().get("_personas") or {}).items()}
    items = []
    used = set()
    for r in rows:
        from .storage import local
        src = Path(local(r["path"] or "") or "")
        if not src.exists():
            items.append({"library_id": r["id"], "skipped": f"missing file {src}"}); continue
        label = purpose_name(displays.get(r.get("persona")) or r.get("persona") or "Asset", r.get("stage"), r.get("version"))
        stem = filename_stem(label)
        folder = stem
        if folder in used:
            folder = f"{stem} ({r['id']})"
        used.add(folder)
        d = out / folder
        d.mkdir(exist_ok=True)
        run_dir = src.parent; files = []
        if (run_dir / "meta.json").exists():
            meta = json.loads((run_dir / "meta.json").read_text())
        else:
            stored = db.one("SELECT meta_json FROM stage_runs WHERE id=?", (r.get("stage_run_id"),))
            meta = json.loads((stored or {}).get("meta_json") or "{}") if stored else {}
        media = list((meta.get("exports") or {}).items()) or [("source", str(src))]
        for asp, p in media:
            raw = str(p)
            p = Path(local(raw) or "")
            if not p.exists():
                continue
            if asp == "source":
                name = f"{stem}{p.suffix}"
            else:
                name = _export_name(r["stage"], asp, p.suffix or ".mp4")
            shutil.copy2(p, d / name); files.append(name)
            tag = str(asp).replace(":", "x")
            ass = run_dir / f"captions_{tag}.ass"
            if not ass.exists() and not raw.startswith(("https://", "http://")):
                ass = Path(raw).parent / f"captions_{tag}.ass"
            if ass.exists():
                srt = d / (Path(name).stem + ".srt"); srt.write_text(ass_to_srt(ass), encoding="utf-8"); files.append(srt.name)
        if (run_dir / "poster.jpg").exists():
            shutil.copy2(run_dir / "poster.jpg", d / "poster.jpg"); files.append("poster.jpg")
        (d / "caption.txt").write_text(_caption_body(db, r, src), encoding="utf-8")
        files.append("caption.txt")
        items.append({"library_id": r["id"], "purpose": label, "job": r["job_id"], "stage": r["stage"], "version": r["version"],
                      "persona": r["persona"], "template": r["template"], "mode": r["mode"], "identity": r["identity"],
                      "passed": r["passed"], "reason": r["reason"], "promoted": bool(r["promoted"]),
                      "folder": d.name, "files": files})
    man = {"exported_at": now(), "filters": {"persona": persona, "promoted": promoted, "job": job, "ids": ids},
           "count": sum(1 for i in items if "files" in i), "items": items,
           "note": "dry-run (mock) items are marked mode=dry; send only mode=live items"}
    (out / "manifest.json").write_text(json.dumps(man, indent=2))
    z = None
    if zip_it:
        z = out.with_suffix(".zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:      # media is already compressed
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(out.parent))
    return man, (str(z) if z else None)
