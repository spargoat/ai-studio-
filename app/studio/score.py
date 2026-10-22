"""Local quality gate, modelled on the AMN Production OS 5-way score (identity, visual, copy, brand, engagement).
Identity and visual are measured locally with OpenCV (YuNet face detector + SFace embedder, the same models as
aar58/tools/faceid.py). Copy, brand and engagement are NOT SCORED until an LLM key is configured; they are stored
as NULL and shown as 'not scored', never as a number. No network, no paid calls."""
import json
from pathlib import Path
from . import APP_DIR

cv2 = None
np = None

def _cv():
    """OpenCV is an optional extra. The Vercel request function does not install it."""
    global cv2, np
    if cv2 is None or np is None:
        import cv2 as _cv2
        import numpy as _np
        try:
            _cv2.utils.logging.setLogLevel(_cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        cv2, np = _cv2, _np
    return cv2, np

def available():
    try:
        _cv()
        return True
    except ImportError:
        pass
    try:
        from . import score_onnx
        return score_onnx.available()
    except Exception:
        return False


DRY_RUN_IDENTITY = "dry run, self-reference"


def mark_dry_still(score):
    """A dry-run still is painted from the persona's own reference, so the cosine is not a match."""
    flags = [f for f in (score.get("flags") or []) if not str(f).startswith("identity")]
    return {**score, "identity": None, "identity_min": None, "identity_note": DRY_RUN_IDENTITY,
            "flags": flags, "passed": False if flags else None}


def unscored(cfg, reason, visual=None, flags=None):
    """A stored score for a file we could not measure. The UI says 'not scored'."""
    q = (cfg or {}).get("quality") or {} if isinstance(cfg, dict) else {}
    thr = float(q.get("identity_threshold", 0.45))
    note = reason if str(reason).startswith("not scored") else f"not scored: {reason}"
    flags = list(flags or [])
    passed = None if visual is None and not flags else (False if flags else None)
    return {"identity": None, "identity_min": None, "identity_note": note, "identity_threshold": thr,
            "visual": visual, "flags": flags, "passed": passed,
            **{k: None for k in NOT_SCORED},
            "not_scored": {"identity": "not scored", "visual": "not scored" if visual is None else None,
                           **{k: "not scored: needs an LLM key" for k in NOT_SCORED}}}

def _models():
    from .assets import model_dir
    return model_dir()
NOT_SCORED = ("copy", "brand", "engagement")
IMG = (".png", ".jpg", ".jpeg", ".webp")
VID = (".mp4", ".mov", ".webm")
_ref_cache = {}


_dets = {}
def _det(shape):
    cv2, _np = _cv()
    k = (shape[1], shape[0])
    if k not in _dets:
        _dets[k] = cv2.FaceDetectorYN.create(str(_models() / "yunet.onnx"), "", k, 0.7)
    return _dets[k]


_rec = None
def _recog():
    global _rec
    cv2, _np = _cv()
    if _rec is None:
        _rec = cv2.FaceRecognizerSF.create(str(_models() / "sface.onnx"), "")
    return _rec


def embed(img):
    """(embedding, face_box_in_original_px) of the largest face, or (None, None)."""
    try:
        cv2, _np = _cv()
    except ImportError:
        from . import score_onnx
        rgb = img[:, :, ::-1] if getattr(img, "ndim", 0) == 3 else img
        return score_onnx.embed_rgb(rgb)
    h, w = img.shape[:2]
    s = min(1.0, 1280 / max(h, w))
    im = cv2.resize(img, (int(w * s), int(h * s))) if s < 1 else img
    _, f = _det(im.shape).detect(im)
    if f is None:
        return None, None
    f = max(f, key=lambda r: r[2] * r[3])
    return _recog().feature(_recog().alignCrop(im, f)), [int(v / s) for v in f[:4]]


def ref_paths(cfg, persona):
    """Local identity images when a refs/<persona> directory exists, else face-lock Blob refs.

    The directory keeps the offline quality-gate test on the checked-in Maksim
    photos. Outfit, character, detail, handwriting and backstory are not
    identity refs. face_sheet_secondary is included only after an identity ref,
    never as the primary face.
    """
    d = APP_DIR / "refs" / persona
    ps = sorted(p for p in d.glob("*") if p.suffix.lower() in IMG) if d.is_dir() else []
    if ps:
        return [str(p) for p in ps]
    from .persona import face_lock_entries, materialize
    out = []
    raw = (cfg.get("_personas") or {}).get(persona, {}).get("refs") or []
    for entry in face_lock_entries(raw):
        try:
            path = materialize(entry)
        except Exception:
            continue
        if path and Path(path).exists() and str(path).lower().endswith(IMG):
            out.append(str(path))
    return out


def ref_embeddings(cfg, persona):
    cv2, _np = _cv()
    if persona not in _ref_cache:
        out = []
        for p in ref_paths(cfg, persona):
            img = cv2.imread(p)
            e = embed(img)[0] if img is not None else None
            if e is not None:
                out.append((Path(p).name, e))
        _ref_cache[persona] = out
    return _ref_cache[persona]


def frames(path, n=5):
    try:
        cv2, _np = _cv()
    except ImportError:
        from .storage import is_url, local
        from . import score_onnx
        if is_url(str(path)):
            path = local(path)
        return score_onnx.frames(path, n)
    if path.lower().endswith(IMG):
        img = cv2.imread(path)
        return [img] if img is not None else []
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n))
        ok, fr = cap.read()
        if ok:
            out.append(fr)
    cap.release()
    return out


def visual(frs):
    """Technical checks only: sharpness (variance of Laplacian), exposure (mean luma, clipped %), resolution."""
    cv2, np = _cv()
    sharp, luma, clip_lo, clip_hi = [], [], [], []
    for f in frs:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        cols, rows = np.where(g.max(axis=0) > 12)[0], np.where(g.max(axis=1) > 12)[0]
        if len(cols) and len(rows):            # ignore solid pad bars (letter/pillarbox) for exposure/sharpness
            g = g[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        sharp.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        luma.append(float(g.mean()))
        clip_lo.append(float((g <= 5).mean() * 100)); clip_hi.append(float((g >= 250).mean() * 100))
    h, w = frs[0].shape[:2]
    return {"width": w, "height": h, "sharpness": round(float(np.median(sharp)), 1),
            "luma_mean": round(float(np.mean(luma)), 1),
            "clipped_dark_pct": round(float(np.mean(clip_lo)), 2), "clipped_bright_pct": round(float(np.mean(clip_hi)), 2)}


def visual_flags(v, q, stage=None):
    flags = []
    if (stage is None or stage in q.get("resolution_stages", ["still", "finish"])) and min(v["width"], v["height"]) < q.get("min_short_side", 1080):
        flags.append(f"resolution {v['width']}x{v['height']} < {q.get('min_short_side', 1080)} short side")
    if v["sharpness"] < q.get("min_sharpness", 50):
        flags.append(f"soft: sharpness {v['sharpness']} < {q.get('min_sharpness', 50)}")
    lo, hi = q.get("luma_range", [40, 210])
    if not lo <= v["luma_mean"] <= hi:
        flags.append(f"exposure: mean luma {v['luma_mean']} outside {lo}-{hi}")
    if v["clipped_bright_pct"] > q.get("max_clipped_pct", 5) or v["clipped_dark_pct"] > q.get("max_clipped_pct", 5):
        flags.append("clipping > {}%".format(q.get("max_clipped_pct", 5)))
    return flags


def score_file(cfg, persona, path, n_frames=5, stage=None):
    from .storage import is_url, local
    if is_url(str(path)):
        path = local(path)
    try:
        cv2, np = _cv()
    except ImportError:
        from . import score_onnx
        if score_onnx.available():
            return score_onnx.score_file(cfg, persona, path, n_frames, stage)
        return unscored(cfg, "face scoring runtime is not installed")
    q = cfg.get("quality") or {}
    thr = float(q.get("identity_threshold", 0.45))
    frs = frames(path, n_frames)
    if not frs:
        return {"error": "could not read media"}
    refs = ref_embeddings(cfg, persona)
    per_frame, faces = [], 0
    for f in frs:
        e, _ = embed(f)
        if e is None:
            continue
        faces += 1
        if refs:
            per_frame.append(max(float(_recog().match(e, r, cv2.FaceRecognizerSF_FR_COSINE)) for _, r in refs))
    v = visual(frs)
    flags = visual_flags(v, q, stage)
    identity = round(float(np.median(per_frame)), 3) if per_frame else None
    if not refs:
        id_note = f"no usable refs for {persona}"
    elif not faces:
        id_note = "no face found"; flags.append("identity: no face found")
    else:
        id_note = f"median cosine over {len(per_frame)}/{len(frs)} frames vs {len(refs)} refs (best ref per frame)"
        if identity < thr:
            flags.append(f"identity {identity:.3f} < threshold {thr}")
    return {"identity": identity, "identity_min": round(min(per_frame), 3) if per_frame else None,
            "identity_note": id_note, "identity_threshold": thr, "visual": v, "flags": flags,
            "passed": not flags, **{k: None for k in NOT_SCORED},
            "not_scored": {k: "not scored: needs an LLM key" for k in NOT_SCORED}}


def scorable(path):
    from .storage import local
    path = local(path) if path else path
    return bool(path) and str(path).lower().endswith(IMG + VID) and Path(path).exists()
