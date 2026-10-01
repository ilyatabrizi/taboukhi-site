// TABOUKHI — motion primitives (SPEC §6).
// One semi-implicit Euler integrator at fixed 1/120 s substeps, x'' = −k(x − target) − c·x',
// mass 1. Settles when |x − target| < 0.3 px and |v| < 5 px/s. A shared ticker runs rAF only
// while something is moving; nothing here writes to the DOM.

export const PRESET = {
  thumb: { k: 380, c: 34 },
  detent: { k: 380, c: 34 },
  lens: { k: 220, c: 26 },
};

// ── The ticker: one rAF loop, alive only while a job is registered. ─────────────
const jobs = new Set();
let raf = 0;
let last = 0;
function frame(now) {
  raf = 0;
  const dt = last ? Math.min((now - last) / 1000, 1 / 20) : 1 / 60;
  last = now;
  for (const job of [...jobs]) {
    try { if (job(dt, now) === false) jobs.delete(job); } catch (err) { jobs.delete(job); console.error(err); }
  }
  if (jobs.size) raf = requestAnimationFrame(frame); else last = 0;
}
export function tick(job) {
  jobs.add(job);
  if (!raf) raf = requestAnimationFrame(frame);
  return () => jobs.delete(job);
}

// ── A 1D spring. ────────────────────────────────────────────────────────────────
const STEP = 1 / 120;
export class Spring {
  constructor({ value = 0, k = 380, c = 34, onUpdate = () => {}, onRest = () => {} } = {}) {
    this.x = value; this.v = 0; this.target = value;
    this.k = k; this.c = c; this.onUpdate = onUpdate; this.onRest = onRest;
    this.acc = 0; this.stopTick = null;
  }
  get moving() { return !!this.stopTick; }
  set(x) { this.stop(); this.x = x; this.v = 0; this.target = x; this.onUpdate(x); }
  to(target, velocity) {
    this.target = target;
    if (velocity !== undefined) this.v = velocity;
    if (!this.stopTick) this.stopTick = tick((dt) => this.step(dt));
  }
  stop() { if (this.stopTick) { this.stopTick(); this.stopTick = null; } this.acc = 0; }
  step(dt) {
    this.acc += dt;
    while (this.acc >= STEP) {
      const a = -this.k * (this.x - this.target) - this.c * this.v;
      this.v += a * STEP;        // semi-implicit: velocity first,
      this.x += this.v * STEP;   // then position with the new velocity
      this.acc -= STEP;
    }
    if (Math.abs(this.x - this.target) < 0.3 && Math.abs(this.v) < 5) {
      this.x = this.target; this.v = 0; this.stopTick = null; this.acc = 0;
      this.onUpdate(this.x); this.onRest(this.x);
      return false;
    }
    this.onUpdate(this.x);
    return true;
  }
}

// ── Cubic-bezier easing, solved the way browsers do. ────────────────────────────
export function bezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 8; i++) {
      const e = sx(t) - x;
      if (Math.abs(e) < 1e-5) return sy(t);
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= e / d;
    }
    let lo = 0, hi = 1; t = x;
    while (lo < hi) {
      const v = sx(t);
      if (Math.abs(v - x) < 1e-5) break;
      if (x > v) lo = t; else hi = t;
      t = (lo + hi) / 2;
      if (hi - lo < 1e-6) break;
    }
    return sy(t);
  };
}
export const EASE = {
  glass: bezier(0.22, 1, 0.36, 1),
  spring: bezier(0.32, 1.25, 0.5, 1),
  expo: bezier(0.4, 0, 0, 1),
  sweep: bezier(0.45, 0, 0.6, 1),       // the lens hole opening across the screen in a morph
  linear: (x) => x,
};
export const CSS_EASE = {
  glass: 'cubic-bezier(.22,1,.36,1)',
  spring: 'cubic-bezier(.32,1.25,.5,1)',
  expo: 'cubic-bezier(.4,0,0,1)',
};

// ── A tween on the shared ticker. Resolves when done or cancelled. ──────────────
export function tween({ duration, delay = 0, ease = EASE.glass, onUpdate, onDone }) {
  let t0 = 0, dead = false, resolve;
  const done = new Promise((r) => { resolve = r; });
  const finish = (complete) => {
    if (dead) return; dead = true; stop();
    if (complete) onUpdate?.(1);
    onDone?.(complete); resolve(complete);
  };
  const stop = tick((dt, now) => {
    if (dead) return false;
    if (!t0) t0 = now;
    const p = duration <= 0 ? 1 : Math.min(1, Math.max(0, (now - t0 - delay) / duration));
    if (now - t0 >= delay) onUpdate?.(ease(p));
    if (p >= 1) { finish(true); return false; }
    return true;
  });
  return { done, cancel: () => finish(false), finish: () => finish(true) };
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Release velocity (px/s) along `key` of a pointer-sample list, anchored at the RELEASE time
// tUp, not at the last move: a finger that stopped before lifting throws nothing. Only the
// samples of the last 80 ms count, the last one must be within 50 ms of the release, and the
// result is clamped to ±4000 px/s.
export function releaseVelocity(s, tUp, key) {
  const last = s[s.length - 1];
  if (!last || tUp - last.t > 50) return 0;
  const w = s.filter((p) => p.t >= tUp - 80);
  if (w.length < 2) return 0;
  const dt = (w[w.length - 1].t - w[0].t) / 1000;
  if (dt < 0.008) return 0;
  return clamp((w[w.length - 1][key] - w[0][key]) / dt, -4000, 4000);
}
export const lerp = (a, b, t) => a + (b - a) * t;
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Commit a WAAPI animation's end state inline and cancel it, so no fill-mode animation is
// ever left alive on glass or its ancestors (A4.1).
export function settle(anim, finalStyles, el) {
  return anim.finished.then(() => {
    try { anim.commitStyles(); } catch { /* not rendered: ignore */ }
    if (finalStyles && el) for (const [k, v] of Object.entries(finalStyles)) {
      if (k.startsWith('--')) el.style.setProperty(k, v); else el.style[k] = v;
    }
    anim.cancel();
    return true;
  }, () => false);
}
