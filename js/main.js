// TABOUKHI — the website demo. Entry module.
// One film of the engraved temple arm, one sheet of glass in front of it, two capsules.
import { initTier, probe, currentTier } from './tier.js';
import { initFilm, film, camera } from './film.js';
import { initGeometry, loadGeometry, measure, geo } from './geometry.js';
import * as lens from './lens.js';
import * as sheet from './sheet.js';
import { initBar, layoutBar, setCurrent, recompute, setFold, barState, barEl } from './bar.js';
import { initRouter, stationFor, routerBusy, whenSettled, currentStation, prefetchAll } from './router.js';
import { initForms } from './forms.js';
import { initToast } from './toast.js';
import { initSW } from './sw-register.js';
import { session } from './prefs.js';
import { EASE, CSS_EASE, wait, tween } from './spring.js';

const html = document.documentElement;
// The inline script gives up on JS after 4 s; a slow module load that gets here later still wins.
html.classList.remove('no-js'); html.classList.add('js');
const RM = matchMedia('(prefers-reduced-motion: reduce)');
const T0 = performance.now();
let readyResolve;
const ready = new Promise((r) => { readyResolve = r; });

// The public hooks for the e2e suite (SPEC §9). Read-only views of live state.
window.__tbk = {
  ready: false,
  whenReady: () => ready,
  whenSettled,
  get route() { return document.body.dataset.route; },
  get tier() { return currentTier(); },
  get encode() { return geo.enc; },
  lens: () => lens.lensState(),
  engraving: () => lens.engravingScreen(),
  sheet: () => ({ state: sheet.S.state, y: sheet.S.y, detents: { ...sheet.S.det }, busy: !!sheet.S.run,
    rect: sheet.S.el ? sheet.S.el.getBoundingClientRect().toJSON() : null, target: sheet.S.ghost ? sheet.targetRect().valueOf() : null }),
  station: () => { const s = stationFor(currentStation()); return { name: currentStation(), ...s, camera: camera.now() }; },
  bar: () => barState(),
  busy: () => routerBusy(),
};

// iOS applies :active only when the document listens for touches.
document.addEventListener('touchstart', () => {}, { passive: true });

// ── The veil: onyx until the still is decoded, then the film. ──────────────────
const veil = document.querySelector('.veil');
const returning = session.get('tbk.seen') === '1';
session.set('tbk.seen', '1');
let veilGone;
const veilDone = new Promise((r) => { veilGone = r; });
function liftVeil() {
  if (!veil || veil.dataset.out) return;
  veil.dataset.out = '1';
  const a = veil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: RM.matches ? 160 : returning ? 300 : 900, easing: 'linear' });
  a.finished.then(() => { veil.remove(); veilGone(); }, () => { veil.remove(); veilGone(); });
}
setTimeout(() => { if (veil && veil.isConnected) { veil.remove(); veilGone(); } }, 2500);   // no matter what

// ── Layout classes the stylesheet cannot derive: bar vs copy (Home), bar vs panel. ──
function layoutClasses() {
  const bar = barEl();
  if (!bar) return;
  const br = bar.getBoundingClientRect();
  const W = html.clientWidth;
  const barLeft = (W - br.width) / 2, barRight = barLeft + br.width;
  html.classList.remove('copy-lift');
  const home = document.querySelector('.home');
  if (home && W >= 720) {
    const hr = home.getBoundingClientRect();
    html.classList.toggle('copy-lift', barLeft < hr.left + Math.min(hr.width, 440) + 24);
  }
  if (sheet.S.ghost && W >= 720) {
    html.classList.remove('bar-under');
    const g = sheet.S.ghost.getBoundingClientRect();
    // The panel keeps its full height unless the bar would come within 12px of it.
    html.classList.toggle('bar-under', barRight > g.left - 12);
  } else html.classList.remove('bar-under');
}

// ── Resize: re-measure, re-place, re-solve (never animated). ──────────────────
// Listening from the start of boot: a rotation during the intro or a morph is not lost, it
// is replayed once the choreography has settled.
let rz = 0, lastPhone = null, pend = false, lensReady = false;
function onResize() {
  cancelAnimationFrame(rz);
  rz = requestAnimationFrame(() => {
    layoutBar();
    if (sheet.S.el && (sheet.S.run || routerBusy())) {
      if (!pend) { pend = true; whenSettled().then(() => { pend = false; onResize(); }); }
      return;
    }
    measure();
    html.dataset.layout = geo.W < 720 ? 'phone' : 'panel';
    layoutBar();
    layoutClasses();
    const route = document.body.dataset.route;
    const phone = geo.W < 720;
    if (sheet.S.el && sheet.S.ghost) {
      if (route === 'home') { if (lensReady) lens.relayout(); }
      else {
        if (phone !== lastPhone || !phone) {
          const want = phone ? (sheet.isDetent() ? sheet.S.state : 'peek') : 'panel';
          sheet.placeInner(want);
          if (phone !== lastPhone && want !== 'full') sheet.S.scroll.scrollTop = 0;
        } else { sheet.measureNeed(sheet.S.main); sheet.measureDetents(); sheet.detentTo(sheet.S.state); }
        camera.set(stationFor(document.body.dataset.station));
        layoutClasses();
      }
      recompute({ inner: route !== 'home', state: sheet.S.state, top: sheet.S.scroll.scrollTop });
    }
    lastPhone = phone;
  });
}

// ── First load on Home: the frost closes in from the edges and stops on the name. ──
// The choreography yields: a navigation away from Home ends it (the new page's bar is shown
// at once), and so does the first touch (the line and the bar arrive within 200 ms while the
// frost close carries on from where it is; a hold still clears the glass).
async function introHome() {
  html.classList.add('intro', 'intro-bar');
  const safety = setTimeout(() => html.classList.remove('intro', 'intro-bar'), 6500);
  const line = document.querySelector('.home__line');
  const hint = document.getElementById('hint');
  const bar = barEl();
  let over = false, leaving = false, closing = false;
  const fade = (el, frames, ms, easing = 'linear') => {
    if (!el || RM.matches) return;
    const a = el.animate(frames, { duration: ms, easing });
    a.finished.then(() => a.cancel(), () => {});
  };
  const onNav = () => { leaving = true; };
  const onApplied = () => { if (document.body.dataset.route !== 'home') finish('route'); };
  const onFirstTouch = () => { if (html.classList.contains('intro') || html.classList.contains('intro-bar')) finish('touch'); };
  function finish(why) {
    if (over) return;
    over = true;
    clearTimeout(safety);
    removeEventListener('pointerdown', onFirstTouch, true);
    document.removeEventListener('tbk:nav', onNav);
    document.removeEventListener('tbk:applied', onApplied);
    const hadLine = html.classList.contains('intro'), hadBar = html.classList.contains('intro-bar');
    html.classList.remove('intro', 'intro-bar');
    if (hadBar) fade(bar, [{ opacity: 0 }, { opacity: 1 }], 200);
    if (why !== 'touch') return;                         // 'route': the Home copy has already gone
    // The bar appears under a finger that could not see it: it stays inert until that touch
    // (and the click it becomes) is over, so a tap on the hidden bar never navigates.
    if (hadBar) {
      html.classList.add('intro-inert');
      const up = () => setTimeout(() => html.classList.remove('intro-inert'), 350);
      addEventListener('pointerup', up, { once: true, capture: true });
      addEventListener('pointercancel', up, { once: true, capture: true });
    }
    if (!closing) { closing = true; lens.openTo(0, RM.matches ? 0 : 1400); lens.rimTo(1, RM.matches ? 0 : 400); }
    if (hadLine) fade(line, [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], 200, CSS_EASE.glass);
    lens.refreshCopy();
    if (hint && !hint.dataset.gone && (hadLine || hint.style.opacity === '0')) {
      hint.style.opacity = '';
      fade(hint, [{ opacity: 0 }, { opacity: 1 }], 200);
    }
  }
  const stop = () => over || leaving || (document.body.dataset.route !== 'home' && (finish('route'), true));
  document.addEventListener('tbk:nav', onNav);
  document.addEventListener('tbk:applied', onApplied);

  if (RM.matches) {
    // The close is replaced by a 200 ms fade of the frost.
    lens.activate({ open: 0, rim: 1 });
    closing = true;
    await veilDone;
    if (stop()) return;
    const a = sheet.S.el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: 'linear' });
    a.finished.then(() => a.cancel(), () => {});
    finish('done');
    return;
  }

  lens.activate({ open: 1, rim: 0 });                    // the film sharp over the whole screen
  addEventListener('pointerdown', onFirstTouch, { capture: true, passive: true });
  const playing = new Promise((r) => {
    if (film.playing) r(); else { document.addEventListener('tbk:playing', r, { once: true }); setTimeout(r, 2500); }
  });
  await Promise.all([veilDone, playing]);
  if (stop()) return;
  await wait(400);
  if (stop()) return;
  closing = true;
  lens.openTo(0, 1400);
  lens.rimTo(1, 400, 1000);
  await wait(1200);
  if (stop()) return;
  // The one line rises beneath it.
  html.classList.remove('intro');
  if (hint) hint.style.opacity = '0';
  const la = line.animate([{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: CSS_EASE.glass });
  await la.finished.catch(() => {}); la.cancel();
  if (stop()) return;
  lens.refreshCopy();
  // The bar fades in (WAAPI on the glass itself, then cancelled: no fill left behind).
  html.classList.remove('intro-bar');
  const ba = bar.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 420, easing: 'linear' });
  ba.finished.then(() => ba.cancel(), () => {});
  clearTimeout(safety);
  // The hint after 800 ms (unless it has been seen this session).
  await wait(800);
  if (stop()) return;
  finish('done');
  if (hint && !hint.dataset.gone) {
    const ha = hint.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 360, easing: 'linear' });
    ha.finished.then(() => { if (!hint.dataset.gone) hint.style.opacity = ''; ha.cancel(); }, () => {});
  }
}

async function boot() {
  const root = new URL(document.querySelector('meta[name="tbk-root"]').content, location.href);
  initTier();
  initFilm();
  initGeometry(film.el);
  html.dataset.layout = geo.W < 720 ? 'phone' : 'panel';
  lastPhone = geo.W < 720;
  initToast();
  initForms();
  initBar();
  addEventListener('resize', onResize);
  document.addEventListener('tbk:applied', layoutClasses);
  initSW(root);

  // The still decides when the veil lifts (1.2 s at the latest).
  Promise.race([film.still.decode ? film.still.decode().catch(() => {}) : Promise.resolve(), wait(1200)]).then(liftVeil);

  const hasSheet = sheet.initSheet();
  if (!hasSheet) {                                       // 404 and offline: capsule, bar, onyx
    layoutClasses();
    window.__tbk.ready = true; readyResolve();
    return;
  }
  lens.initLens();
  lensReady = true;
  // Geometry is worth waiting for briefly: the lens and the stations are placed from it.
  await Promise.race([loadGeometry(root), wait(1500)]);
  measure();
  layoutClasses();

  const route = document.body.dataset.route;
  if (route === 'home') {
    sheet.placeHome();
    recompute({ inner: false, state: 'home' });
    initRouter();
    await introHome();
  } else {
    sheet.placeInner('peek');
    camera.set(stationFor(document.body.dataset.station));
    recompute({ inner: true, state: sheet.S.state, top: 0 });
    initRouter();
    await veilDone;
  }
  // Every page is one tap away: fetch them all once the first view has landed.
  (window.requestIdleCallback || setTimeout)(prefetchAll);

  document.addEventListener('tbk:sheet', (e) => {
    if (sheet.S.run) return;
    const s = e.detail.state;
    if (sheet.isDetent(s)) setFold(s === 'full' || sheet.S.scroll.scrollTop > 24);
    else setFold(false);
  });
  // The fold follows the scroll direction. Scroll events arrive a few px at a time, so the
  // delta is summed over one direction of travel before it is compared with −8.
  let run = 0;
  document.addEventListener('tbk:scroll', (e) => {
    if (!sheet.isPhone() || document.body.dataset.route === 'home' || sheet.S.run) return;
    const { top, dy } = e.detail;
    run = (run <= 0) === (dy <= 0) ? run + dy : dy;
    if (run < -8) setFold(false);
    else if (top > 24 && run > 0) setFold(true);
  });
  document.addEventListener('tbk:tier', () => lens.relayout());
  document.addEventListener('tbk:encode', () => { measure(); if (document.body.dataset.route === 'home') lens.relayout(); else camera.set(stationFor(document.body.dataset.station)); });
  document.fonts?.ready.then(onResize);

  window.__tbk.ready = true;
  readyResolve();
  probe(T0);
}

window.__tbk.booting = boot().catch((err) => {
  console.error(err);
  html.classList.remove('intro', 'intro-bar');
  if (veil && veil.isConnected) veil.remove();
});
