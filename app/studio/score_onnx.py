"""Identity and visual scores without OpenCV.

YuNet and SFace are the same ONNX files FaceDetectorYN and FaceRecognizerSF
load. OpenCV is too large for the request function, so this path runs them
with onnxruntime and measures sharpness with numpy. When neither runtime can
be imported, score.unscored() is what the UI shows.
"""
import os
from pathlib import Path

os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "3")

_yunet = None
_sface = None
_ref_cache = {}

_DST = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)
_DST_MEAN = (56.0262, 71.9008)


def available():
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _np():
    import numpy as np
    return np


def _session(kind):
    global _yunet, _sface
    import onnxruntime as ort
    from .assets import model_dir
    models = model_dir()
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    if kind == "yunet":
        if _yunet is None:
            _yunet = ort.InferenceSession(str(models / "yunet.onnx"), opts, providers=["CPUExecutionProvider"])
        return _yunet
    if _sface is None:
        _sface = ort.InferenceSession(str(models / "sface.onnx"), opts, providers=["CPUExecutionProvider"])
    return _sface


def read_rgb(path):
    from PIL import Image
    np = _np()
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"))
    except Exception:
        return None


def _nms(boxes, scores, thr=0.3, top_k=5000):
    np = _np()
    if len(boxes) == 0:
        return []
    x1, y1, w, h = boxes.T
    areas = np.maximum(w * h, 1e-6)
    order = scores.argsort()[::-1][:top_k]
    keep = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        if rest.size == 0:
            break
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x1[i] + w[i], x1[rest] + w[rest])
        yy2 = np.minimum(y1[i] + h[i], y1[rest] + h[rest])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter)
        order = rest[iou <= thr]
    return keep


def detect(rgb, score_thr=0.7):
    """Largest-face row (x, y, w, h, 5 landmarks, score) in original pixels, or None."""
    np = _np()
    h, w = rgb.shape[:2]
    scale = min(640 / w, 640 / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    from PIL import Image
    resized = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR))
    pad = np.zeros((640, 640, 3), np.uint8)
    pad[:nh, :nw] = resized
    bgr = pad[:, :, ::-1]
    blob = np.ascontiguousarray(bgr.transpose(2, 0, 1)[None]).astype(np.float32)
    outs = _session("yunet").run(None, {"input": blob})
    faces = []
    for i, stride in enumerate((8, 16, 32)):
        cls = outs[i].reshape(-1)
        obj = outs[i + 3].reshape(-1)
        bbox = outs[i + 6].reshape(-1, 4)
        kps = outs[i + 9].reshape(-1, 10)
        cols = 640 // stride
        score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        idx = np.flatnonzero(score >= score_thr)
        if len(idx) == 0:
            continue
        r, c = idx // cols, idx % cols
        bw = np.exp(bbox[idx, 2]) * stride
        bh = np.exp(bbox[idx, 3]) * stride
        cx = (c + bbox[idx, 0]) * stride
        cy = (r + bbox[idx, 1]) * stride
        pts = np.zeros((len(idx), 15), np.float32)
        pts[:, 0] = cx - bw / 2
        pts[:, 1] = cy - bh / 2
        pts[:, 2] = bw
        pts[:, 3] = bh
        for n in range(5):
            pts[:, 4 + 2 * n] = (kps[idx, 2 * n] + c) * stride
            pts[:, 4 + 2 * n + 1] = (kps[idx, 2 * n + 1] + r) * stride
        pts[:, 14] = score[idx]
        faces.append(pts)
    if not faces:
        return None
    faces = np.vstack(faces)
    faces = faces[_nms(faces[:, :4], faces[:, 14])]
    faces[:, 0:14] /= scale
    return max(faces, key=lambda row: float(row[2] * row[3]))


def _similarity(src):
    """2x3 map from the 112x112 crop back to the source, matching FaceRecognizerSF."""
    np = _np()
    src = np.asarray(src, np.float64)
    dst = np.asarray(_DST, np.float64)
    src_mean = src.mean(0)
    src_d = src - src_mean
    dst_d = dst - np.asarray(_DST_MEAN)
    A = (dst_d.T @ src_d) / 5.0
    d = np.array([1.0, -1.0 if np.linalg.det(A) < 0 else 1.0])
    u, s, vt = np.linalg.svd(A)
    tol = float(s.max()) * 2 * np.finfo(np.float32).eps
    rank = int((s > tol).sum())
    if rank == 1 and np.linalg.det(u) * np.linalg.det(vt) <= 0:
        d = np.array([1.0, -1.0])
    T = u @ np.diag(d) @ vt
    var = float((src_d ** 2).sum() / 5.0) or 1.0
    scale = float((s * d).sum() / var)
    M = np.zeros((2, 3), np.float64)
    M[:, :2] = scale * T
    M[:, 2] = np.asarray(_DST_MEAN) - scale * (T @ src_mean)
    return M


def _warp(rgb, matrix):
    from PIL import Image
    data = tuple(float(v) for v in matrix.reshape(-1))
    warped = Image.fromarray(rgb).transform((112, 112), Image.Transform.AFFINE, data=data, resample=Image.Resampling.BILINEAR)
    return _np().asarray(warped)


def embed_rgb(rgb):
    """(1, 128) embedding and the face box, or (None, None)."""
    np = _np()
    face = detect(rgb)
    if face is None:
        return None, None
    aligned = _warp(rgb, _similarity(face[4:14].reshape(5, 2)))
    blob = np.ascontiguousarray(aligned[:, :, ::-1].transpose(2, 0, 1)[None]).astype(np.float32)
    feat = _session("sface").run(None, {"data": blob})[0].astype(np.float32)
    return feat, [int(v) for v in face[:4]]


def _cosine(a, b):
    np = _np()
    a = np.asarray(a, np.float64).ravel()
    b = np.asarray(b, np.float64).ravel()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def frames(path, n=5):
    path = str(path)
    if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        img = read_rgb(path)
        return [img] if img is not None else []
    import tempfile
    from .media import ff
    dest = Path(tempfile.mkdtemp())
    try:
        ff(["-i", path, "-vf", "fps=1", "-frames:v", str(n), str(dest / "f%02d.jpg")])
    except Exception:
        return []
    out = []
    for shot in sorted(dest.glob("f*.jpg")):
        img = read_rgb(shot)
        if img is not None:
            out.append(img)
    return out


def visual(frs):
    np = _np()
    sharp, luma, clip_lo, clip_hi = [], [], [], []
    for rgb in frs:
        g = (0.114 * rgb[:, :, 2] + 0.587 * rgb[:, :, 1] + 0.299 * rgb[:, :, 0]).astype(np.float64)
        cols, rows = np.where(g.max(axis=0) > 12)[0], np.where(g.max(axis=1) > 12)[0]
        if len(cols) and len(rows):
            g = g[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        if g.shape[0] < 3 or g.shape[1] < 3:
            lap = g
        else:
            lap = g[1:-1, 2:] + g[1:-1, :-2] + g[2:, 1:-1] + g[:-2, 1:-1] - 4 * g[1:-1, 1:-1]
        sharp.append(float(lap.var()))
        luma.append(float(g.mean()))
        clip_lo.append(float((g <= 5).mean() * 100))
        clip_hi.append(float((g >= 250).mean() * 100))
    h, w = frs[0].shape[:2]
    return {"width": int(w), "height": int(h), "sharpness": round(float(np.median(sharp)), 1),
            "luma_mean": round(float(np.mean(luma)), 1),
            "clipped_dark_pct": round(float(np.mean(clip_lo)), 2),
            "clipped_bright_pct": round(float(np.mean(clip_hi)), 2)}


def score_file(cfg, persona, path, n_frames=5, stage=None):
    from .score import NOT_SCORED, ref_paths, unscored, visual_flags
    np = _np()
    q = cfg.get("quality") or {}
    thr = float(q.get("identity_threshold", 0.45))
    frs = frames(path, n_frames)
    if not frs:
        return {"error": "could not read media"}
    measured = visual(frs)
    flags = visual_flags(measured, q, stage)
    try:
        paths = ref_paths(cfg, persona)
        refs = []
        for p in paths:
            img = read_rgb(p)
            feat = embed_rgb(img)[0] if img is not None else None
            if feat is not None:
                refs.append(feat)
    except Exception as e:
        result = unscored(cfg, f"face models unavailable ({e})", visual=measured, flags=flags)
        return result
    per_frame, faces = [], 0
    for rgb in frs:
        feat, _ = embed_rgb(rgb)
        if feat is None:
            continue
        faces += 1
        if refs:
            per_frame.append(max(_cosine(feat, ref) for ref in refs))
    identity = round(float(np.median(per_frame)), 3) if per_frame else None
    if not refs:
        note = f"not scored: no usable refs for {persona}"
        identity = None
    elif not faces:
        note = "no face found"
        flags.append("identity: no face found")
    else:
        note = f"median cosine over {len(per_frame)}/{len(frs)} frames vs {len(refs)} refs (best ref per frame)"
        if identity < thr:
            flags.append(f"identity {identity:.3f} < threshold {thr}")
    if str(note).startswith("not scored") and not flags:
        passed = None
    else:
        passed = not flags
    return {"identity": identity, "identity_min": round(min(per_frame), 3) if per_frame else None,
            "identity_note": note, "identity_threshold": thr, "visual": measured, "flags": flags,
            "passed": passed, **{k: None for k in NOT_SCORED},
            "not_scored": {k: "not scored: needs an LLM key" for k in NOT_SCORED}}
