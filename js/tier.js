// TABOUKHI — performance tiers (SPEC §6, A2).
//   1   full design
//   1b  the sheet (frost and panel) drops from thick to regular glass
//   2   Home frost without backdrop-filter (pre-blurred ambient film + sharp film in a circle);
//       inner pages keep 1b
//   3   no backdrop-filter, or reduced transparency: all three surfaces are paint
// Forced with ?tier=1|1b|2|3 (never probed then). Otherwise persisted per session and
// lowered by a frame-time probe that runs once the load sequence has ended.
import { session } from './prefs.js';

const ORDER = ['1', '1b', '2', '3'];
const KEY = 'tbk.tier';
let forced = null;
let probed = 0;

export function currentTier() { return document.documentElement.dataset.tier || '1'; }

export function initTier() {
  const q = new URLSearchParams(location.search).get('tier');
  forced = ORDER.includes(q) ? q : null;
  let t = forced;
  if (!t) {
    const std = CSS.supports('backdrop-filter', 'blur(1px)');
    const wk = CSS.supports('-webkit-backdrop-filter', 'blur(1px)');
    const rt = matchMedia('(prefers-reduced-transparency: reduce)').matches;
    const saved = session.get(KEY);
    t = ORDER.includes(saved) ? saved : '1';
    if (!std && !wk) t = '3';
    else if (rt) t = '3';
    // A2: WebKit before 18 ignores a mask on a backdrop-filtered element (WebKit 279115), so
    // the frost hole would not open. Unprefixed backdrop-filter arrived in WebKit 18.0, the same
    // release that honours the mask, so "-webkit- only" is exactly WebKit < 18 — Safari, and
    // also Chrome/Firefox on iOS, in-app browsers (Instagram) and home-screen apps, which a UA
    // parse misses. Use the tier-2 lens there.
    else if (!std && wk && ORDER.indexOf(t) < 2) t = '2';
  }
  apply(t, false);
  return t;
}

function apply(t, persist) {
  const prev = currentTier();
  document.documentElement.dataset.tier = t;
  if (persist) session.set(KEY, t);
  if (prev !== t) document.dispatchEvent(new CustomEvent('tbk:tier', { detail: t }));
}

export function dropTier(to) {
  if (forced) return currentTier();
  const cur = ORDER.indexOf(currentTier());
  const next = to ? Math.max(cur, ORDER.indexOf(to)) : Math.min(cur + 1, 2);
  if (next !== cur) apply(ORDER[next], true);
  return currentTier();
}

// 90 rAF deltas while visible; median > 20 ms or p90 > 34 ms drops one tier; one re-probe.
// A steady ~30 Hz cadence is Low Power Mode throttling rAF, not jank: it drops nothing. And
// tier 2 is never reached from the probe while the film is not playing (nothing to judge).
export function probe(startedAt) {
  if (forced || probed >= 2 || ORDER.indexOf(currentTier()) >= 2) return;
  const since = performance.now() - startedAt;
  if (since < 3000) { setTimeout(() => probe(startedAt), 3000 - since); return; }
  if (document.visibilityState !== 'visible') {
    document.addEventListener('visibilitychange', () => probe(startedAt), { once: true });
    return;
  }
  probed++;
  const d = [];
  let last = 0;
  const step = (now) => {
    if (document.visibilityState !== 'visible') { probed--; return; }
    if (last) d.push(now - last);
    last = now;
    if (d.length < 90) { requestAnimationFrame(step); return; }
    d.sort((a, b) => a - b);
    const med = d[45], p90 = d[81];
    const p10 = d[Math.floor(d.length * 0.1)], p90w = d[Math.floor(d.length * 0.9)];
    if (p10 > 28 && p90w < 40) return;                  // steady 30 Hz: Low Power Mode
    if (med > 20 || p90 > 34) {
      const v = document.querySelector('.film__video');
      if (ORDER.indexOf(currentTier()) + 1 >= 2 && (!v || v.paused)) return;
      dropTier(); setTimeout(() => probe(startedAt), 1000);
    }
  };
  requestAnimationFrame(step);
}
