"""Tune quality.identity_threshold on local images only (no network). Scores files against refs/maksim/ the same
way the gate does (largest face, best ref, median over sampled frames). Writes data/identity_tuning.csv."""
import csv, glob, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.config import load_config
from studio import score
import cv2, numpy as np

W = "/workspace"
GOOD = (glob.glob(f"{W}/aar58/ref_lock/face_sheet/panels/0[3-8]*.jpg")            # turnaround panels (01/02 are refs)
        + [f"{W}/aar58/maksim/gen/a9.jpg", f"{W}/aar58/maksim/gen/a2.jpg"]
        + [f"{W}/personas/batch2/maksim/K6_new_york/01.jpg"]  # 02/03 are faceless slides (QC_NOTES) + glob.glob(f"{W}/personas/batch2/maksim/K6_alt_GPT/*.png")
        # only the IG slides confirmed as Maksim's face in aar58/ref_lock/candidates.md (C1, C5-C10, C14)
        + [f"{W}/review_bundle/{x}" for x in ("aleks_ready_to_post/profile_photo.jpg", "aleks_ready_to_post/post1/05.jpg",
           "aleks_ready_to_post/post3/05.jpg", "aleks_ready_to_post/post4/01.jpg", "aleks_ready_to_post/post2/03.jpg",
           "aleks_ready_to_post/post2/04.jpg", "aleks_ready_to_post_batch2/post6_alt/03.jpg", "aleks_ready_to_post/post1/02.jpg")]
        + glob.glob(f"{W}/ai_studio/app/data/jobs/live-maksim-01/*/v*/*.mp4")
        + glob.glob(f"{W}/ai_studio/app/data/jobs/live-maksim-01/still/v*/still.png"))
GOOD = [p for p in GOOD if "final_send" not in p]
BAD = {"marco": glob.glob(f"{W}/personas/marco/face_sheet/panels/*.jpg") + glob.glob(f"{W}/personas/marco/ref_lock/face_sheet/*.jpg"),
       "callum": glob.glob(f"{W}/personas/callum/face_sheet_v6/FS-D*_view.jpg") + glob.glob(f"{W}/personas/callum/batch1_v3/gen/*_face.jpg")
                 + glob.glob(f"{W}/personas/callum/ref_lock/face_sheet/*.jpg"),
       "swap_base (pre-swap real men)": [f"{W}/personas/batch2/src/{n}" for n in ("K6_nyc_DYwtt_04.jpg", "S_maksim_southampton_DeFb_13.jpg",
                                         "M5a_hk_DdrIw_06.jpg", "M5b_hk_DdrIw_09.jpg", "M6_crans_DbqYzW_03.jpg", "M8a_seych_Dd7Ft_02.jpg",
                                         "M8b_seych_Dd7Ft_12.jpg", "S_marco_capdail_DZc_01.jpg")],
       "spokesguy": [p for p in glob.glob(f"{W}/spokesguy/footage/**/*.mp4", recursive=True) if "/._" not in p]}

cfg = load_config(); refs = score.ref_embeddings(cfg, "maksim")


def sc(path, n=8):
    vals = []
    for f in score.frames(path, n):
        e, _ = score.embed(f)
        if e is not None:
            vals.append(max(float(score._recog().match(e, r, cv2.FaceRecognizerSF_FR_COSINE)) for _, r in refs))
    return (round(float(np.median(vals)), 3), round(min(vals), 3), len(vals)) if vals else (None, None, 0)


rows = []
for p in sorted(set(GOOD)):
    rows.append(("good", "maksim", p, *sc(p)))
for grp, ps in BAD.items():
    for p in sorted(set(ps)):
        if Path(p).exists():
            rows.append(("bad", grp, p, *sc(p)))
out = Path(__file__).resolve().parent.parent / "data/identity_tuning.csv"
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["label", "group", "path", "median", "min_frame", "faces"]); w.writerows(rows)
g = sorted(r[3] for r in rows if r[0] == "good" and r[3] is not None)
b = sorted(r[3] for r in rows if r[0] == "bad" and r[3] is not None)
print(f"good n={len(g)} min={g[0]} p10={g[len(g)//10]} median={g[len(g)//2]} max={g[-1]}  (no face: {sum(1 for r in rows if r[0]=='good' and r[3] is None)})")
print(f"bad  n={len(b)} min={b[0]} median={b[len(b)//2]} p90={b[int(len(b)*.9)]} max={b[-1]}  (no face: {sum(1 for r in rows if r[0]=='bad' and r[3] is None)})")
for grp in BAD:
    v = sorted(r[3] for r in rows if r[1] == grp and r[3] is not None)
    if v: print(f"  {grp:32} n={len(v)} min={v[0]} median={v[len(v)//2]} max={v[-1]}")
print("lowest good:", [(Path(r[2]).name, r[3]) for r in sorted((r for r in rows if r[0]=='good' and r[3] is not None), key=lambda r: r[3])[:5]])
print("highest bad:", [(r[1], Path(r[2]).name, r[3]) for r in sorted((r for r in rows if r[0]=='bad' and r[3] is not None), key=lambda r: -r[3])[:5]])
print(f"gap: {round(g[0]-b[-1],3)}  midpoint: {round((g[0]+b[-1])/2,3)}")
