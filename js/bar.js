// TABOUKHI — the tab bar (C4). JS owns every width and every x: labels are measured at
// their heaviest weight, segments are set inline, and no stylesheet rule competes for them
// (glass-tabbar trap 6). The thumb springs between segments, follows a drag 1:1 with rubber
// banding and a velocity stretch, and the bar folds to its current tab on phone inner pages.
import { Spring, PRESET, tween, EASE, clamp, lerp } from './spring.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');
const B = {
  el: null, links: [], thumb: null,
  seg: [],                 // [{ x, w, label }] in bar-local px (x includes the 4px padding)
  cur: -1, width: 0,
  tx: 0, stretch: 1, scale: 1, op: 0,
  spring: null, folded: false, foldTw: null, fold: 0, opTw: null,
  drag: null, suppress: false, inner: false,
};
const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));

// ── Geometry ─────────────────────────────────────────────────────────────────
export function layoutBar() {
  if (!B.el) return;
  const W = document.documentElement.clientWidth;
  const gutter = W < 360 ? 12 : parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--gutter')) || 16;
  B.el.dataset.geo = '1';
  // Measure every label at 600 (the current tab's weight) so a tab never grows past its box.
  const spans = B.links.map((a) => a.firstElementChild);
  B.links.forEach((a) => { a.style.width = 'auto'; a.style.fontWeight = '600'; });
  const labels = spans.map((s) => Math.ceil(Math.max(s.scrollWidth, s.getBoundingClientRect().width)));
  B.links.forEach((a) => a.style.removeProperty('font-weight'));
  // The segments fill the gutter-to-gutter width less the bar's own 4px padding each side, so
  // the bar never runs past its gutters (nor, at 320, past the sheet).
  const avail = Math.min(W - 2 * gutter, 520) - 8;
  const sum = labels.reduce((a, b) => a + b, 0);
  const pad = clamp((avail - sum) / 8, 6, 18);
  let x = 4;
  B.seg = labels.map((label, i) => {
    const w = Math.round(label + 2 * pad);
    B.links[i].style.width = `${w}px`;
    const s = { x, w, label };
    x += w;
    return s;
  });
  B.width = x + 4;
  if (!B.folded) B.el.style.width = `${B.width}px`;
  else applyFold(1);
  if (B.cur >= 0 && !B.drag) { B.spring.set(B.seg[B.cur].x); }
  paintThumb();
}

// The thumb's width follows its position, interpolated between segment centres.
function widthAt(x) {
  const s = B.seg; if (!s.length) return 80;
  if (x <= s[0].x) return s[0].w;
  for (let i = 0; i < s.length - 1; i++) {
    if (x <= s[i + 1].x) return lerp(s[i].w, s[i + 1].w, (x - s[i].x) / (s[i + 1].x - s[i].x));
  }
  return s[s.length - 1].w;
}
function paintThumb() {
  if (!B.thumb) return;
  let x = B.tx, w = widthAt(B.tx);
  if (B.fold > 0 && B.cur >= 0) {
    const fw = B.seg[B.cur].label + 32;
    x = lerp(x, 4, B.fold); w = lerp(w, fw, B.fold);
  }
  const st = B.thumb.style;
  st.width = `${w.toFixed(1)}px`;
  st.transform = `translateX(${x.toFixed(2)}px) scale(${(B.scale * B.stretch).toFixed(4)}, ${B.scale.toFixed(4)})`;
  st.opacity = B.op.toFixed(3);
}

// ── Current tab ──────────────────────────────────────────────────────────────
export function setCurrent(tab, { animate = true } = {}) {
  const i = B.links.findIndex((a) => a.dataset.tab === tab);
  B.links.forEach((a, j) => { if (j === i) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); a.classList.remove('is-hot'); });
  const was = B.cur;
  B.cur = i;
  if (i < 0) {                                     // Home, Notices: no tab is current
    fadeThumb(0);
    if (B.folded) setFold(false);
    return;
  }
  const target = B.seg[i].x;
  if (!animate || RM.matches || B.op < 0.05 || was < 0 && !B.spring.moving && B.op < 0.5) {
    // Appearing, not travelling: placed on its tab and scaled in from .8.
    B.spring.set(target); B.tx = target;
    if (animate && !RM.matches) {
      B.scale = 0.8; B.op = 0;
      B.opTw?.cancel();
      B.opTw = tween({ duration: 380, ease: EASE.spring, onUpdate: (p) => { B.scale = lerp(0.8, 1, p); B.op = Math.min(1, p * 1.6); paintThumb(); } });
    } else { B.opTw?.cancel(); B.scale = 1; B.op = 1; paintThumb(); }
    return;
  }
  B.opTw?.cancel();
  B.op = 1;
  B.spring.to(target);
}
function fadeThumb(to) {
  const from = B.op;
  B.opTw?.cancel();
  if (RM.matches) { B.op = to; paintThumb(); return; }
  B.opTw = tween({ duration: 200, ease: EASE.linear, onUpdate: (p) => { B.op = lerp(from, to, p); paintThumb(); } });
}

// ── Fold (phone, inner pages) ──────────────────────────────────────────────────
function applyFold(p) {
  B.fold = p;
  const i = B.cur;
  if (i < 0) return;
  const s = B.seg[i];
  const fw = s.label + 32 + 8;
  B.el.style.width = `${lerp(B.width, fw, p).toFixed(1)}px`;
  const shift = lerp(0, fw / 2 - (s.x + s.w / 2), p);
  B.links.forEach((a, j) => {
    a.style.transform = shift ? `translateX(${shift.toFixed(2)}px)` : '';
    a.style.opacity = j === i ? '' : (1 - p).toFixed(3);
  });
  paintThumb();
}
export function setFold(on) {
  const phone = document.documentElement.clientWidth < 720;
  if (!phone || !B.inner || B.cur < 0) on = false;
  if (on === B.folded) return;
  B.folded = on;
  B.el.classList.toggle('is-folded', on);
  B.el.classList.toggle('rp', on);
  B.foldTw?.cancel();
  const from = B.fold, to = on ? 1 : 0;
  if (RM.matches) { applyFold(to); if (!on) clearFold(); return; }
  B.foldTw = tween({ duration: 420, ease: EASE.glass, onUpdate: (p) => applyFold(lerp(from, to, p)), onDone: (ok) => { if (ok && !on) clearFold(); } });
}
function clearFold() { B.links.forEach((a) => { a.style.removeProperty('transform'); a.style.removeProperty('opacity'); }); B.el.style.width = `${B.width}px`; B.fold = 0; }

// Recomputed on every navigation (scroll-derived chrome trap).
export function recompute({ inner, state, top = 0 }) {
  B.inner = inner;
  setFold(inner && (state === 'full' || top > 24));
}

// ── Drag ─────────────────────────────────────────────────────────────────────
function localX(e) { return e.clientX - B.el.getBoundingClientRect().left; }
function nearest(cx) {
  let best = 0, bd = Infinity;
  B.seg.forEach((s, i) => { const d = Math.abs(s.x + s.w / 2 - cx); if (d < bd) { bd = d; best = i; } });
  return best;
}
function onDown(e) {
  if (e.button > 0 || B.folded) return;
  B.drag = { id: e.pointerId, x0: e.clientX, on: false, s: [], startCur: B.cur };
}
function onMove(e) {
  const d = B.drag; if (!d || e.pointerId !== d.id) return;
  if (!d.on) {
    if (Math.abs(e.clientX - d.x0) <= 6) return;
    d.on = true;
    try { B.el.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    B.spring.stop();
    if (B.op < 1) { B.opTw?.cancel(); B.op = 1; B.scale = 1; }
  }
  const lx = localX(e);
  const w = widthAt(B.tx);
  let x = lx - w / 2;
  const lo = B.seg[0].x, hi = B.seg[B.seg.length - 1].x;
  if (x < lo) x = lo + (x - lo) * 0.35;
  if (x > hi) x = hi + (x - hi) * 0.35;
  d.s.push({ t: e.timeStamp, x: e.clientX }); if (d.s.length > 6) d.s.shift();
  const a = d.s[0], b = d.s[d.s.length - 1];
  const v = b.t > a.t ? ((b.x - a.x) / (b.t - a.t)) * 1000 : 0;
  B.stretch = 1 + Math.min(Math.abs(v) / 3000, 0.2);
  B.tx = x;
  const hot = nearest(x + widthAt(x) / 2);
  B.links.forEach((l, j) => l.classList.toggle('is-hot', j === hot));
  paintThumb();
}
function onUp(e, cancel) {
  const d = B.drag; if (!d || (e && e.pointerId !== d.id)) return;
  B.drag = null;
  if (!d.on) return;
  B.suppress = true; setTimeout(() => { B.suppress = false; }, 0);
  B.links.forEach((l) => l.classList.remove('is-hot'));
  B.stretch = 1;
  if (cancel) {                                    // put it back, never complete the gesture
    if (B.cur >= 0) { B.spring.x = B.tx; B.spring.to(B.seg[B.cur].x); } else fadeThumb(0);
    return;
  }
  const i = nearest(B.tx + widthAt(B.tx) / 2);
  navigator.vibrate?.(6);
  B.spring.x = B.tx;
  B.spring.to(B.seg[i].x);
  emit('tbk:go', { href: B.links[i].href });
}

export function initBar() {
  B.el = document.querySelector('.bar');
  if (!B.el) return;
  B.links = [...B.el.querySelectorAll('a[data-tab]')];
  B.thumb = B.el.querySelector('.bar__thumb');
  B.spring = new Spring({ ...PRESET.thumb, onUpdate: (x) => { B.tx = x; paintThumb(); } });
  B.cur = B.links.findIndex((a) => a.getAttribute('aria-current') === 'page');
  B.inner = document.body.dataset.route !== 'home';

  B.el.addEventListener('dragstart', (e) => e.preventDefault());
  B.el.addEventListener('pointerdown', onDown);
  B.el.addEventListener('pointermove', onMove);
  B.el.addEventListener('pointerup', (e) => onUp(e, false));
  B.el.addEventListener('pointercancel', (e) => onUp(e, true));
  // Only the bar's own capture counts (the link's implicit touch capture bubbles up here too).
  B.el.addEventListener('lostpointercapture', (e) => { if (e.target === B.el && B.drag && B.drag.on) onUp(e, true); });
  B.el.addEventListener('click', (e) => {
    if (B.suppress) { e.preventDefault(); e.stopPropagation(); B.suppress = false; return; }
    if (B.folded) { e.preventDefault(); e.stopPropagation(); setFold(false); return; }
    // A tap: the thumb leaves at once, while the router fetches (navigation starts now too).
    const a = e.target.closest('a[data-tab]');
    if (a && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) setCurrent(a.dataset.tab);
  });
  B.el.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const i = B.links.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = B.links.length;
    B.links[(i + (e.key === 'ArrowRight' ? 1 : n - 1)) % n].focus();
  });

  layoutBar();
  if (B.cur >= 0) { B.spring.set(B.seg[B.cur].x); B.op = 1; B.scale = 1; paintThumb(); }
  document.fonts?.ready.then(layoutBar);
}

export function barState() {
  return { current: B.cur >= 0 ? B.links[B.cur].dataset.tab : null, folded: B.folded, fold: B.fold, width: B.el ? B.el.getBoundingClientRect().width : 0,
    thumb: B.thumb ? B.thumb.getBoundingClientRect() : null, seg: B.seg.map((s) => ({ ...s })) };
}
export const barEl = () => B.el;
