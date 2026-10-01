// TABOUKHI — the soft router (SPEC §5). Same-origin links under the site root are fetched,
// parsed, and swapped into the one sheet while the camera travels; the film element is
// never re-mounted. A same-URL click is a no-op (it only scrolls the sheet to its top);
// popstate re-renders without pushing and never twice; any failure falls back to a real
// navigation.
import { solveStation, camTransform, measure } from './geometry.js';
import { camera } from './film.js';
import * as sheet from './sheet.js';
import * as lens from './lens.js';
import { setCurrent, recompute } from './bar.js';
import { hydrate } from './forms.js';

const R = { root: null, key: '', route: '', station: '', cache: new Map(), token: 0, busy: false, onSettle: [] };

const keyOf = (u) => { const url = new URL(u, location.href); return url.pathname.replace(/index\.html$/, '') + url.search; };

function underRoot(url) {
  return url.origin === location.origin && url.pathname.startsWith(R.root.pathname) && (/\/$/.test(url.pathname) || /\.html$/.test(url.pathname));
}

function fetchPage(url) {
  const k = keyOf(url);
  if (!R.cache.has(k)) {
    const p = fetch(url, { credentials: 'same-origin' })
      .then((r) => (r.ok && /text\/html/.test(r.headers.get('content-type') || '') ? r.text() : null))
      .catch(() => null);
    R.cache.set(k, p);
    p.then((t) => { if (!t) R.cache.delete(k); });
  }
  return R.cache.get(k);
}

// Shell links are written relative to the page they were built for. At boot they are made
// absolute from their root-relative data-to (icons and the manifest too: Chrome re-resolves
// icon links against the current URL), so no URL change — a push, or a Back that lands
// before its page has been fetched — can ever resolve them against the wrong depth.
function pinShellLinks() {
  document.querySelectorAll('link[data-to], .capsule[data-to], .bar a[data-to]').forEach((a) => a.setAttribute('href', new URL(a.dataset.to, R.root).href));
}
function repoint(rootContent) {
  document.querySelector('meta[name="tbk-root"]').setAttribute('content', rootContent);
}

export function stationFor(station) {
  if (!station || station === 'home' || station === 'onyx') return { tx: 0, ty: 0, z: 1 };
  return solveStation(station, sheet.freeArea());
}
const camMs = () => (innerWidth < 720 ? 700 : 900);

export async function go(href, { push = true } = {}) {
  const url = new URL(href, location.href);
  url.hash = '';
  const k = keyOf(url);
  if (k === R.key) {
    sheet.S.scroll?.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    return;
  }
  const token = ++R.token;
  document.dispatchEvent(new Event('tbk:nav'));
  const text = await fetchPage(url.href);
  if (token !== R.token) return;
  let doc = null;
  try { doc = text && new DOMParser().parseFromString(text, 'text/html'); } catch { doc = null; }
  const main = doc && doc.getElementById('page');
  if (!main || !doc.querySelector('.sheet') || !sheet.S.el) { location.href = url.href; return; }

  const b = doc.body.dataset;
  const to = { route: b.route, station: b.station, tab: b.tab || '', title: doc.title,
    root: doc.querySelector('meta[name="tbk-root"]')?.getAttribute('content') || './' };
  const from = R.route;
  // The Peek height the new page asks for is known before the morph aims at it.
  if (to.route !== 'home') sheet.measureNeed(document.importNode(main, true));
  if (push) history.pushState({ tbk: 1 }, '', url.href);
  repoint(to.root);                                     // at once: the new URL resolves them from now on
  R.key = k; R.route = to.route; R.station = to.station;
  R.busy = true;

  const apply = () => {
    const nodes = [...document.adoptNode(main).childNodes];
    sheet.S.main.replaceChildren(...nodes);
    document.title = to.title;
    const bd = document.body.dataset;
    bd.route = to.route; bd.station = to.station; bd.tab = to.tab;
    hydrate(sheet.S.main);
    const h = sheet.S.main.querySelector('h1');
    if (h) h.focus({ preventScroll: true });
    document.dispatchEvent(new Event('tbk:applied'));
  };

  setCurrent(to.tab);
  measure();
  try {
    if (from === 'home' && to.route !== 'home') {
      camera.move(stationFor(to.station), camMs());
      recompute({ inner: true, state: 'peek', top: 0 });
      await sheet.morphToInner(apply);
    } else if (from !== 'home' && to.route === 'home') {
      camera.move({ tx: 0, ty: 0, z: 1 }, camMs());
      recompute({ inner: false, state: 'home', top: 0 });
      await sheet.morphToHome(apply);
    } else {
      camera.move(stationFor(to.station), camMs());
      await sheet.swapInner(apply);
      recompute({ inner: true, state: sheet.S.state, top: 0 });
    }
  } catch (err) {
    console.error(err);
    location.href = url.href;
    return;
  }
  if (token === R.token) {
    R.busy = false;
    const q = R.onSettle.splice(0); q.forEach((f) => f());
  }
}

export function routerBusy() { return R.busy || !!sheet.S.run; }
export function whenSettled() { return routerBusy() ? new Promise((r) => R.onSettle.push(r)) : Promise.resolve(); }
export function currentStation() { return R.station; }

// Every page the shell links to, fetched once (the current one is skipped).
export function prefetchAll() {
  if (!R.root) return;
  for (const a of document.querySelectorAll('.bar a[data-to], .capsule[href]')) {
    const url = new URL(a.href, location.href);
    if (underRoot(url) && keyOf(url) !== R.key) fetchPage(url.href);
  }
}

export function initRouter() {
  R.root = new URL(document.querySelector('meta[name="tbk-root"]').content, location.href);
  R.key = keyOf(location.href);
  R.route = document.body.dataset.route;
  R.station = document.body.dataset.station;
  pinShellLinks();
  history.replaceState({ tbk: 1 }, '', location.href);

  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.target || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (!underRoot(url)) return;
    if (url.hash && keyOf(url) === R.key) return;       // in-page anchor (the skip link)
    e.preventDefault();
    go(url.href);
  });
  document.addEventListener('tbk:go', (e) => go(e.detail.href));

  const pre = (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.target) return;
    const url = new URL(a.href, location.href);
    if (underRoot(url) && keyOf(url) !== R.key) fetchPage(url.href);
  };
  document.addEventListener('pointerover', pre, { passive: true });
  document.addEventListener('touchstart', pre, { passive: true });
  document.addEventListener('focusin', pre);

  addEventListener('popstate', () => {
    if (keyOf(location.href) === R.key) return;         // hash-only change: nothing to render
    go(location.href, { push: false });
  });
}
