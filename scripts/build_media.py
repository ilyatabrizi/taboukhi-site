#!/usr/bin/env python3
"""Every derived film file for the TABOUKHI site, from the one supplied shot (SPEC §7).

    python3 scripts/build_media.py                 # sq pt ambient json track
    python3 scripts/build_media.py pt track        # any subset, in that order

Steps, all writing to assets/media/:
  sq       film-sq-1920.mp4/.webp   the hiring site's 1920² loop, copied byte for byte (not re-encoded),
                                    then the old film-1920.* / film-1280.* copies are removed
  pt       film-pt-1080.mp4/.webp   portrait: master 2880² cropped x 682–2140, y 288–2880 (1458×2592),
                                    INTER_AREA to 1080×1920 BEFORE the flow step, then the same eased
                                    16 s out-and-back loop; the poster is frame 0 decoded from the final file
  ambient  film-ambient-320.mp4/.webp  film-sq-1920's frames at 320², GaussianBlur σ 9 (tier 2 backdrop)
  json     media.json               per-encode crop in master-frame fractions
  track    runs scripts/k_track.py  → k-track.json + shots/k-track.jpg (asserts, fails the build)

Every MP4 we encode goes through `avconvert --preset PresetPassthrough` (moov first), must pass
mp4probe.check() (moov before mdat, one video track, no audio), and must survive an AVFoundation
transcode (`--preset PresetAppleM4VWiFi`) — the proof that Safari's media stack reads it.

The loop method is copied from taboukhi/scripts/build_film.py (that repo is read-only reference and is
not imported, so this repo stands alone). The only change is that read/flow/between take any w×h
instead of a square; for a square they compute exactly what the original does:
  SEG = 90 source frames (0–3.75 s), HALF = 192 output frames per direction,
  pos(i) = SEG·(1 − cos(πi/HALF))/2, flow = DIS medium at half the output size,
  sequence = half + half[-2:0:-1] = 384 frames, 16 s at 24 fps.

No ffmpeg on this Mac: OpenCV's bundled FFmpeg encodes (libx264, crf 23), avconvert remuxes.
Set TBK_SCRATCH to choose where temporary encodes and the AVFoundation check file go.
"""
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

sys.dont_write_bytecode = True                      # no scripts/__pycache__ from importing mp4probe
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mp4probe  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEDIA = ROOT / "assets/media"
SRC = MEDIA / "source/film-original.mp4"
HIRING = ROOT.parent / "taboukhi/assets/media"          # read-only fallback for the sq copy
SCRATCH = pathlib.Path(os.environ.get("TBK_SCRATCH") or tempfile.gettempdir())

MASTER = 2880
SEG = 90             # source frames used (0..90 of 121): the slower first 3.75 s
HALF = 192           # output frames per direction: 8 s at 24 fps, 16 s round trip
FPS = 24
FRAMES = 2 * HALF    # 384

# Portrait window in master pixels: 9:16, full height below the black top 10%, engraving centred.
PT_X0, PT_X1, PT_Y0, PT_Y1 = 682, 2140, 288, 2880     # 1458 × 2592
PT_W, PT_H = 1080, 1920
AMB = 320
AMB_SIGMA = 9

BUDGET = {                                              # bytes, decimal KB/MB (the stricter reading)
    "film-pt-1080.mp4": 1_800_000, "film-pt-1080.webp": 70_000,
    "film-ambient-320.mp4": 120_000, "film-ambient-320.webp": 8_000,
}


def media_json():
    f = lambda v: round(v, 5)
    return {
        "sq": {"w": 1920, "h": 1920, "crop": [0, 0, 1, 1]},
        "pt": {"w": PT_W, "h": PT_H, "crop": [f(PT_X0 / MASTER), f(PT_Y0 / MASTER),
                                              f((PT_X1 - PT_X0) / MASTER), f((PT_Y1 - PT_Y0) / MASTER)]},
        "fps": FPS, "frames": FRAMES,
    }


# ---- the loop (from taboukhi/scripts/build_film.py, generalised to w×h) ----------------------------

def read_source(prep):
    """Source frames 0..SEG, each passed through prep() as it is read (keeps 8 GB machines happy)."""
    cap = cv2.VideoCapture(str(SRC))
    frames = []
    while len(frames) <= SEG:
        ok, f = cap.read()
        if not ok:
            break
        assert f.shape[:2] == (MASTER, MASTER), f.shape
        frames.append(prep(f))
    cap.release()
    assert len(frames) == SEG + 1, len(frames)
    return frames


def flows(frames, fw, fh):
    """Forward flow k -> k+1 for every pair, at fw×fh."""
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    grey = [cv2.cvtColor(cv2.resize(f, (fw, fh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY) for f in frames]
    return [dis.calc(grey[k], grey[k + 1], None) for k in range(SEG)]


_grid = {}


def between(a, b, flow, t):
    """Frame at fraction t between a and b: pull a back and b forward along the flow."""
    if t < 1e-3:
        return a
    if t > 1 - 1e-3:
        return b
    h, w = a.shape[:2]
    fh, fw = flow.shape[:2]
    f = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR)
    f[..., 0] *= w / fw
    f[..., 1] *= h / fh
    if (w, h) not in _grid:
        _grid[(w, h)] = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    gx, gy = _grid[(w, h)]
    wa = cv2.remap(a, gx - t * f[..., 0], gy - t * f[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    wb = cv2.remap(b, gx + (1 - t) * f[..., 0], gy + (1 - t) * f[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.addWeighted(wa, 1 - t, wb, t, 0)


def positions():
    """Source position of each outbound output frame, eased at both ends (1 − cos)."""
    return [SEG * (1 - math.cos(math.pi * i / HALF)) / 2 for i in range(HALF + 1)]


def loop_index(j):
    """Output frame j of 384 → outbound index: 0..192 out, then 191..1 back (turns not doubled)."""
    return j if j <= HALF else FRAMES - j


# ---- encode / remux / verify ------------------------------------------------------------------------

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAILED ({r.returncode}): {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def encode(frame_iter, size, dest):
    """cv2 'avc1' → temp → avconvert PresetPassthrough → dest. Returns frame count written."""
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="tbk-enc-", dir=SCRATCH))
    tmp = tmpdir / "enc.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"avc1"), FPS, size)
    assert vw.isOpened(), "cv2.VideoWriter(avc1) did not open"
    n = 0
    for f in frame_iter:
        assert (f.shape[1], f.shape[0]) == size, f.shape
        vw.write(f)
        n += 1
    vw.release()
    print(f"    cv2 avc1 wrote {n} frames, {tmp.stat().st_size} B")
    r = subprocess.run(["avconvert", "--preset", "PresetPassthrough", "--source", str(tmp),
                        "--output", str(dest), "--replace"], capture_output=True, text=True)
    print(f"    avconvert PresetPassthrough exit {r.returncode}")
    if r.returncode != 0:
        raise SystemExit(r.stdout + r.stderr)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return n


def verify_mp4(path, size, frames=FRAMES):
    ok, info, problems = mp4probe.check(str(path))
    print("    mp4probe:", mp4probe.line(str(path), info), "PASS" if ok else "FAIL " + "; ".join(problems))
    assert ok, problems
    assert info.get("samples") == frames, info.get("samples")
    assert abs(info.get("fps", 0) - FPS) < 0.01, info.get("fps")     # from stts; cv2's CAP_PROP_FPS = frames/duration
    check = SCRATCH / "check.m4v"
    r = subprocess.run(["avconvert", "--preset", "PresetAppleM4VWiFi", "--source", str(path),
                        "--output", str(check), "--replace"], capture_output=True, text=True)
    print(f"    AVFoundation read (PresetAppleM4VWiFi → {check.name}) exit {r.returncode}")
    assert r.returncode == 0, r.stdout + r.stderr
    check.unlink(missing_ok=True)
    cap = cv2.VideoCapture(str(path))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = 0
    while cap.grab():
        n += 1
    cap.release()
    print(f"    decoded back: {w}×{h}, {n} frames")
    assert (w, h) == size and n == frames, (w, h, n)
    return info


def first_frame(path):
    cap = cv2.VideoCapture(str(path))
    ok, f = cap.read()
    cap.release()
    assert ok, path
    return f


def budget(path):
    b = BUDGET.get(path.name)
    size = path.stat().st_size
    print(f"    {path.name}: {size} B ({size / 1000:.1f} KB)" + (f"  budget {b} B  {'OK' if size <= b else 'OVER'}" if b else ""))
    if b:
        assert size <= b, f"{path.name} {size} B over budget {b} B"
    return size


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


# ---- steps ------------------------------------------------------------------------------------------

def step_sq():
    print("sq: film-sq-1920.* = the hiring site's film-1920.*, byte for byte")
    for ext in ("mp4", "webp"):
        dest = MEDIA / f"film-sq-1920.{ext}"
        src = MEDIA / f"film-1920.{ext}"
        if not src.exists():
            src = HIRING / f"film-1920.{ext}"
        if src.exists():
            shutil.copyfile(src, dest)
            assert md5(src) == md5(dest)
            print(f"    copied {src.relative_to(ROOT.parent)} → {dest.name} (md5 {md5(dest)})")
        else:
            assert dest.exists(), f"no source for {dest.name}"
            print(f"    {dest.name} already present (md5 {md5(dest)})")
    verify_mp4(MEDIA / "film-sq-1920.mp4", (1920, 1920))
    webp = cv2.imread(str(MEDIA / "film-sq-1920.webp"))
    assert webp is not None and webp.shape[:2] == (1920, 1920), None if webp is None else webp.shape
    budget(MEDIA / "film-sq-1920.mp4"); budget(MEDIA / "film-sq-1920.webp")
    # Only now that the new names exist and check out: remove the old copies.
    for name in ("film-1920.mp4", "film-1920.webp", "film-1280.mp4", "film-1280.webp"):
        p = MEDIA / name
        if p.exists():
            p.unlink()
            print(f"    removed old {name}")


def step_pt():
    print(f"pt: master x {PT_X0}–{PT_X1}, y {PT_Y0}–{PT_Y1} → {PT_W}×{PT_H}, eased loop")
    t0 = time.time()
    crop = lambda f: cv2.resize(f[PT_Y0:PT_Y1, PT_X0:PT_X1], (PT_W, PT_H), interpolation=cv2.INTER_AREA)
    frames = read_source(crop)
    print(f"    read + cropped {len(frames)} source frames ({time.time() - t0:.1f} s)")
    fl = flows(frames, PT_W // 2, PT_H // 2)
    print(f"    {len(fl)} DIS flows at {PT_W // 2}×{PT_H // 2} ({time.time() - t0:.1f} s)")
    pos = positions()
    print(f"    peak speed {max(b - a for a, b in zip(pos, pos[1:])):.3f} source frames per output frame")

    def gen():
        for j in range(FRAMES):
            p = pos[loop_index(j)]
            k = min(int(p), SEG - 1)
            yield between(frames[k], frames[k + 1], fl[k], p - k)

    dest = MEDIA / "film-pt-1080.mp4"
    n = encode(gen(), (PT_W, PT_H), dest)
    assert n == FRAMES, n
    print(f"    encoded ({time.time() - t0:.1f} s)")
    verify_mp4(dest, (PT_W, PT_H))
    budget(dest)
    poster = MEDIA / "film-pt-1080.webp"
    cv2.imwrite(str(poster), first_frame(dest), [cv2.IMWRITE_WEBP_QUALITY, 80])
    budget(poster)


def step_ambient():
    print(f"ambient: film-sq-1920 frames → {AMB}², GaussianBlur σ {AMB_SIGMA}")
    src = MEDIA / "film-sq-1920.mp4"

    def gen():
        cap = cv2.VideoCapture(str(src))
        while True:
            ok, f = cap.read()
            if not ok:
                break
            yield cv2.GaussianBlur(cv2.resize(f, (AMB, AMB), interpolation=cv2.INTER_AREA), (0, 0), AMB_SIGMA)
        cap.release()

    dest = MEDIA / "film-ambient-320.mp4"
    n = encode(gen(), (AMB, AMB), dest)
    assert n == FRAMES, n
    verify_mp4(dest, (AMB, AMB))
    budget(dest)
    poster = MEDIA / "film-ambient-320.webp"
    cv2.imwrite(str(poster), first_frame(dest), [cv2.IMWRITE_WEBP_QUALITY, 80])
    budget(poster)


def step_json():
    m = media_json()
    (MEDIA / "media.json").write_text(json.dumps(m, separators=(",", ":")) + "\n")
    print("json: media.json", json.dumps(m, separators=(",", ":")))


def step_track():
    print("track: scripts/k_track.py")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/k_track.py")])
    if r.returncode != 0:
        raise SystemExit(f"k_track.py failed (exit {r.returncode})")


STEPS = {"sq": step_sq, "pt": step_pt, "ambient": step_ambient, "json": step_json, "track": step_track}


def main():
    sys.stdout.reconfigure(line_buffering=True)     # keep our lines in order with k_track.py's
    SCRATCH.mkdir(parents=True, exist_ok=True)
    want = sys.argv[1:] or list(STEPS)
    for w in want:
        assert w in STEPS, f"unknown step {w}; choose from {list(STEPS)}"
    for name in STEPS:              # always in dependency order
        if name in want:
            STEPS[name]()
    print("done:", ", ".join(want))


if __name__ == "__main__":
    main()
