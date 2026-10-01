// TABOUKHI — the film and the camera (C1).
// The film plays, full stop. Muted/defaultMuted/playsInline are set as PROPERTIES before
// play() (the attribute alone is not always honoured); it is replayed on every signal a
// strict autoplay policy lifts on; it is never hidden and never seeked (two keyframes).
// The Film switch (prefs.film = 'held') is the only thing that may pause it.
import { prefs } from './prefs.js';
import { currentTier, dropTier } from './tier.js';
import { geo, setEncode, camTransform } from './geometry.js';
import { EASE, CSS_EASE, clamp } from './spring.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');

export const film = {
  el: null, cam: null, still: null, video: null, ambient: null,
  playing: false, held: false,
};

function play(v) {
  if (!v || document.hidden) return;
  if (v === film.video && film.held) return;
  const p = v.play();
  if (p) p.catch((err) => {
    // NotAllowedError is an autoplay policy (Low Power Mode): the poster stays and gestures
    // retry. A decode failure while the frost is up drops to the tier-2 lens.
    if (err && err.name === 'NotSupportedError' && document.body.dataset.route === 'home') dropTier('2');
  });
}

function pinEncode() {
  // <source media> is evaluated once; pin the poster and the geometry to what was loaded.
  const src = film.video.currentSrc || '';
  let enc = /film-pt-/.test(src) ? 'pt' : /film-sq-/.test(src) ? 'sq' : null;
  if (!enc) enc = matchMedia('(max-aspect-ratio: 4/5)').matches ? 'pt' : 'sq';
  film.el.dataset.enc = enc;
  setEncode(enc);
  // Point the <img> at the same file first, then drop the <picture> sources, so the
  // re-selection lands on the URL already loaded (no second download, no swap on rotation).
  const want = film.still.dataset[enc];
  if (want && film.still.getAttribute('src') !== want) film.still.setAttribute('src', want);
  const pic = film.still.parentElement;
  if (pic && pic.tagName === 'PICTURE') pic.querySelectorAll('source').forEach((s) => s.remove());
  document.dispatchEvent(new CustomEvent('tbk:encode', { detail: enc }));
}

export function initFilm() {
  film.el = document.querySelector('.film');
  film.cam = film.el.querySelector('.film__cam');
  film.still = film.el.querySelector('.film__still');
  film.video = film.el.querySelector('.film__video');
  film.ambient = film.el.querySelector('.film__ambient');
  const v = film.video;

  // Encode first, from the same query the <source media> used at load.
  setEncode(matchMedia('(max-aspect-ratio: 4/5)').matches ? 'pt' : 'sq');
  if (v.currentSrc) pinEncode(); else v.addEventListener('loadedmetadata', pinEncode, { once: true });
  if (!film.el.dataset.enc) film.el.dataset.enc = geo.enc;

  v.muted = true; v.defaultMuted = true; v.playsInline = true; v.loop = true; v.controls = false;
  v.setAttribute('muted', ''); v.setAttribute('playsinline', '');
  film.held = prefs.film === 'held';

  v.addEventListener('playing', () => { film.playing = true; v.classList.add('is-on'); document.dispatchEvent(new Event('tbk:playing')); });
  v.addEventListener('pause', () => { film.playing = false; if (!document.hidden && !film.held) play(v); });
  v.addEventListener('ended', () => play(v));
  ['loadedmetadata', 'canplay', 'stalled', 'suspend'].forEach((t) => v.addEventListener(t, () => { if (v.paused) play(v); }));
  // Capture: a failing <source> fires its error on the source element, not on the video. Only
  // the last one counts (the portrait file failing still leaves the square one to try).
  v.addEventListener('error', (e) => {
    if (e.target !== v && e.target !== v.querySelector('source:last-of-type')) return;
    if (document.body.dataset.route === 'home') dropTier('2');
  }, true);
  // Low Power Mode blocks autoplay until a user activation. Only these events carry one (a touch
  // pointerdown/touchstart does not), and every gesture retries until the film is playing.
  gestureKick(v, () => { play(v); if (film.ambient.src) play(film.ambient); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { play(v); if (film.ambient.src) play(film.ambient); } });
  if (film.held) { v.pause(); v.classList.add('is-on'); } else { if (v.readyState >= 2) play(v); play(v); }

  document.addEventListener('tbk:film', (e) => setHeld(e.detail === 'held'));
  document.addEventListener('tbk:tier', syncAmbient);
  syncAmbient();
}

const GESTURES = ['pointerup', 'touchend', 'click', 'keydown'];
function gestureKick(el, kick) {
  GESTURES.forEach((t) => addEventListener(t, kick, { passive: true, capture: true }));
  el.addEventListener('playing', () => GESTURES.forEach((t) => removeEventListener(t, kick, true)), { once: true });
}

export function setHeld(held) {
  film.held = held;
  if (held) film.video.pause();      // holds on the current frame; the frame stays on screen
  else play(film.video);
  if (film.ambient.src) { if (held) film.ambient.pause(); else play(film.ambient); }
}

// ── Tier 2: the ambient film, kept in step without seeking the sharp one. ────────
let syncTimer = 0;
function syncAmbient() {
  const a = film.ambient;
  const on = currentTier() === '2';
  if (on && !a.src) {
    a.muted = true; a.defaultMuted = true; a.playsInline = true; a.loop = true;
    a.poster = a.dataset.poster; a.src = a.dataset.src; a.preload = 'auto';
    a.addEventListener('pause', () => { if (!document.hidden && !film.held && currentTier() === '2') play(a); });
    gestureKick(a, () => play(a));
    play(a);
  }
  clearInterval(syncTimer);
  if (on) syncTimer = setInterval(() => {
    const s = film.video, n = geo.track ? geo.track.frames / geo.track.fps : (s.duration || 16);
    if (!a.src || a.readyState < 2 || s.paused) return;
    let d = s.currentTime - a.currentTime;
    d = ((d + n / 2) % n + n) % n - n / 2;              // wrapped to ±n/2 (±8 s)
    if (Math.abs(d) > 1.5) { a.currentTime = s.currentTime; a.playbackRate = 1; film.ambientSeeks = (film.ambientSeeks || 0) + 1; }
    else if (Math.abs(d) > 0.05) a.playbackRate = 1 + clamp(0.5 * d, -0.1, 0.1);
    else a.playbackRate = 1;
  }, 1000);
}

// ── The camera: transform only, WAAPI, committed and cancelled at the end. ──────
export const camera = {
  from: { tx: 0, ty: 0, z: 1 }, to: { tx: 0, ty: 0, z: 1 }, t0: 0, dur: 0, anim: null,
  // The camera's transform right now (JS mirror of the running animation).
  now() {
    if (!this.anim || !this.dur) return this.to;
    const p = EASE.glass(clamp((performance.now() - this.t0) / this.dur, 0, 1));
    const a = this.from, b = this.to;
    return { tx: a.tx + (b.tx - a.tx) * p, ty: a.ty + (b.ty - a.ty) * p, z: a.z + (b.z - a.z) * p };
  },
  set(t) {
    if (this.anim) { this.anim.cancel(); this.anim = null; }
    this.from = this.to = t; this.dur = 0;
    film.cam.style.transform = camTransform(t);
  },
  move(t, duration) {
    if (RM.matches || !duration) { this.set(t); return Promise.resolve(); }
    const start = this.now();
    if (this.anim) { this.anim.cancel(); this.anim = null; }
    this.from = start; this.to = t; this.t0 = performance.now(); this.dur = duration;
    film.cam.style.transform = camTransform(start);
    const anim = film.cam.animate(
      [{ transform: camTransform(start) }, { transform: camTransform(t) }],
      { duration, easing: CSS_EASE.glass });
    this.anim = anim;
    return anim.finished.then(() => {
      if (this.anim !== anim) return;
      film.cam.style.transform = camTransform(t);
      anim.cancel(); this.anim = null; this.dur = 0;
    }, () => {});
  },
  // Screen point of a rest-camera point under the camera's current transform.
  apply([x, y]) {
    const c = this.now();
    const cx = geo.W / 2, cy = geo.H / 2;
    return [cx + (x - cx) * c.z + c.tx, cy + (y - cy) * c.z + c.ty];
  },
};
