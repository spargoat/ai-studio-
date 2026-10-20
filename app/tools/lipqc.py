"""Talking-head QC metrics (run with /workspace/.venv_mp/bin/python). Machine metrics only.
usage: lipqc.py name=path[@start:dur] ...   -> prints JSON per clip
Metrics:
  mouth_env_corr  corr(mouth opening, audio log-envelope) at best lag in +-200ms (higher = mouth follows speech)
  lag_ms          that best lag (+ = mouth late)
  onset_ratio     mouth p90 during speech in first 4s / p90 during speech overall (1.0 = no slow start)
  mouth_p95       mouth opening p95, % of face height (articulation range)
  pause_open      share of silent frames with mouth > 50% of speech median (lower = closes in pauses)
  blinks_per_min, sway_pct (camera/head drift range, % frame), head_rot_deg_sd (roll sd)
  beard_sharp / upper_sharp at a fixed 400px face height, beard_over_upper (Sync softening shows as low ratio)
"""
import json, subprocess, sys
import cv2, numpy as np, mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions
TASK = '/workspace/.venv_mp/face_landmarker.task'

def audio_env(path, ss, dur, fps, n):
    cmd = ['ffmpeg', '-v', 'error'] + (['-ss', str(ss), '-t', str(dur)] if ss is not None else []) + \
          ['-i', path, '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']
    a = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32)
    h = int(16000 / fps)
    return np.array([np.log(np.sqrt((a[i*h:(i+1)*h]**2).mean()) + 1e-4) if (i+1)*h <= len(a) else np.log(1e-4) for i in range(n)])

def run(path, ss=None, dur=None):
    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=TASK), running_mode=vision.RunningMode.VIDEO))
    c = cv2.VideoCapture(path); fps = c.get(5) or 30
    if ss: c.set(cv2.CAP_PROP_POS_MSEC, ss * 1000)
    maxn = int(dur * fps) if dur else 10**9
    mo, ear, roll, bs, us, shifts = [], [], [], [], [], []
    prev = None; i = 0
    while i < maxn:
        ok, f = c.read()
        if not ok: break
        H, W = f.shape[:2]
        r = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB)), int(i * 1000 / fps))
        g = cv2.cvtColor(cv2.resize(f, (480, int(480 * H / W))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            (sx, sy), _ = cv2.phaseCorrelate(prev, g); shifts.append((sx / 480, sy / 480))
        prev = g
        if not r.face_landmarks:
            mo.append(np.nan); ear.append(np.nan); roll.append(np.nan); i += 1; continue
        p = np.array([[q.x * W, q.y * H] for q in r.face_landmarks[0]])
        fh = np.linalg.norm(p[152] - p[10])
        mo.append(np.linalg.norm(p[13] - p[14]) / fh)
        e = (np.linalg.norm(p[159] - p[145]) / np.linalg.norm(p[33] - p[133]) +
             np.linalg.norm(p[386] - p[374]) / np.linalg.norm(p[362] - p[263])) / 2
        ear.append(e)
        roll.append(np.degrees(np.arctan2(p[263, 1] - p[33, 1], p[263, 0] - p[33, 0])))
        if i % 3 == 0:  # sharpness at fixed face scale
            s = 400.0 / fh
            fg = cv2.cvtColor(cv2.resize(f, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            q = p * s
            jaw = [234, 132, 58, 172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397, 288, 361, 454, 322, 2, 92]
            m = np.zeros_like(fg, np.uint8); cv2.fillPoly(m, [q[jaw].astype(np.int32)], 1)
            lap = cv2.Laplacian(fg, cv2.CV_32F)
            if m.sum() > 50: bs.append(lap[m > 0].var())
            x0, x1 = int(q[33, 0]), int(q[263, 0]); y0, y1 = int(q[10, 1] + 0.25 * 400), int(q[1, 1] - 20)
            if x1 > x0 and y1 > y0: us.append(lap[max(0, y0):y1, max(0, x0):x1].var())
        i += 1
    mo = np.array(mo); n = len(mo)
    env = audio_env(path, ss, dur, fps, n)
    speech = env > (np.percentile(env, 20) + 0.45 * (np.percentile(env, 95) - np.percentile(env, 20)))
    ok = ~np.isnan(mo)
    best, lag = -9, 0
    for L in range(-6, 7):  # +-200ms at 30fps
        a = mo[max(0, L):n + min(0, L)]; b = env[max(0, -L):n - max(0, L)]; k = ~np.isnan(a)
        cc = np.corrcoef(a[k], b[k])[0, 1]
        if cc > best: best, lag = cc, L
    sp = speech & ok
    first = sp & (np.arange(n) < 4 * fps)
    p90 = np.nanpercentile(mo[sp], 90) if sp.sum() > 5 else np.nan
    onset = (np.nanpercentile(mo[first], 90) / p90) if first.sum() > 5 else np.nan
    med = np.nanmedian(mo[sp]) if sp.sum() > 5 else np.nan
    sil = (~speech) & ok
    pause_open = float((mo[sil] > 0.5 * med).mean()) if sil.sum() > 5 else np.nan
    ear = np.array(ear); thr = np.nanmedian(ear) * 0.55
    bl = 0; inb = False
    for v in ear:
        if v == v and v < thr and not inb: bl += 1; inb = True
        elif v == v and v >= thr: inb = False
    pos = np.cumsum(np.array(shifts), 0) if shifts else np.zeros((1, 2))
    rs = lambda x: round(float(x), 3)
    return {"secs": rs(n / fps), "detect": rs(ok.mean()), "mouth_env_corr": rs(best), "lag_ms": int(lag * 1000 / fps),
            "onset_ratio": rs(onset), "mouth_p95": rs(np.nanpercentile(mo, 95) * 100), "pause_open": rs(pause_open),
            "blinks_per_min": rs(bl / (n / fps) * 60), "sway_pct": rs((pos.max(0) - pos.min(0)).mean() * 100),
            "head_roll_sd": rs(np.nanstd(roll)), "beard_sharp": rs(np.mean(bs) if bs else np.nan),
            "upper_sharp": rs(np.mean(us) if us else np.nan),
            "beard_over_upper": rs((np.mean(bs) / np.mean(us)) if bs and us else np.nan)}

if __name__ == "__main__":
    out = {}
    for arg in sys.argv[1:]:
        name, _, spec = arg.partition('=')
        path, _, rng = spec.partition('@')
        ss, dur = (map(float, rng.split(':')) if rng else (None, None))
        out[name] = run(path, ss, dur)
        print(name, json.dumps(out[name]), flush=True)
