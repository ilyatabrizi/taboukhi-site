// TABOUKHI — the toast (C13): paint only, above the bar. Enters on the house spring,
// holds 1.6 s, fades in 240 ms. role="status" announces it.
import { CSS_EASE } from './spring.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');
let el = null, timer = 0, anim = null;

export function initToast() { el = document.querySelector('.toast'); }

export function toast(msg) {
  if (!el) return;
  clearTimeout(timer);
  if (anim) anim.cancel();
  el.textContent = msg;
  el.style.opacity = '1';
  anim = RM.matches
    ? el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 120 })
    : el.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: CSS_EASE.spring });
  anim.finished.then(() => { anim = null; }, () => {});
  timer = setTimeout(() => {
    const out = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: RM.matches ? 120 : 240, easing: 'linear' });
    anim = out;
    out.finished.then(() => { el.style.opacity = '0'; el.textContent = ''; out.cancel(); anim = null; }, () => {});
  }, 360 + 1600);
}
