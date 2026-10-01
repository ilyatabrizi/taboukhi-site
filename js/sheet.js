// TABOUKHI — the sheet (C5): the frost on Home, a panel (≥720px) or a bottom sheet with
// detents (phone) everywhere else, and the morphs between them.
// Every WAAPI animation here is committed and cancelled when it ends (A4.1): nothing with a
// fill mode is ever left on .sheet.
import { geo } from './geometry.js';
import { Spring, PRESET, CSS_EASE, EASE, clamp, wait, releaseVelocity } from './spring.js';
import * as lens from './lens.js';
import { currentTier } from './tier.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');
const DETENTS = ['full', 'peek', 'glance'];

export const S = {
  el: null, inner: null, scroll: null, grab: null, ghost: null, main: null,
  state: 'home', y: 0, H: 0, det: { full: 0, peek: 0, glance: 0 },
  spring: null, drag: null, hand: null, suppressClick: false,
  run: null,          // the running choreography: { finish() }
  need: null,         // phone: { peek, glance } visible heights the page's own content asks for
};

const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
export const isPhone = () => geo.W < 720;
export const isDetent = (s = S.state) => DETENTS.includes(s);

// ── Geometry of the target boxes ──────────────────────────────────────────────
// Peek and Glance show what SPEC §5 lists for each page ([data-peek] and the title) whole:
// the spec heights are the minimum, grown to fit the content (capped at 70% / 50% of the
// screen), so a small phone never cuts the title or the one filled button.
export function measureDetents() {
  const r = S.ghost.getBoundingClientRect();
  S.H = r.height;
  let specPeek = parseFloat(getComputedStyle(S.ghost).getPropertyValue('--peek-h'));
  if (!specPeek) specPeek = clamp(0.46 * innerHeight, 300, 420);
  const peekVis = Math.min(Math.max(specPeek, S.need?.peek || 0), 0.70 * innerHeight);
  const glanceVis = Math.min(Math.max(148, S.need?.glance || 0), 0.5 * innerHeight);
  S.det = { full: 0, peek: Math.max(0, S.H - peekVis), glance: Math.max(0, S.H - glanceVis) };
  return S.det;
}
// Lay a copy of a page's content out in the ghost (the sheet's box, never painted) and read how
// much of it Peek and Glance must show. Phone only; the copy is removed at once.
export function measureNeed(mainEl) {
  if (!S.ghost || !mainEl || !isPhone()) { S.need = null; return S.need; }
  const box = document.createElement('div');
  box.className = 'sheet-measure';
  box.setAttribute('aria-hidden', 'true');
  const copy = mainEl.cloneNode(true);
  box.append(...copy.childNodes);
  S.ghost.append(box);
  const top = box.getBoundingClientRect().top;
  const bottom = (el) => (el ? el.getBoundingClientRect().bottom - top : 0);
  const pk = box.querySelector('[data-peek]'), h1 = box.querySelector('h1');
  S.need = pk || h1 ? { peek: pk ? bottom(pk) + 44 + 40 : 0, glance: h1 ? bottom(h1) + 44 + 40 : 0 } : null;
  box.remove();
  return S.need;
}
// The inner-page box for the current layout, as it will stand on screen (with the Peek
// offset on the phone).
export function targetRect(detent = 'peek') {
  const r = S.ghost.getBoundingClientRect();
  const off = isPhone() ? (measureDetents(), S.det[detent]) : 0;
  return { x: r.left, y: r.top + off, w: r.width, h: r.height };
}
// The free area a station centres its point in (SPEC §4 C1).
export function freeArea() {
  const cap = document.querySelector('.capsule').getBoundingClientRect();
  if (isPhone()) {
    const t = targetRect('peek');
    return { x0: 0, x1: geo.W, y0: cap.bottom + 12, y1: t.y - 12 };
  }
  const t = targetRect();
  const top = parseFloat(getComputedStyle(S.ghost).top) || 24;
  return { x0: 0, x1: t.x - 24, y0: top + 44, y1: geo.H - 104 };
}

// ── State ────────────────────────────────────────────────────────────────────
function writeY(y) {
  S.y = y;
  S.el.style.transform = `translateY(${y.toFixed(2)}px)`;
  S.el.style.setProperty('--off', `${Math.max(0, y).toFixed(1)}px`);
}
function clearBox() {
  S.el.style.removeProperty('transform');
  S.el.style.removeProperty('clip-path');
  for (const p of ['--mx', '--my', '--mw', '--mh', '--rk', '--off', ...Object.keys(FULL)]) S.el.style.removeProperty(p);
  for (const p of ['left', 'top', 'width', 'height', 'right', 'bottom']) S.inner.style.removeProperty(p);
}
export function setState(s) {
  S.state = s;
  S.el.dataset.state = s;
  S.grab.setAttribute('aria-expanded', s === 'full' ? 'true' : 'false');
  emit('tbk:sheet', { state: s });
}
// Place the sheet for an inner page, instantly (first load, resize, reduced motion).
export function placeInner(detent = 'peek') {
  S.spring.stop();
  clearBox();
  if (isPhone()) { measureNeed(S.main); setState(detent); measureDetents(); writeY(S.det[detent]); }
  else setState('panel');
}
export function placeHome() {
  S.spring.stop();
  clearBox();
  setState('home');
}

export function detentTo(name, v = 0) {
  if (!isPhone() || !isDetent()) return;
  measureDetents();
  setState(name);
  const target = S.det[name];
  if (RM.matches) { S.spring.set(target); return; }
  S.spring.x = S.y;
  S.spring.to(target, v);
  if (name !== 'full' && S.scroll.scrollTop > 0) S.scroll.scrollTo({ top: 0, behavior: RM.matches ? 'auto' : 'smooth' });
}

function rubber(off) {
  const lo = S.det.full, hi = S.det.glance;
  if (off < lo) return lo + (off - lo) * 0.35;
  if (off > hi) return hi + (off - hi) * 0.35;
  return off;
}
function release(v) {
  const proj = S.y + v * 0.2;
  let best = 'peek', bd = Infinity;
  for (const d of DETENTS) { const dd = Math.abs(proj - S.det[d]); if (dd < bd) { bd = dd; best = d; } }
  navigator.vibrate?.(6);
  detentTo(best, v);
}

// ── Drag: the whole sheet at Glance/Peek, the grabber strip at Full ─────────────
function onDown(e) {
  if (!isPhone() || !isDetent() || e.button > 0 || S.run) return;
  if (S.state === 'full' && !e.target.closest('.sheet__grab')) return;
  if (e.target.closest('.ph-chip')) return;
  measureDetents();
  S.drag = { id: e.pointerId, y0: e.clientY, off0: S.y, on: false, s: [{ t: e.timeStamp, y: e.clientY }] };
}
function onMove(e) {
  const d = S.drag; if (!d || e.pointerId !== d.id) return;
  const dy = e.clientY - d.y0;
  if (!d.on) {
    if (Math.abs(dy) <= 6) return;
    d.on = true; d.y0 = e.clientY; d.off0 = S.y;
    S.spring.stop();
    try { S.el.setPointerCapture(e.pointerId); } catch { /* ignore */ }
  }
  writeY(rubber(d.off0 + (e.clientY - d.y0)));
  d.s.push({ t: e.timeStamp, y: e.clientY }); if (d.s.length > 8) d.s.shift();
}
function onUp(e, cancel) {
  const d = S.drag; if (!d || (e && e.pointerId !== d.id)) return;
  S.drag = null;
  if (!d.on) return;
  S.suppressClick = true; setTimeout(() => { S.suppressClick = false; }, 0);
  release(cancel || !e ? 0 : releaseVelocity(d.s, e.timeStamp, 'y'));
}
// The handoff: at Full with the content at its top, a downward drag moves the sheet.
function onTouchStart(e) {
  if (!isPhone() || S.state !== 'full' || e.touches.length !== 1 || S.run) { S.hand = null; return; }
  S.hand = { y: e.touches[0].clientY, top: S.scroll.scrollTop <= 0, decided: false, on: false, s: [] };
}
function onTouchMove(e) {
  const h = S.hand; if (!h) return;
  const y = e.touches[0].clientY, dy = y - h.y;
  if (!h.decided) {
    h.decided = true;
    if (!(h.top && dy > 0)) { S.hand = null; return; }
    h.on = true; h.off0 = S.y; measureDetents(); S.spring.stop();
  }
  if (!h.on) return;
  e.preventDefault();
  writeY(rubber(h.off0 + dy));
  h.s.push({ t: e.timeStamp, y }); if (h.s.length > 8) h.s.shift();
}
function onTouchEnd(e) {
  const h = S.hand; S.hand = null;
  if (h && h.on) release(h.s.length > 1 && e.type === 'touchend' ? releaseVelocity(h.s, e.timeStamp, 'y') : 0);
}

// ── Morphs ────────────────────────────────────────────────────────────────────
const anims = new Set();
function run(el, frames, opts) {
  const a = el.animate(frames, opts);
  anims.add(a);
  a.finished.then(() => anims.delete(a), () => anims.delete(a));
  return a;
}
function stopAnims() { for (const a of anims) { try { a.cancel(); } catch { /* ignore */ } } anims.clear(); }
// The Home copy is absolutely placed: animating #page would make it the copy's containing block
// (a transform does), so the copy would jump for the length of the fade. Animate .home itself.
const fadeTarget = () => S.main.querySelector(':scope > .home') || S.main;
// Fades out from wherever the content stands now (an interrupted morph leaves it at 0).
function contentOut(dy = 0, ms = 160) {
  if (!S.main.firstElementChild) return Promise.resolve();
  const o0 = +getComputedStyle(S.main).opacity;
  if (o0 === 0) { S.main.style.opacity = '0'; return Promise.resolve(); }
  const move = dy && !RM.matches;
  const a = run(fadeTarget(), move ? [{ opacity: o0, transform: 'none' }, { opacity: 0, transform: `translateY(${dy}px)` }] : [{ opacity: o0 }, { opacity: 0 }],
    { duration: RM.matches ? Math.min(ms, 160) : ms, easing: 'linear' });
  return a.finished.then(() => { S.main.style.opacity = '0'; a.cancel(); }, () => {});
}
function contentIn(dy = 24, ms = 360) {
  S.main.style.opacity = '';
  const frames = RM.matches || !dy
    ? [{ opacity: 0 }, { opacity: 1 }]
    : [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: 'none' }];
  const a = run(fadeTarget(), frames, { duration: RM.matches ? 160 : ms, easing: CSS_EASE.glass });
  return a.finished.then(() => a.cancel(), () => {});
}
function placeInnerAt(r) {
  const st = S.inner.style;
  st.left = `${r.x}px`; st.top = `${r.y}px`; st.width = `${r.w}px`; st.height = `${r.h}px`;
}
function maskVars(r) {
  return { '--mx': `${r.x.toFixed(1)}px`, '--my': `${r.y.toFixed(1)}px`, '--mw': `${r.w.toFixed(1)}px`, '--mh': `${r.h.toFixed(1)}px` };
}
function maskRect(r) {
  for (const [k, v] of Object.entries(maskVars(r))) S.el.style.setProperty(k, v);
}
// The clip as the registered lengths the morph state's clip-path reads (see tokens.css).
const FULL = { '--ct': '0px', '--cr': '0px', '--cb': '0px', '--cl': '0px' };
function insetOf(r) {
  const t = r.y, l = r.x, rr = geo.W - (r.x + r.w), b = Math.max(geo.H - (r.y + r.h), -40);
  return { '--ct': `${t.toFixed(1)}px`, '--cr': `${rr.toFixed(1)}px`, '--cb': `${b.toFixed(1)}px`, '--cl': `${l.toFixed(1)}px` };
}
function setClip(v) { for (const [k, val] of Object.entries(v)) S.el.style.setProperty(k, val); }
// A new navigation interrupts whatever is running: it is frozen, not finished. The content
// stays out (opacity 0) and the lens keeps its current opening, so the next choreography
// starts from what is on screen instead of popping to the old one's end state first.
export function hurry() { if (S.run) { const r = S.run; S.run = null; r.finish(true); } }
function guard() {
  let dead = false, endFn = null;
  const ctx = { get dead() { return dead; }, end(fn) { endFn = fn; } };
  S.run = { finish(intr = true) { dead = true; stopAnims(); endFn?.(intr); } };
  return ctx;
}
// Tier 2 (and reduced motion): no masked morph. WebKit before 18 ignores a mask on a
// backdrop-filtered element, so the gathering frost would show as one hard box.
const plainMorph = () => RM.matches || currentTier() === '2';
function fadeSheetIn() {
  if (RM.matches) return;
  const a = S.el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: 'linear' });
  a.finished.then(() => a.cancel(), () => {});
}

// Home → inner: the frost gathers into the sheet while the lens opens to cover.
export async function morphToInner(apply) {
  hurry();
  const detent = 'peek';
  const R = targetRect(detent);
  if (plainMorph()) {
    await contentOut(0, 160);
    apply(); lens.deactivate(); placeInner(detent); S.scroll.scrollTop = 0;
    fadeSheetIn();
    await contentIn(0, 160);
    return;
  }
  const ctx = guard();
  let swapped = false;
  const swap = () => { if (swapped) return; swapped = true; apply(); placeInnerAt(R); S.scroll.scrollTop = 0; };
  const end = (intr) => {
    swap();
    lens.deactivate();
    placeInner(detent);
    S.main.style.opacity = intr ? '0' : '';
    S.run = null;
  };
  ctx.end(end);

  contentOut(0, 160);
  S.el.style.removeProperty('transform');
  setState('morph');
  maskRect(R);
  // Inline = the END value; the animation covers the way there (fill 'backwards' only covers
  // the pending frame — never 'both'/'forwards' on glass).
  S.el.style.setProperty('--rk', 'rgba(0,0,0,1)');
  if (isPhone()) S.el.style.setProperty('--off', `${S.det[detent].toFixed(1)}px`);
  setClip(insetOf(R));
  run(S.el, [{ '--rk': 'rgba(0,0,0,0)' }, { '--rk': 'rgba(0,0,0,1)' }], { duration: 120, easing: 'linear', fill: 'backwards' });
  const clip = run(S.el, [FULL, insetOf(R)], { duration: 640, easing: CSS_EASE.glass, fill: 'backwards' });
  // The hole sweeps (in-out) rather than bursting open, so the frost is visibly eaten from
  // the lens outward while the clip gathers it in from the edges.
  lens.openTo(1, 640, { ease: EASE.sweep });
  lens.rimTo(0, 200);

  await wait(320); if (ctx.dead) return;
  swap();
  contentIn(24, 360);
  await clip.finished.catch(() => {}); if (ctx.dead) return;
  end();
}

// Inner → Home: the reverse. The box goes full, the clip expands, the hole closes.
export async function morphToHome(apply) {
  hurry();
  const cur = S.el.getBoundingClientRect();
  const R = { x: cur.left, y: cur.top, w: cur.width, h: cur.height };
  if (plainMorph()) {
    await contentOut(0, 160);
    apply(); placeHome(); lens.activate({ open: 0, rim: 1 });
    fadeSheetIn();
    await contentIn(0, 160);
    return;
  }
  const ctx = guard();
  let swapped = false;
  const swap = () => {
    if (swapped) return; swapped = true;
    // The box goes full-screen before the Home copy arrives, so what measures it on arrival
    // (the copy-lift, the lens clamp) sees where it will stand.
    for (const p of ['left', 'top', 'width', 'height']) S.inner.style.removeProperty(p);
    apply();
    lens.refreshCopy();
  };
  const end = (intr) => {
    swap();
    placeHome();
    S.el.classList.remove('to-home');
    S.main.style.opacity = intr ? '0' : '';
    if (!intr) { lens.setOpen(0); lens.rimTo(1, 0); }
    S.run = null;
    emit('tbk:home-ready');
  };
  ctx.end(end);

  S.spring.stop();
  contentOut(0, 160);
  S.el.style.removeProperty('transform');
  placeInnerAt(R);
  maskRect(R);
  S.el.style.setProperty('--rk', 'rgba(0,0,0,0)');
  if (isPhone()) S.el.style.setProperty('--off', `${Math.max(0, S.y).toFixed(1)}px`);
  setClip(FULL);
  setState('morph');
  S.el.classList.add('to-home');                 // the wash settles toward onyx during the morph, not after it
  lens.activate({ open: 1, rim: 0 });
  // The sheet spreads back out: the opaque rect grows with the clip, so the frost is seen
  // leaving the panel's box rather than appearing from nowhere once the hole closes.
  const full = { x: 0, y: 0, w: geo.W, h: geo.H };
  maskRect(full);
  run(S.el, [insetOf(R), FULL], { duration: 560, easing: CSS_EASE.glass, fill: 'backwards' });
  run(S.el, [maskVars(R), maskVars(full)], { duration: 560, easing: CSS_EASE.glass, fill: 'backwards' });
  run(S.el, [{ '--rk': 'rgba(0,0,0,1)' }, { '--rk': 'rgba(0,0,0,0)' }], { duration: 700, delay: 200, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'backwards' });
  lens.openTo(0, 900, { delay: 200 });
  lens.rimTo(1, 400, 700);

  await wait(160); if (ctx.dead) return;
  swap();
  await wait(540); if (ctx.dead) return;
  contentIn(12, 360);
  await wait(1100 - 700); if (ctx.dead) return;
  end();
}

// Inner → inner: the sheet stays put; the content goes out and comes back in.
export async function swapInner(apply) {
  hurry();
  const ctx = guard();
  let swapped = false;
  const swap = () => {
    if (swapped) return; swapped = true;
    apply();
    S.scroll.scrollTop = 0;
    // Always: the new page may ask for a different Peek height than the last one.
    if (isPhone()) { measureNeed(S.main); detentTo('peek'); }
  };
  ctx.end((intr) => { swap(); S.main.style.opacity = intr ? '0' : ''; S.run = null; });
  await contentOut(RM.matches ? 0 : -24, 160); if (ctx.dead) return;
  swap();
  await contentIn(RM.matches ? 0 : 24, 360); if (ctx.dead) return;
  S.run = null;
}

// ── Init ─────────────────────────────────────────────────────────────────────
export function initSheet() {
  S.el = document.querySelector('.sheet');
  if (!S.el) return false;
  S.inner = S.el.querySelector('.sheet__in');
  S.scroll = S.el.querySelector('.sheet__scroll');
  S.grab = S.el.querySelector('.sheet__grab');
  S.ghost = document.querySelector('.sheet-ghost');
  S.main = document.getElementById('page');
  S.state = S.el.dataset.state;
  S.spring = new Spring({ ...PRESET.detent, onUpdate: writeY });

  S.el.addEventListener('pointerdown', onDown);
  S.el.addEventListener('pointermove', onMove);
  S.el.addEventListener('pointerup', (e) => onUp(e, false));
  S.el.addEventListener('pointercancel', (e) => onUp(e, true));
  // lostpointercapture bubbles: a touch's implicit capture on the grabber is lost the moment
  // the sheet takes it, and that must not read as the sheet losing its own.
  S.el.addEventListener('lostpointercapture', (e) => { if (e.target === S.el && S.drag && S.drag.on) onUp(e, true); });
  S.el.addEventListener('click', (e) => { if (S.suppressClick) { e.preventDefault(); e.stopPropagation(); S.suppressClick = false; } }, true);
  S.scroll.addEventListener('touchstart', onTouchStart, { passive: true });
  S.scroll.addEventListener('touchmove', onTouchMove, { passive: false });
  S.scroll.addEventListener('touchend', onTouchEnd, { passive: true });
  S.scroll.addEventListener('touchcancel', onTouchEnd, { passive: true });

  S.grab.addEventListener('click', () => { if (isDetent()) detentTo(S.state === 'full' ? 'peek' : 'full'); });
  S.el.addEventListener('focusin', (e) => {
    if (isPhone() && (S.state === 'peek' || S.state === 'glance') && e.target.matches('input, textarea, select')) detentTo('full');
  });

  let lastTop = 0;
  S.scroll.addEventListener('scroll', () => {
    const top = S.scroll.scrollTop, dy = top - lastTop; lastTop = top;
    S.scroll.classList.toggle('is-scrolled', top > 0);
    emit('tbk:scroll', { top, dy });
  }, { passive: true });

  // A1: on desktop inner pages a wheel over the film scrolls the sheet's content.
  addEventListener('wheel', (e) => {
    if (e.ctrlKey || S.state !== 'panel' || S.el.contains(e.target)) return;
    const k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? S.scroll.clientHeight : 1;
    S.scroll.scrollBy({ top: e.deltaY * k, left: 0, behavior: 'auto' });
  }, { passive: true });
  return true;
}
