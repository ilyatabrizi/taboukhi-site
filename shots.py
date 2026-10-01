#!/usr/bin/env python3
"""Screenshots of the TABOUKHI website demo, into shots/.

    python3 shots.py                                            local build on :8261 (starts serve.py if nothing listens)
    python3 shots.py https://ilyatabrizi.github.io/taboukhi-site/
    python3 shots.py --only phone                               one device (phone | desktop)

Phone 390x844 @3x and desktop 1440x900 @2x:
  - every route at rest: home, house, collection, commission, contact, notices, 404, offline
  - Home states: rest, lens dragged, hold-clear
  - phone detents on The house: glance, peek, full
  - both success states: collection, commission

Files are named <device>-<route>[-<state>].png. Uses the same runner, selectors and
waits as e2e.py (system Chrome through Playwright, service workers blocked).
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
import urllib.parse

import e2e
from e2e import SEL, ROUTE_PATH, PREVIEW_OK, Runner, Touch, VIEWPORTS

OUT = e2e.ROOT / "shots"
DEVICES = {"phone": ("390x844", 3), "desktop": ("1440x900", 2)}


async def run(base, live, only):
    from playwright.async_api import async_playwright
    R = Runner(base, live, list(VIEWPORTS), False)
    R.load_media()
    OUT.mkdir(parents=True, exist_ok=True)
    made, failed = [], []

    async def snap(page, name):
        path = OUT / f"{name}.png"
        await page.screenshot(path=str(path))
        made.append(path)
        print(f"  {path.relative_to(e2e.ROOT)}")

    async def step(name, coro):
        try:
            await coro
        except Exception as e:
            failed.append(name)
            print(f"  FAILED {name}: {type(e).__name__}: {(str(e).strip().splitlines() or [''])[0][:200]}")

    async with async_playwright() as p:
        R.browser = await p.chromium.launch(channel="chrome")
        try:
            for dev, (vp, dpr) in DEVICES.items():
                if only and dev != only:
                    continue
                print(f"{dev} {vp} @{dpr}x")
                W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
                touch = VIEWPORTS[vp]["touch"]
                c = await R.ctx(vp, dpr=dpr)
                try:
                    # every route at rest (deep link, settled)
                    for rid, path in ROUTE_PATH.items():
                        async def route_shot(rid=rid, path=path):
                            pg = await R.page(c)
                            await R.load(pg, path, first=True)
                            await snap(pg, f"{dev}-{rid}")
                            await pg.close()
                        await step(f"{dev}-{rid}", route_shot())

                    # Home states
                    pg = await R.page(c)

                    async def home_states():
                        await R.load(pg, "", first=True)
                        info = await R.lens(pg)
                        tx, ty = W * (0.30 if touch else 0.26), H * (0.26 if touch else 0.30)
                        if touch:
                            t = await Touch.of(pg)
                            await t.drag(info["x"], info["y"], tx, ty, steps=14, dt=0.016)
                        else:
                            await e2e.mouse_path(pg, info["x"], info["y"], tx, ty, steps=14)
                        await e2e.stable(pg, [SEL["lens"]], 8, 2500)
                        await snap(pg, f"{dev}-home-lens")
                        # hold-clear: press and hold (the lens position does not matter: the whole screen clears)
                        await e2e.soft_wait(pg, e2e.LENS_CLOSED_JS, 8000)
                        info = await R.lens(pg)
                        where = (W * 0.82, H * 0.2) if touch else (info["x"], info["y"])
                        th = await e2e.hold_start(R, pg, vp, where)
                        await asyncio.sleep(0.5)
                        await e2e.wait_lr(pg, ">=", math.hypot(W, H) - 1, 2000)
                        await snap(pg, f"{dev}-home-hold")
                        await e2e.hold_end(pg, th)
                    await step(f"{dev}-home states", home_states())
                    await pg.close()

                    # phone detents
                    if touch:
                        pg = await R.page(c)

                        async def detents():
                            await R.load(pg, "house/")
                            for want in ("peek", "full", "glance"):
                                ok = await e2e.set_detent(R, pg, want)
                                if not ok:
                                    print(f"  (could not reach {want}; shot shows {(await R.geom(pg))['sheet']['state']})")
                                await snap(pg, f"{dev}-house-{want}")
                        await step(f"{dev}-detents", detents())
                        await pg.close()

                    # success states
                    pg = await R.page(c)

                    async def successes():
                        await R.load(pg, "collection/")
                        await pg.fill(SEL["notify_input"], "name@example.com")
                        await e2e.click_notify(pg)
                        await e2e.wait_text(pg, PREVIEW_OK, 5000)
                        await e2e.soft_wait(pg, e2e.ANIMS_DONE_JS, 2000)
                        await snap(pg, f"{dev}-collection-success")
                        await R.load(pg, "commission/")
                        await e2e.fill_commission(pg)
                        await e2e.submit_commission(pg)
                        await e2e.wait_text(pg, PREVIEW_OK, 5000)
                        await e2e.soft_wait(pg, e2e.ANIMS_DONE_JS, 2000)
                        await snap(pg, f"{dev}-commission-success")
                    await step(f"{dev}-success", successes())
                    await pg.close()
                finally:
                    await c.close()
        finally:
            try:
                await asyncio.wait_for(R.browser.close(), 10)
            except Exception:
                print("  (browser.close() did not return within 10 s; moving on)")
    return made, failed


def main():
    ap = argparse.ArgumentParser(description="TABOUKHI website demo screenshots")
    ap.add_argument("base", nargs="?", default=e2e.DEFAULT_BASE)
    ap.add_argument("--only", choices=list(DEVICES))
    args = ap.parse_args()
    base = args.base.rstrip("/") + "/"
    live = not base.startswith(("http://localhost", "http://127.0.0.1"))
    proc = None if live else e2e.start_server(base)
    if proc:
        print(f"(started serve.py on :{urllib.parse.urlparse(base).port}, pid {proc.pid})")
    t0 = time.time()
    try:
        made, failed = asyncio.run(run(base, live, args.only))
    finally:
        e2e.stop_server(proc)
    print(f"\n{len(made)} screenshots in {OUT}, {len(failed)} failed steps ({time.time() - t0:.0f}s)")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
