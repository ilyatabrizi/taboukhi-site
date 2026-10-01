#!/usr/bin/env python3
"""Where the engraved TABOUKHI sits in every frame of the loop (SPEC §7), for the Home lens.

    python3 scripts/k_track.py

1. Read assets/media/film-sq-1920.mp4 in grayscale (384 frames; its frame = the master frame).
2. Template = the frame-0 engraving box, x 42.4–59.2 %, y 46.3–51.4 % = px 814–1137 × 889–987.
3. Per frame, matchTemplate (TM_CCOEFF_NORMED) in a ±80 px window around the previous centre, at
   0.97–1.05 × the previous scale in 0.01 steps (sub-pixel peak by a parabola fit).
4. Flag any frame scoring below 0.5.
5. Circular 5-frame moving average (the loop closes on itself).
6. Assert frame 192 (the far turn, source frame 90) lands within ±1 % of x 37.2–62.0, y 43.1–49.6.
7. Assert every station point of SPEC §4 C1 is inside the frame in all 384 frames, for both encodes'
   crops (media.json), and check each against the footage:
     house / commission  the k-track mean: inside every frame's tracked engraving box, and every
                         tracked box lies wholly inside both crops
     collection          (0.30, 0.44) sits on the lit arm in every frame
     contact / notices   (0.72, 0.80) is reported (luma range), not asserted beyond being in frame
   Plus: the engraving tracked independently in film-pt-1080.mp4 must agree with k-track mapped
   through the pt crop (so one k-track serves both encodes).

Writes assets/media/k-track.json  {fps:24, frames:384, pts:[[cx,cy,w],…]}  master-frame fractions, 4 dp
       shots/k-track.jpg          every 48th frame with its box, both encodes, for eye review
Exits non-zero if any assertion fails.
"""
import json
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEDIA = ROOT / "assets/media"
SQ = MEDIA / "film-sq-1920.mp4"
PT = MEDIA / "film-pt-1080.mp4"
OUT = MEDIA / "k-track.json"
SHEET = ROOT / "shots/k-track.jpg"

N = 1920
TPL = (814, 889, 1137, 987)            # x0, y0, x1, y1 (exclusive) in frame 0
WIN = 80                               # ± search window, px
SCALES = [0.97 + 0.01 * k for k in range(9)]
FLAG = 0.5
TURN = 192
TURN_BOX = (37.2, 43.1, 62.0, 49.6)    # x0, y0, x1, y1 %, frame 192
TOL = 1.0                              # ± percentage points
STATIONS = {                           # SPEC §4 C1, master fractions (house/commission = k-track mean)
    "collection": (0.30, 0.44),
    "contact": (0.72, 0.80),
}
FAIL = []


def check(ok, msg):
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        FAIL.append(msg)


def read_grey(path):
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    cap.release()
    return out


def subpix(res, x, y):
    """Parabola through the peak and its neighbours, per axis."""
    def off(a, b, c):
        d = a - 2 * b + c
        return 0.0 if abs(d) < 1e-9 else max(-0.5, min(0.5, 0.5 * (a - c) / d))
    h, w = res.shape
    dx = off(res[y, x - 1], res[y, x], res[y, x + 1]) if 0 < x < w - 1 else 0.0
    dy = off(res[y - 1, x], res[y, x], res[y + 1, x]) if 0 < y < h - 1 else 0.0
    return x + dx, y + dy


def track(frames, tpl, c0):
    """[(cx, cy, scale, score)] per frame, px of these frames; template centre c0 at scale 1 in frame 0."""
    th0, tw0 = tpl.shape
    cx, cy, s = c0[0], c0[1], 1.0
    H, W = frames[0].shape
    out = []
    for g in frames:
        best = None
        for k in SCALES:
            sc = s * k
            tw, th = max(8, round(tw0 * sc)), max(4, round(th0 * sc))
            t = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
            x0 = int(round(cx - tw / 2 - WIN)); y0 = int(round(cy - th / 2 - WIN))
            x0c, y0c = max(0, x0), max(0, y0)
            x1c, y1c = min(W, x0 + tw + 2 * WIN), min(H, y0 + th + 2 * WIN)
            if x1c - x0c < tw or y1c - y0c < th:
                continue
            res = cv2.matchTemplate(g[y0c:y1c, x0c:x1c], t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if best is None or mv > best[0]:
                px, py = subpix(res, ml[0], ml[1])
                best = (mv, x0c + px + tw / 2, y0c + py + th / 2, tw / tw0)
        mv, cx, cy, s = best
        out.append((cx, cy, s, mv))
    return out


def second_opinion(frames, raw, flagged, c0, tw0, th0):
    """Flagged frames only: re-match with the frame-0 template ROTATED (−4…+4° in 0.5° steps; the arm
    turns ~3° over the loop, which is what drags the fixed template's score down), at the tracked scale,
    ±24 px around the tracked centre. Returns [(frame, best score, angle, centre offset px)]."""
    g0 = frames[0]
    px, py = int(tw0 * 0.6), int(th0 * 1.2)               # pad so rotated corners come from the frame
    cx0, cy0 = c0
    big = g0[int(cy0 - th0 / 2 - py):int(cy0 + th0 / 2 + py), int(cx0 - tw0 / 2 - px):int(cx0 + tw0 / 2 + px)]
    bc = (big.shape[1] / 2, big.shape[0] / 2)
    rot = {}
    for a in np.arange(-4, 4.01, 0.5):
        r = cv2.warpAffine(big, cv2.getRotationMatrix2D(bc, float(a), 1.0), (big.shape[1], big.shape[0]), flags=cv2.INTER_LINEAR)
        rot[float(a)] = r[py:py + th0, px:px + tw0]
    out = []
    for i in flagged:
        cx, cy, sc, _ = raw[i]
        tw, th = round(tw0 * sc), round(th0 * sc)
        g = frames[i]; W2 = 24
        x0, y0 = int(round(cx - tw / 2 - W2)), int(round(cy - th / 2 - W2))
        best = None
        for a, t in rot.items():
            t = cv2.resize(t, (tw, th), interpolation=cv2.INTER_LINEAR)
            res = cv2.matchTemplate(g[y0:y0 + th + 2 * W2, x0:x0 + tw + 2 * W2], t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if best is None or mv > best[0]:
                qx, qy = subpix(res, ml[0], ml[1])
                best = (mv, a, float(np.hypot(x0 + qx + tw / 2 - cx, y0 + qy + th / 2 - cy)))
        out.append((i, *best))
    return out


def circular_mean(a, k=5):
    r = k // 2
    return np.mean([np.roll(a, -d, axis=0) for d in range(-r, r + 1)], axis=0)


def inside(pt, crop, margin=0.0):
    x, y = pt; x0, y0, cw, ch = crop
    return x0 + margin <= x <= x0 + cw - margin and y0 + margin <= y <= y0 + ch - margin


def box_of(p, aspect):
    cx, cy, w = p; h = w * aspect
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def main():
    media = json.loads((MEDIA / "media.json").read_text())
    crops = {k: media[k]["crop"] for k in ("sq", "pt")}
    assert media["fps"] == 24 and media["frames"] == 384, media

    print(f"reading {SQ.name}")
    frames = read_grey(SQ)
    n = len(frames)
    check(n == 384 and frames[0].shape == (N, N), f"{SQ.name}: {n} frames of {frames[0].shape[1]}×{frames[0].shape[0]}")
    x0, y0, x1, y1 = TPL
    tpl = frames[0][y0:y1, x0:x1]
    tw0, th0 = x1 - x0, y1 - y0
    aspect = th0 / tw0
    print(f"template {tw0}×{th0} px = x {100 * x0 / N:.1f}–{100 * x1 / N:.1f} %, y {100 * y0 / N:.1f}–{100 * y1 / N:.1f} %")

    raw = track(frames, tpl, ((x0 + x1) / 2, (y0 + y1) / 2))
    scores = np.array([r[3] for r in raw])
    flagged = [i for i, sc in enumerate(scores) if sc < FLAG]
    print(f"match score: min {scores.min():.4f} (frame {int(scores.argmin())}), median {np.median(scores):.4f}, "
          f"frame 0 {scores[0]:.4f}")
    print(f"flagged frames (score < {FLAG}): {len(flagged)}"
          + (f", frames {flagged[0]}–{flagged[-1]}" + (" (contiguous)" if flagged == list(range(flagged[0], flagged[-1] + 1)) else f" {flagged}")
             if flagged else ""))
    if flagged:
        so = second_opinion(frames, raw, flagged, ((x0 + x1) / 2, (y0 + y1) / 2), tw0, th0)
        sc2 = np.array([r[1] for r in so]); off = np.array([r[3] for r in so]); ang = np.array([r[2] for r in so])
        print(f"second opinion on the {len(so)} flagged frames (rotated template): score min {sc2.min():.4f}, "
              f"median {np.median(sc2):.4f}; best angle {ang.min():+.1f}…{ang.max():+.1f}°; centre offset from the track "
              f"max {off.max():.2f} px (frame {so[int(off.argmax())][0]}), mean {off.mean():.2f} px")
        check(sc2.min() >= FLAG, f"every flagged frame re-matches at ≥ {FLAG} once the template is rotated (min {sc2.min():.4f})")
        check(off.max() <= 0.005 * N, f"and the re-matched centre is within 0.5 % of the frame ({0.005 * N:.1f} px) of the track (max {off.max():.2f} px)")

    arr = np.array([[cx / N, cy / N, tw0 * s / N] for cx, cy, s, _ in raw])
    sm = circular_mean(arr, 5)
    dev = np.abs(sm - arr).max(axis=0) * N
    print(f"smoothing moved centres by at most {dev[0]:.2f} / {dev[1]:.2f} px, width by {dev[2]:.2f} px")
    # The loop is out-and-back, so frame i and frame 384 − i are the same picture.
    mirror = np.abs(sm[1:] - sm[1:][::-1]).max(axis=0) * N
    print(f"out/back symmetry: max |pts[i] − pts[384−i]| = {mirror[0]:.2f} / {mirror[1]:.2f} / {mirror[2]:.2f} px (x / y / w)")
    pts = np.round(sm, 4)

    # 6. the far turn
    b = [100 * v for v in box_of(pts[TURN], aspect)]
    print(f"frame {TURN} box: x {b[0]:.2f}–{b[2]:.2f} %, y {b[1]:.2f}–{b[3]:.2f} %  (expected x 37.2–62.0, y 43.1–49.6, ±{TOL})")
    check(all(abs(b[i] - TURN_BOX[i]) <= TOL for i in range(4)), f"frame {TURN} lands within ±{TOL} % of the far-turn box")

    # 7. stations
    mean = pts[:, :2].mean(axis=0)
    eng = (round(float(mean[0]), 4), round(float(mean[1]), 4))
    print(f"k-track mean (house / commission station): ({eng[0]}, {eng[1]})  spec ≈ (0.505, 0.475)")
    check(abs(eng[0] - 0.505) <= 0.01 and abs(eng[1] - 0.475) <= 0.01, "k-track mean within ±0.01 of the spec's (0.505, 0.475)")
    stations = {"house": eng, "commission": eng, **STATIONS}
    boxes = [box_of(p, aspect) for p in pts]
    for name, crop in crops.items():
        for st, p in stations.items():
            check(all(inside(p, crop) for _ in range(n)), f"{name} crop {crop}: {st} {p} inside the frame in all {n} frames")
        worst = min(min(bx[0] - crop[0], bx[1] - crop[1], crop[0] + crop[2] - bx[2], crop[1] + crop[3] - bx[3]) for bx in boxes)
        check(worst > 0, f"{name} crop: the whole tracked engraving box is inside the frame in all {n} frames "
                         f"(closest edge {100 * worst:.2f} % of the master)")
    check(all(bx[0] <= eng[0] <= bx[2] and bx[1] <= eng[1] <= bx[3] for bx in boxes),
          f"house/commission point sits inside the tracked engraving box in all {n} frames")
    # footage: luma in a 15 px patch at each fixed station point
    def patch(g, p, r=7):
        x, y = int(round(p[0] * N)), int(round(p[1] * N))
        return float(g[y - r:y + r + 1, x - r:x + r + 1].mean())
    col = np.array([patch(g, STATIONS["collection"]) for g in frames])
    con = np.array([patch(g, STATIONS["contact"]) for g in frames])
    bg = np.array([patch(g, (0.5, 0.15)) for g in frames])       # the black top band, for reference
    print(f"luma at collection {STATIONS['collection']}: min {col.min():.1f} (frame {int(col.argmin())}), max {col.max():.1f}; "
          f"black top band (0.5, 0.15): max {bg.max():.1f}")
    check(col.min() > 4 * max(bg.max(), 5), "collection point sits on the lit arm in every frame (luma > 4× the black band)")
    print(f"luma at contact {STATIONS['contact']}: min {con.min():.1f}, max {con.max():.1f} (frame {int(con.argmax())}), "
          f"mean {con.mean():.1f}  [reported only]")

    # pt: track independently, compare with k-track mapped through the crop
    print(f"reading {PT.name}")
    pfr = read_grey(PT)
    ph, pw = pfr[0].shape
    check(len(pfr) == 384 and (pw, ph) == (media["pt"]["w"], media["pt"]["h"]), f"{PT.name}: {len(pfr)} frames of {pw}×{ph}")
    cx0, cy0, cw, ch = crops["pt"]
    to_pt = lambda p: ((p[0] - cx0) / cw * pw, (p[1] - cy0) / ch * ph, p[2] / cw * pw)
    m0 = to_pt(pts[0])
    ptw, pth = round(m0[2]), round(m0[2] * aspect)
    px0, py0 = int(round(m0[0] - ptw / 2)), int(round(m0[1] - pth / 2))
    praw = track(pfr, pfr[0][py0:py0 + pth, px0:px0 + ptw], (px0 + ptw / 2, py0 + pth / 2))
    pscore = np.array([r[3] for r in praw])
    parr = circular_mean(np.array([[r[0], r[1]] for r in praw]), 5)
    mapped = np.array([to_pt(p)[:2] for p in pts])
    err = np.hypot(*(parr - mapped).T)
    # frame 0's template was cut from the mapped box, so the comparison is about drift from there on
    print(f"pt independent track: min score {pscore.min():.4f}; |pt track − mapped k-track| max {err.max():.2f} px "
          f"(frame {int(err.argmax())}), mean {err.mean():.2f} px, in a {pw} px wide frame")
    check(err.max() <= 0.01 * pw, "k-track mapped through the pt crop agrees with the pt encode within 1 % of its width")

    print(f"FLAG (SPEC §7 step 4, reported, not a failure): {len(flagged)} frames below {FLAG}, fixed-template min {scores.min():.4f}")

    data = {"fps": 24, "frames": n, "pts": [[float(v) for v in p] for p in pts]}
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    size = OUT.stat().st_size
    print(f"wrote {OUT.relative_to(ROOT)} {size} B")
    check(size <= 15_000, f"k-track.json ≤ 15 KB ({size} B)")

    contact_sheet(frames, pfr, pts, aspect, scores, to_pt)
    if FAIL:
        print(f"\n{len(FAIL)} FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("\nall k-track checks passed")


def contact_sheet(frames, pfr, pts, aspect, scores, to_pt):
    """Every 48th frame: sq full frame + zoom on the box, pt full frame with the mapped box."""
    idx = list(range(0, 384, 48))
    C = 480; ZH = 170; LH = 30
    col = (0, 200, 255)
    tiles = []
    cap_sq = cv2.VideoCapture(str(SQ)); cap_pt = cv2.VideoCapture(str(PT))
    colour_sq, colour_pt = {}, {}
    for i in range(384):
        ok1, a = cap_sq.read(); ok2, b = cap_pt.read()
        if i in idx:
            colour_sq[i], colour_pt[i] = a, b
    cap_sq.release(); cap_pt.release()
    for i in idx:
        f = colour_sq[i].copy()
        x0, y0, x1, y1 = [int(round(v * N)) for v in box_of(pts[i], aspect)]
        cv2.rectangle(f, (x0, y0), (x1, y1), col, 5)
        cx, cy = int(round(pts[i][0] * N)), int(round(pts[i][1] * N))
        cv2.drawMarker(f, (cx, cy), col, cv2.MARKER_CROSS, 40, 4)
        full = cv2.resize(f, (C, C), interpolation=cv2.INTER_AREA)
        # zoom: the box plus 25 % either side, at the tile width
        w = x1 - x0; zx0, zx1 = x0 - w // 4, x1 + w // 4
        zh = int((zx1 - zx0) * ZH / C); zy0 = cy - zh // 2
        zoom = cv2.resize(colour_sq[i][zy0:zy0 + zh, zx0:zx1].copy(), (C, ZH), interpolation=cv2.INTER_AREA)
        k = C / (zx1 - zx0)
        cv2.rectangle(zoom, (int((x0 - zx0) * k), int((y0 - zy0) * k)), (int((x1 - zx0) * k), int((y1 - zy0) * k)), col, 2)
        # pt
        p = pfr[0].shape
        g = colour_pt[i].copy()
        mx, my, mw = to_pt(pts[i]); mh = mw * aspect
        cv2.rectangle(g, (int(mx - mw / 2), int(my - mh / 2)), (int(mx + mw / 2), int(my + mh / 2)), col, 4)
        ptile = cv2.resize(g, (int(C * 1080 / 1920), C), interpolation=cv2.INTER_AREA)
        pcol = np.zeros((C, C, 3), np.uint8); off = (C - ptile.shape[1]) // 2
        pcol[:, off:off + ptile.shape[1]] = ptile
        lab = np.zeros((LH, C, 3), np.uint8)
        cv2.putText(lab, f"frame {i}  score {scores[i]:.3f}  sq / zoom / pt", (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
        tiles.append(np.vstack([lab, full, zoom, pcol]))
    gap = np.full((tiles[0].shape[0], 6, 3), 40, np.uint8)
    rows = []
    for r in range(2):
        row = []
        for t in tiles[r * 4:(r + 1) * 4]:
            row += [t, gap]
        rows.append(np.hstack(row[:-1]))
    sheet = np.vstack([rows[0], np.full((6, rows[0].shape[1], 3), 40, np.uint8), rows[1]])
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(SHEET), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"wrote {SHEET.relative_to(ROOT)} {sheet.shape[1]}×{sheet.shape[0]}, {SHEET.stat().st_size} B")


if __name__ == "__main__":
    main()
