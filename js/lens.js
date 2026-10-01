// TABOUKHI — the lens (C6). Home only.
// A clear round hole in the frost that rests on the engraved name and follows it through
// the film (k-track, one read per presented video frame), and can be dragged, thrown,
// tapped to a new spot, held open to clear the whole screen, or moved from the keyboard.
// Writes only --lx/--ly/--lr on .sheet (and on the film in tier 2) and transform, size and
// opacity on .lens. Never on :root.
import { geo, toScreen, widthToScreen, engravingAt, layoutKind } from './geometry.js';
import { film, camera } from './film.js';
import { Spring, PRESET, tween, tick, EASE, clamp, lerp, releaseVelocity } from './spring.js';
import { session } from './prefs.js';
import { currentTier } from './tier.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');
const FINE = matchMedia('(hover: hover) and (pointer: fine)');
const COARSE = matchMedia('(pointer: coarse)');
const BOUNDS = { phone: [104, 136], tablet: [120, 170], desktop: [132, 248] };
const HOLD_MS = 400, HOLD_SLOP = 8;

const L = {
  sheet: null, el: null, handle: null, hint: null,
  active: false, t: 0,
  x: 0, y: 0, r: 0, rb: 0,
  mode: 'track',                 // track | follow | park | drag | throw | placed | return
  open: 1, pick: 0, rim: 0,
  ret: null, drag: null, press: null, held: false,
  copy: null, travelled: 0,
  idle: 0, openTw: null, rimTw: null, pickTw: null, retStop: null,
  sx: null, sy: null,
  rvfc: 0, rafStop: null,
  cam: null,                     // tier 2: the camera box [w, h], measured once per layout
  rimEls: null, rimC: null, rimHx: [],   // touch: the capsule and the bar (and their centres), whose rim light leans to the lens
};

function bounds() { return BOUNDS[layoutKind()]; }

// The engraving on screen now: centre, width and the rest radius it asks for.
export function engravingScreen() {
  const [fx, fy, fw] = engravingAt(L.t);
  const p = camera.apply(toScreen(fx, fy));
  const w = widthToScreen(fw) * camera.now().z;
  const [lo, hi] = bounds();
  return { x: p[0], y: p[1], w, r: clamp(0.56 * w, lo, hi) };
}

// ── The copy clamp: the disc never intersects a text box on the frost. ───────────
export function refreshCopy() {
  const els = [...document.querySelectorAll('.home__line, .home__hint')];
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const el of els) {
    const range = document.createRange();
    range.selectNodeContents(el);
    for (const rc of range.getClientRects()) {
      if (!rc.width || !rc.height) continue;
      l = Math.min(l, rc.left); t = Math.min(t, rc.top); r = Math.max(r, rc.right); b = Math.max(b, rc.bottom);
    }
  }
  L.copy = Number.isFinite(l) ? { l, t, r, b } : null;
}
function outside(x, y, m) {
  const c = L.copy; if (!c) return true;
  const qx = clamp(x, c.l, c.r), qy = clamp(y, c.t, c.b);
  return Math.hypot(x - qx, y - qy) >= m - 0.01;
}
function clampCentre(x, y, rad) {
  const W = geo.W, H = geo.H;
  x = clamp(x, 0, W); y = clamp(y, 0, H);
  const c = L.copy, m = rad + 8;
  if (!c || outside(x, y, m)) return [x, y];
  // Minkowski push: radially off the rounded rect when outside it, else out through each edge;
  // take the nearest candidate that stays on screen.
  const qx = clamp(x, c.l, c.r), qy = clamp(y, c.t, c.b);
  const d = Math.hypot(x - qx, y - qy);
  const cands = [];
  if (d > 0.01) cands.push([qx + ((x - qx) / d) * m, qy + ((y - qy) / d) * m]);
  cands.push([c.l - m, y], [c.r + m, y], [x, c.t - m], [x, c.b + m]);
  let best = null, bd = Infinity;
  for (const [cx, cy] of cands) {
    if (cx < 0 || cx > W || cy < 0 || cy > H || !outside(cx, cy, m)) continue;
    const dd = Math.hypot(cx - x, cy - y);
    if (dd < bd) { bd = dd; best = [cx, cy]; }
  }
  return best || [x, y];
}

// ── Paint ───────────────────────────────────────────────────────────────────
function paint() {
  if (!L.active) return;
  const E = engravingScreen();
  let x, y;
  switch (L.mode) {
    case 'track': x = E.x; y = E.y; break;
    case 'return': {
      const p = EASE.glass(clamp((performance.now() - L.ret.t0) / 1200, 0, 1));
      x = lerp(L.ret.x, E.x, p); y = lerp(L.ret.y, E.y, p);
      if (p >= 1) setMode('track');
      break;
    }
    case 'drag': x = L.drag.x; y = L.drag.y; break;
    default: x = L.sx.x; y = L.sy.x;
  }
  const rb = lerp(E.r, bounds()[0], L.pick);
  [x, y] = clampCentre(x, y, rb);
  if (L.mode === 'follow' || L.mode === 'drag') L.travelled += Math.hypot(x - L.x, y - L.y);
  const R = Math.hypot(geo.W, geo.H);
  const r = rb + (R - rb) * L.open;
  L.x = x; L.y = y; L.r = r; L.rb = rb;
  const s = L.sheet.style;
  s.setProperty('--lx', `${x.toFixed(1)}px`);
  s.setProperty('--ly', `${y.toFixed(1)}px`);
  s.setProperty('--lr', `${r.toFixed(1)}px`);
  const ls = L.el.style;
  ls.transform = `translate3d(${(x - r).toFixed(1)}px, ${(y - r).toFixed(1)}px, 0)`;
  ls.width = ls.height = `${(2 * r).toFixed(1)}px`;
  ls.opacity = L.rim.toFixed(3);
  if (currentTier() === '2') paintFilmClip(x, y, r);
  paintRims(x, y);
  if (L.travelled > 240) dismissHint();
}
// On a touch screen there is no pointer for the chrome's rim light to lean toward, so it leans
// toward the clear window instead: the light stays on the upper-left side (0–28% / 0–22%, the
// pointer light's own range) and slides toward the lens. Two elements, no new surface.
function paintRims(x, y) {
  if (!COARSE.matches || RM.matches) return;
  if (!L.rimEls) L.rimEls = [document.querySelector('.capsule'), document.querySelector('.bar')].filter(Boolean);
  // Both are fixed chrome: their centres are read once per layout, not every frame.
  if (!L.rimC) L.rimC = L.rimEls.map((el) => { const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; });
  L.rimEls.forEach((el, i) => {
    const dx = x - L.rimC[i][0], dy = y - L.rimC[i][1], d = Math.hypot(dx, dy) || 1;
    const hx = 14 + 14 * (dx / d), hy = 11 + 11 * (dy / d);
    const prev = L.rimHx[i];
    if (prev && Math.abs(prev[0] - hx) < 0.5 && Math.abs(prev[1] - hy) < 0.5) return;
    L.rimHx[i] = [hx, hy];
    el.style.setProperty('--hx', `${hx.toFixed(1)}%`);
    el.style.setProperty('--hy', `${hy.toFixed(1)}%`);
  });
}
function parkRims() {
  if (!L.rimEls) return;
  L.rimEls.forEach((el) => { el.style.removeProperty('--hx'); el.style.removeProperty('--hy'); });
  L.rimHx = [];
}
// Tier 2: the sharp film is clipped to the lens, in the film's own (camera-local) coordinates.
function paintFilmClip(x, y, r) {
  const c = camera.now();
  const cx = geo.W / 2, cy = geo.H / 2;
  if (!L.cam) L.cam = [film.cam.offsetWidth, film.cam.offsetHeight];
  const ox = (geo.W - L.cam[0]) / 2, oy = (geo.H - L.cam[1]) / 2;
  const lx = cx + (x - cx - c.tx) / c.z - ox, ly = cy + (y - cy - c.ty) / c.z - oy, lr = r / c.z;
  for (const el of [film.video, film.still]) {
    el.style.setProperty('--lx', `${lx.toFixed(1)}px`);
    el.style.setProperty('--ly', `${ly.toFixed(1)}px`);
    el.style.setProperty('--lr', `${lr.toFixed(1)}px`);
  }
}

// rAF runs only while something moves; tracking runs on the video's own frame clock.
let animating = 0;
function animate(on) {
  animating += on ? 1 : -1;
  if (animating < 0) animating = 0;
  if (animating && !L.rafStop) L.rafStop = tick(() => { if (!animating || !L.active) { L.rafStop = null; return false; } syncTime(); paint(); return true; });
}
function syncTime() { if (!('requestVideoFrameCallback' in HTMLVideoElement.prototype) || film.video.paused) L.t = film.video.currentTime || 0; }
function startTracking() {
  const v = film.video;
  if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
    const cb = (now, md) => { if (!L.active) { L.rvfc = 0; return; } L.t = md.mediaTime; if (!animating) paint(); L.rvfc = v.requestVideoFrameCallback(cb); };
    if (!L.rvfc) L.rvfc = v.requestVideoFrameCallback(cb);
  } else {
    animate(true);                                     // rAF fallback for tracking
    L.fallbackTrack = true;
  }
}
function stopTracking() {
  if (L.rvfc) { try { film.video.cancelVideoFrameCallback(L.rvfc); } catch { /* ignore */ } L.rvfc = 0; }
  if (L.fallbackTrack) { L.fallbackTrack = false; animate(false); }
}

// ── Modes ───────────────────────────────────────────────────────────────────
function setMode(m) {
  const was = L.mode;
  if (was === 'return' && m !== 'return' && L.retStop) { L.retStop(); L.retStop = null; }
  L.mode = m;
  const picked = m === 'follow' || m === 'drag' || m === 'throw' || m === 'placed' || m === 'park';
  pickTo(picked ? 1 : 0);
  if (m === 'return') {
    L.ret = { x: L.x, y: L.y, t0: performance.now() };
    if (RM.matches) { L.mode = 'track'; paint(); return; }
    let on = true; animate(true);
    L.retStop = () => { if (on) { on = false; animate(false); } };
    setTimeout(() => { if (L.retStop) { L.retStop(); L.retStop = null; } }, 1250);
  }
}
function pickTo(v) {
  if (L.pick === v && !L.pickTw) return;
  L.pickTw?.cancel();
  if (RM.matches) { L.pick = v; paint(); return; }
  const from = L.pick; animate(true);
  L.pickTw = tween({ duration: 280, onUpdate: (p) => { L.pick = lerp(from, v, p); }, onDone: () => { L.pickTw = null; animate(false); } });
}
function seedSprings() { L.sx.set(L.x); L.sy.set(L.y); }
// Velocity is passed only by a throw; a follow or a tap keeps the spring's own momentum.
function springTo(x, y, vx, vy) {
  [x, y] = clampCentre(x, y, bounds()[0]);
  if (RM.matches) { L.sx.set(x); L.sy.set(y); paint(); return; }
  L.sx.to(x, vx); L.sy.to(y, vy);
  if (!L.springWatch) {
    animate(true);
    L.springWatch = tick(() => {
      if (L.sx.moving || L.sy.moving) return true;
      L.springWatch = null; animate(false);
      if (L.mode === 'throw') L.mode = 'placed';
      paint();
      return false;
    });
  }
}
function idleIn(ms) {
  clearTimeout(L.idle);
  L.idle = setTimeout(() => { if (L.active && L.mode !== 'drag' && !L.held) setMode('return'); }, ms);
}
// After a touch lets go: off the metal (more than two radii from the engraving) the lens is a
// dark disc on the frost, so it glides back sooner; on the arm it stays put for 6 s.
function idleAfterTouch() {
  const E = engravingScreen();
  idleIn(Math.hypot(L.x - E.x, L.y - E.y) > 2 * L.rb ? 2200 : 6000);
}
export function returnHome() { if (L.active && L.mode !== 'track') setMode('return'); }

// ── Open (0 = rest, 1 = the whole screen clear) and the rim ────────────────────
export function setOpen(v) { L.openTw?.cancel(); L.openTw = null; L.open = v; paint(); }
export function openTo(v, duration, { delay = 0, ease = EASE.glass } = {}) {
  L.openTw?.cancel();
  if (RM.matches || !duration) { L.open = v; paint(); return Promise.resolve(true); }
  const from = L.open; animate(true);
  const tw = tween({ duration, delay, ease, onUpdate: (p) => { L.open = lerp(from, v, p); }, onDone: () => { if (L.openTw === tw) L.openTw = null; animate(false); } });
  L.openTw = tw;
  return tw.done;
}
export function rimTo(v, duration, delay = 0) {
  L.rimTw?.cancel();
  if (RM.matches || !duration) { L.rim = v; paint(); return Promise.resolve(true); }
  const from = L.rim; animate(true);
  const tw = tween({ duration, delay, ease: EASE.linear, onUpdate: (p) => { L.rim = lerp(from, v, p); }, onDone: () => { if (L.rimTw === tw) L.rimTw = null; animate(false); } });
  L.rimTw = tw;
  return tw.done;
}

function holdOpen() { L.held = true; dismissHint(); openTo(1, RM.matches ? 0 : 420); }
function holdClose() { if (!L.held) return; L.held = false; openTo(0, RM.matches ? 0 : 520); }

// ── The hint: gone after the first move or tap, for the rest of the session. ────
function dismissHint() {
  if (!L.hint || L.hint.dataset.gone) return;
  L.hint.dataset.gone = '1';
  session.set('tbk.hint', '1');
  const a = L.hint.animate([{ opacity: 1 }, { opacity: 0 }], { duration: RM.matches ? 120 : 240, easing: 'linear' });
  a.finished.then(() => { L.hint.style.opacity = '0'; a.cancel(); }, () => {});
}

// ── Input ───────────────────────────────────────────────────────────────────
function onHandleDown(e) {
  if (!L.active || e.button > 0) return;
  e.preventDefault();
  L.handle.setPointerCapture(e.pointerId);
  clearTimeout(L.idle);
  L.springsWere = L.mode;
  L.drag = { id: e.pointerId, type: e.pointerType, sx: e.clientX, sy: e.clientY, ox: L.x - e.clientX, oy: L.y - e.clientY,
    x: L.x, y: L.y, moved: false, s: [{ t: e.timeStamp, x: e.clientX, y: e.clientY }] };
  L.press = setTimeout(() => { if (L.drag && !L.drag.moved) holdOpen(); }, HOLD_MS);
  L.el.classList.add('is-grab');
}
function onHandleMove(e) {
  const d = L.drag; if (!d || e.pointerId !== d.id) return;
  if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > HOLD_SLOP) {
    d.moved = true; clearTimeout(L.press);
    if (!L.held) { L.sx.stop(); L.sy.stop(); setMode('drag'); dismissHint(); }
  }
  if (!d.moved || L.held) return;
  d.x = e.clientX + d.ox; d.y = e.clientY + d.oy;
  d.s.push({ t: e.timeStamp, x: e.clientX, y: e.clientY });
  if (d.s.length > 8) d.s.shift();
  paint();
}
function onHandleUp(e, cancelled) {
  const d = L.drag; if (!d || (e && e.pointerId !== d.id)) return;
  clearTimeout(L.press);
  L.drag = null;
  L.el.classList.remove('is-grab');
  if (L.held) { holdClose(); if (L.mode === 'drag') { seedSprings(); setMode('placed'); } idleIn(d.type === 'mouse' ? 5000 : 6000); return; }
  if (!d.moved) return;                                  // a tap on the lens: nothing to do
  seedSprings();
  if (cancelled) { setMode('placed'); if (d.type === 'mouse') idleIn(6000); else idleAfterTouch(); return; }
  if (d.type === 'mouse' && FINE.matches) { setMode('follow'); idleIn(5000); return; }
  const vx = releaseVelocity(d.s, e.timeStamp, 'x'), vy = releaseVelocity(d.s, e.timeStamp, 'y');
  setMode('throw');
  springTo(L.x + vx * 0.2, L.y + vy * 0.2, vx, vy);
  if (d.type === 'mouse') { idleIn(6000); return; }
  // The throw's landing point decides the wait, not where the finger let go.
  const [tx, ty] = [L.sx.target, L.sy.target], E = engravingScreen();
  idleIn(Math.hypot(tx - E.x, ty - E.y) > 2 * L.rb ? 2200 : 6000);
}

function onFrostDown(e) {
  if (!L.active || e.button > 0) return;
  if (e.target.closest('a, button, input, textarea, label')) return;
  L.pressInfo = { id: e.pointerId, x: e.clientX, y: e.clientY, t: e.timeStamp, onCopy: !!e.target.closest('.home'), type: e.pointerType };
  clearTimeout(L.press);
  L.press = setTimeout(() => { if (L.pressInfo) { L.pressInfo.held = true; holdOpen(); } }, HOLD_MS);
}
function onFrostMove(e) {
  const p = L.pressInfo; if (!p || e.pointerId !== p.id) return;
  if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > HOLD_SLOP) { clearTimeout(L.press); if (!p.held) L.pressInfo = null; }
}
function onFrostUp(e) {
  const p = L.pressInfo; if (!p || e.pointerId !== p.id) return;
  clearTimeout(L.press);
  L.pressInfo = null;
  if (p.held) { holdClose(); return; }
  if (p.onCopy || (p.type === 'mouse' && FINE.matches)) return;
  // A tap on empty frost springs the lens there.
  if (L.mode === 'track' || L.mode === 'return') seedSprings();
  setMode('placed'); dismissHint();
  springTo(e.clientX, e.clientY);
  const E = engravingScreen();
  idleIn(Math.hypot(L.sx.target - E.x, L.sy.target - E.y) > 2 * L.rb ? 2200 : 6000);
}

function onMouseMove(e) {
  if (!L.active || e.pointerType !== 'mouse' || !FINE.matches || L.drag || L.held) return;
  const over = e.target.closest && e.target.closest('.capsule, .bar, .home, .skip');
  if (over) {
    if (L.mode === 'follow') { L.sx.to(L.sx.x); L.sy.to(L.sy.x); L.mode = 'park'; }
    idleIn(5000);
    return;
  }
  if (L.mode === 'track' || L.mode === 'return') seedSprings();
  if (L.mode !== 'follow') setMode('follow');
  springTo(e.clientX, e.clientY);
  idleIn(5000);
}

function onKey(e) {
  if (!L.active) return;
  const k = e.key;
  if (k === ' ' || k === 'Spacebar') {
    e.preventDefault();
    if (e.type === 'keydown' && !e.repeat) holdOpen();
    if (e.type === 'keyup') holdClose();
    return;
  }
  if (e.type !== 'keydown') return;
  if (k === 'Escape') { e.preventDefault(); returnHome(); return; }
  const step = e.shiftKey ? 96 : 24;
  const dx = k === 'ArrowLeft' ? -step : k === 'ArrowRight' ? step : 0;
  const dy = k === 'ArrowUp' ? -step : k === 'ArrowDown' ? step : 0;
  if (!dx && !dy) return;
  e.preventDefault();
  if (L.mode === 'track' || L.mode === 'return' || L.mode === 'follow' || L.mode === 'park') seedSprings();
  setMode('placed'); dismissHint();
  springTo(L.sx.target + dx, L.sy.target + dy);
  idleIn(6000);
}

// ── Lifecycle ────────────────────────────────────────────────────────────────
export function initLens() {
  L.sheet = document.querySelector('.sheet');
  L.el = document.querySelector('.lens');
  if (!L.sheet || !L.el) return false;
  L.handle = L.el.querySelector('.lens__handle');
  // The pair paints through one watcher job (springTo), not from each spring.
  L.sx = new Spring({ ...PRESET.lens });
  L.sy = new Spring({ ...PRESET.lens });

  L.handle.addEventListener('pointerdown', onHandleDown);
  L.handle.addEventListener('pointermove', onHandleMove);
  L.handle.addEventListener('pointerup', (e) => onHandleUp(e, false));
  L.handle.addEventListener('pointercancel', (e) => onHandleUp(e, true));
  L.handle.addEventListener('lostpointercapture', (e) => { if (L.drag) onHandleUp(e, true); });
  L.handle.addEventListener('keydown', onKey);
  L.handle.addEventListener('keyup', onKey);
  L.handle.addEventListener('blur', holdClose);
  L.sheet.addEventListener('pointerdown', onFrostDown);
  addEventListener('pointermove', onFrostMove, { passive: true });
  addEventListener('pointerup', onFrostUp);
  addEventListener('pointercancel', (e) => { if (L.pressInfo && e.pointerId === L.pressInfo.id) { clearTimeout(L.press); L.pressInfo = null; holdClose(); } });
  addEventListener('pointermove', onMouseMove, { passive: true });
  document.addEventListener('mouseout', (e) => { if (!e.relatedTarget && L.active && FINE.matches && L.mode !== 'track' && !L.drag) setMode('return'); });
  for (const el of [L.sheet, L.el]) el.addEventListener('contextmenu', (e) => { if (L.active) e.preventDefault(); });
  film.video.addEventListener('seeked', () => { L.t = film.video.currentTime; paint(); });
  return true;
}

export function activate({ open = 0, rim = 1, mode = 'track' } = {}) {
  L.active = true;
  L.el.hidden = false;
  L.hint = document.getElementById('hint');
  if (L.hint && session.get('tbk.hint') === '1') { L.hint.dataset.gone = '1'; L.hint.style.opacity = '0'; }
  L.open = open; L.rim = rim; L.pick = 0; L.mode = mode; L.travelled = 0;
  L.t = film.video.currentTime || 0;
  L.cam = null; L.rimC = null;
  refreshCopy();
  startTracking();
  paint();
}
export function deactivate() {
  L.active = false;
  stopTracking();
  L.openTw?.cancel(); L.rimTw?.cancel(); L.pickTw?.cancel();
  L.sx?.stop(); L.sy?.stop();
  if (L.springWatch) { L.springWatch(); L.springWatch = null; }
  if (L.retStop) { L.retStop(); L.retStop = null; }
  animating = 0;
  clearTimeout(L.idle); clearTimeout(L.press);
  L.drag = null; L.pressInfo = null; L.held = false;
  L.el.hidden = true;
  L.el.style.opacity = '0';
  parkRims();
  for (const el of [film.video, film.still, L.sheet]) { el.style.removeProperty('--lx'); el.style.removeProperty('--ly'); el.style.removeProperty('--lr'); }
}
export function relayout() { L.cam = null; L.rimC = null; L.rimHx = []; if (L.active) { refreshCopy(); paint(); } }
export function lensState() {
  return { active: L.active, x: L.x, y: L.y, r: L.r, rest: L.rb, mode: L.mode, open: L.open, rim: L.rim, held: L.held, t: L.t };
}
export const lensPaint = paint;
