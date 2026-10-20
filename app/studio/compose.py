"""Compose step (after finish), local ffmpeg only, $0.
- word-timed burned-in captions from a VTT (words spread across each cue) or from the script (words spread across
  the detected speech span, weighted by length + punctuation pauses)
- optional text overlays [{text, start, end, pos: top|center|bottom}]
- optional music bed, side-chain ducked under the voice
- exports per aspect (9:16, 1:1, 16:9), cropped around the detected face or padded (solid colour, no blur)."""
import os
import re
import subprocess
from pathlib import Path
from .media import ff, duration

FONT_DIR = "/workspace/creative/fonts/twf_caption"
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


# ---------- timing
def _ts(t):
    h, m, s = t.split(":") if t.count(":") == 2 else ("0", *t.split(":"))
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_vtt(path):
    cues, lines = [], Path(path).read_text().splitlines()
    for i, l in enumerate(lines):
        if "-->" in l:
            a, b = [x.strip().split(" ")[0] for x in l.split("-->")]
            txt = []
            for t in lines[i + 1:]:
                if not t.strip():
                    break
                txt.append(re.sub(r"<[^>]+>", "", t.strip()))
            cues.append((_ts(a), _ts(b), " ".join(txt)))
    return cues


def _weights(words):
    return [len(w) + (4 if w[-1:] in ".?!" else 2 if w[-1:] in ",;:" else 0) + 1 for w in words]


def spread(text, start, end):
    words = text.split()
    if not words:
        return []
    ws = _weights(words); tot = sum(ws); t = start; out = []
    for w, k in zip(words, ws):
        d = (end - start) * k / tot
        out.append((w, round(t, 3), round(t + d, 3))); t += d
    return out


def speech_span(media, noise_db=-35, min_sil=0.25):
    """(start, end) of speech in the audio track via silencedetect. Local, no models."""
    from .assets import ffmpeg_bin
    r = subprocess.run([ffmpeg_bin("ffmpeg"), "-i", str(media), "-af", f"silencedetect=n={noise_db}dB:d={min_sil}", "-f", "null", "-"],
                       capture_output=True, text=True)
    d = duration(media) or 0
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    s = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    e = starts[-1] if starts and starts[-1] > s and (len(ends) < len(starts)) else d
    return round(s, 3), round(e, 3)


def words_from_alignment(al, offset=0.0):
    """ElevenLabs character alignment -> [(word, start, end)] exact timings."""
    chars, st, en = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    out, cur, a = [], "", None
    for ch, s0, e0 in zip(chars, st, en):
        if ch.isspace():
            if cur:
                out.append((cur, round(a + offset, 3), round(prev + offset, 3))); cur = ""
            continue
        if not cur:
            a = s0
        cur += ch; prev = e0
    if cur:
        out.append((cur, round(a + offset, 3), round(prev + offset, 3)))
    return out


def word_times(script=None, vtt=None, media=None, alignment=None, offset=0.0):
    if alignment:
        import json as _j
        return words_from_alignment(_j.loads(Path(alignment).read_text()), offset)
    if vtt:
        return [w for a, b, t in parse_vtt(vtt) for w in spread(t, a, b)]
    s, e = speech_span(media)
    return spread(script, s, e)


# ---------- ASS captions
def _ass_t(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_file(words, w, h, out, c):
    size = int(c.get("size_pct", 6.2) / 100 * min(w, h) * (1.25 if w < h else 1))
    font = c.get("font_name", "TWF Caption")
    hi = c.get("highlight", "&H0000D7FF")   # ASS BGR: gold
    y = int(h * c.get("y_pct", 72) / 100)
    n = int(c.get("words_per_chunk", 3))
    up = c.get("uppercase", False)
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
            f"{max(2, size // 12)},{max(1, size // 30)},5,60,60,0,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    for i in range(0, len(words), n):
        chunk = words[i:i + n]
        for j, (wd, a, b) in enumerate(chunk):
            end = chunk[j + 1][1] if j + 1 < len(chunk) else b
            txt = " ".join((f"{{\\c{hi}}}{x}{{\\c&H00FFFFFF&}}" if k == j else x)
                           for k, (x, _, _) in enumerate(chunk))
            ev.append(f"Dialogue: 0,{_ass_t(a)},{_ass_t(end)},Cap,,0,0,0,,{{\\pos({w // 2},{y})}}"
                      + (txt.upper() if up else txt))
    Path(out).write_text(head + "\n".join(ev) + "\n")
    return out


def _esc(p):
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# ---------- framing
def face_center(video, n=5):
    from . import score
    if not score.available():
        return None
    xs, ys = [], []
    for f in score.frames(str(video), n):
        _, box = score.embed(f)
        if box:
            xs.append(box[0] + box[2] / 2); ys.append(box[1] + box[3] / 2)
    if not xs:
        return None
    xs.sort(); ys.sort()
    return xs[len(xs) // 2], ys[len(ys) // 2]


def frame_filter(sw, sh, tw, th, mode, center, pad_color="black", zoom=0.5, side="left"):
    """Scale/crop (around the face) or pad the source to tw x th. No blur anywhere.
    pad  : whole frame, solid bars.
    crop : fill, cropped around the face.
    zoom : between pad (0) and crop (1): scaled by fit+zoom*(fill-fit), cropped around the face, rest solid colour.
    side : whole frame at full height, placed off-centre (left/right third) on solid colour, leaving room for text."""
    if mode == "side":
        fs = th / sh; w2 = min(round(sw * fs / 2) * 2, tw)
        x = {"left": int(tw * 0.08), "right": int(tw * 0.92) - w2}.get(side, (tw - w2) // 2)
        return (f"scale=-2:{th},crop={w2}:{th},pad={tw}:{th}:{max(0, min(x, tw - w2))}:0:color={pad_color},setsar=1")
    if mode == "zoom":
        fit, fill = min(tw / sw, th / sh), max(tw / sw, th / sh)
        z = min(max(float(zoom), 0.0), 1.0); s = fit + z * (fill - fit)
        w2, h2 = round(sw * s / 2) * 2, round(sh * s / 2) * 2
        cw, ch = min(w2, tw), min(h2, th)
        cx, cy = (center or (sw / 2, sh * 0.4)); cx, cy = cx * s, cy * s
        x = int(min(max(cx - cw / 2, 0), w2 - cw)); y = int(min(max(cy - ch * 0.42, 0), h2 - ch))
        return (f"scale={w2}:{h2},crop={cw}:{ch}:{x}:{y},"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={pad_color},setsar=1")
    if mode == "pad":
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={pad_color},setsar=1")
    s = max(tw / sw, th / sh)
    w2, h2 = round(sw * s / 2) * 2, round(sh * s / 2) * 2
    cx, cy = (center or (sw / 2, sh * 0.4))
    cx, cy = cx * s, cy * s
    x = int(min(max(cx - tw / 2, 0), w2 - tw)); y = int(min(max(cy - th * 0.42, 0), h2 - th))  # face a bit above centre
    return f"scale={w2}:{h2},crop={tw}:{th}:{x}:{y},setsar=1"


def overlay_filters(overlays, w, h, font):
    out = []
    for o in overlays or []:
        y = {"top": "h*0.08", "center": "(h-text_h)/2", "bottom": "h*0.86"}.get(o.get("pos", "top"), "h*0.08")
        size = int(o.get("size_pct", 7.5 if h > w else 5) / 100 * min(w, h))   # 9:16 default bigger (was 5%)
        txt = o["text"].replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")
        en = f":enable='between(t,{o.get('start', 0)},{o.get('end', 9999)})'"
        out.append(f"drawtext=fontfile='{font}':text='{txt}':fontcolor=white:fontsize={size}:x=(w-text_w)/2:y={y}:"
                   f"box=1:boxcolor=black@0.55:boxborderw={size // 3}{en}")
    return out


# ---------- main
def compose(src, out_dir, params, script=None, fast=False):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cap = params.get("captions") or {}
    fonts = cap.get("fonts_dir", FONT_DIR)
    bold = str(Path(fonts) / "TWFCaption-Bold.ttf")
    words = []
    is_img = str(src).lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    if cap.get("enabled", True) and not is_img:
        al = params.get("_alignment") if cap.get("use_alignment", True) else None
        words = word_times(script=None if cap.get("vtt") else script, vtt=cap.get("vtt"), media=src,
                           alignment=al, offset=float(params.get("_alignment_offset") or 0))
    from .assets import ffmpeg_bin
    from .media import FONT
    sw, sh = map(int, subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height", "-of", "csv=p=0", str(src)],
                                     capture_output=True, text=True).stdout.strip().split(",")[:2])
    center = face_center(src, 1 if fast else 5)
    music = params.get("music") or {}
    div = 4 if fast else 1

    def one(asp):
        tw, th = [v // div // 2 * 2 for v in ASPECTS[asp]]
        mode = (params.get("framing") or {}).get(asp, "crop" if asp != "16:9" else "pad")
        tag = asp.replace(":", "x")
        vf = [frame_filter(sw, sh, tw, th, mode, center, params.get("pad_color", "black"),
                           params.get("zoom", 0.5), params.get("side", "left"))]
        if words:
            ass = ass_file(words, tw, th, out_dir / f"captions_{tag}.ass", cap)
            vf.append(f"subtitles='{_esc(ass)}':fontsdir='{_esc(fonts)}'")
        vf += overlay_filters(params.get("overlays"), tw, th, bold if Path(bold).exists() else FONT)
        if is_img:
            out = out_dir / f"compose_{tag}.png"
            ff(["-i", str(src), "-vf", ",".join(vf), "-frames:v", "1", str(out)])
            return asp, str(out)
        out = out_dir / f"compose_{tag}.mp4"
        args = ["-i", str(src)]
        if music.get("path"):
            vol = music.get("volume", 0.18)
            args += ["-stream_loop", "-1", "-i", music["path"]]
            duck = music.get("duck", True)
            af = (f"[1:a]volume={vol},aresample=48000[m];[0:a]asplit=2[v][sc];"
                  + (f"[m][sc]sidechaincompress=threshold={music.get('duck_threshold', 0.03)}:ratio={music.get('duck_ratio', 8)}:"
                     f"attack=20:release={music.get('duck_release_ms', 400)}[md];" if duck else "[m]anull[md];[sc]anullsink;")
                  + f"[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
            args += ["-filter_complex", f"[0:v]{','.join(vf)}[vo];{af}", "-map", "[vo]", "-map", "[a]"]
        else:
            args += ["-vf", ",".join(vf), "-map", "0:v", "-map", "0:a?"]
        args += ["-c:v", "libx264", "-crf", "20", "-preset", os.environ.get("STUDIO_FFMPEG_PRESET", "medium"),
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)]
        ff(args, timeout=900)
        return asp, str(out)
    from concurrent.futures import ThreadPoolExecutor
    fmts = params.get("formats", ["9:16", "1:1", "16:9"])
    if fast and os.environ.get("STUDIO_COMPOSE_ALL") != "1":
        fmts = fmts[:1]                       # tests: one aspect; STUDIO_COMPOSE_ALL=1 or STUDIO_MOCK_FAST=0 = full
    with ThreadPoolExecutor(max_workers=len(fmts) or 1) as ex:      # aspects encode in parallel
        exports = dict(ex.map(one, fmts))
    return {"exports": exports, "words": len(words), "face_center": center, "source": str(src),
            "caption_source": ("elevenlabs alignment" if words and params.get("_alignment") and cap.get("use_alignment", True)
                               else "vtt" if cap.get("vtt") else "script"), "music": music.get("path")}
