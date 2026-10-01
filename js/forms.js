// TABOUKHI — forms, switches, copy, the preview chip, the rim ripple and the pointer light.
// Everything is delegated from document, so pages swapped in by the router need only
// hydrate() for state that lives outside the markup (the Film switch).
// Demo behaviour (SPEC §8): validate, show errors, aria-busy for 700 ms, then the success
// state. No network request and no storage — ever.
import { prefs } from './prefs.js';
import { toast } from './toast.js';
import { detentTo, isPhone, S } from './sheet.js';

const RM = matchMedia('(prefers-reduced-motion: reduce)');
const FINE = matchMedia('(hover: hover) and (pointer: fine)');
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const saved = new WeakMap();        // form wrap → its original children (for "Start again")

function describe(input, id, on) {
  const ids = new Set((input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
  if (on) ids.add(id); else ids.delete(id);
  if (ids.size) input.setAttribute('aria-describedby', [...ids].join(' ')); else input.removeAttribute('aria-describedby');
}
function setError(form, name, on) {
  const err = form.parentElement.querySelector(`[data-err="${name}"]`) || form.querySelector(`[data-err="${name}"]`);
  const input = form.elements[name];
  const el = input && (input.length && !input.tagName ? input[0] : input);
  if (el) { if (on) el.setAttribute('aria-invalid', 'true'); else el.removeAttribute('aria-invalid'); if (err) describe(el, err.id, on); }
  if (err) err.hidden = !on;
  return el;
}

function validate(form) {
  const kind = form.dataset.form;
  const f = form.elements;
  const bad = [];
  const email = (f.email.value || '').trim();
  if (kind === 'notify') {
    if (!EMAIL.test(email)) bad.push('email');
  } else {
    if (!f.name.value.trim()) bad.push('name');
    if (!EMAIL.test(email)) bad.push('email');
    if (f.reply.value === 'phone' && !f.phone.value.trim()) bad.push('phone');
    if (!f.idea.value.trim()) bad.push('idea');
    if (!f.consent.checked) bad.push('consent');
  }
  // An empty address and a malformed one get different words (data-empty on the message).
  const eErr = form.parentElement.querySelector('[data-err="email"]') || form.querySelector('[data-err="email"]');
  if (eErr && eErr.dataset.empty) {
    if (!eErr.dataset.bad) eErr.dataset.bad = eErr.textContent;
    eErr.textContent = email ? eErr.dataset.bad : eErr.dataset.empty;
  }
  const all = kind === 'notify' ? ['email'] : ['name', 'email', 'phone', 'idea', 'consent'];
  let first = null;
  for (const n of all) {
    const el = setError(form, n, bad.includes(n));
    if (bad.includes(n) && !first) first = el;
  }
  // First invalid field in document order.
  if (bad.length) {
    const order = [...form.querySelectorAll('[aria-invalid="true"]')];
    (order[0] || first)?.focus();
  }
  return bad.length === 0;
}

function setBusy(form, on) {
  const btn = form.querySelector('[type="submit"]');
  if (on) {
    form.setAttribute('aria-busy', 'true');
    if (btn) {
      btn.dataset.label = btn.querySelector('.btn__l') ? btn.querySelector('.btn__l > span').textContent : btn.textContent;
      if (btn.querySelector('.btn__l')) btn.querySelectorAll('.btn__l > span').forEach((s) => { s.textContent = btn.dataset.busy; });
      else btn.textContent = btn.dataset.busy;
    }
  } else {
    form.removeAttribute('aria-busy');
    if (btn && btn.dataset.label) {
      if (btn.querySelector('.btn__l')) btn.querySelectorAll('.btn__l > span').forEach((s) => { s.textContent = btn.dataset.label; });
      else btn.textContent = btn.dataset.label;
    }
  }
}

function succeed(form) {
  const wrap = form.closest('[data-form-wrap]');
  const tpl = S.main.querySelector(`template[data-done="${form.dataset.form}"]`) || document.querySelector(`template[data-done="${form.dataset.form}"]`);
  if (!wrap || !tpl) return;
  const email = form.elements.email.value.trim();
  setBusy(form, false);
  saved.set(wrap, [...wrap.childNodes]);
  const done = tpl.content.firstElementChild.cloneNode(true);
  done.querySelectorAll('[data-email]').forEach((s) => { s.textContent = email; });
  wrap.replaceChildren(done);
  const mark = done.querySelector('.done__mark');
  if (mark && !RM.matches) {
    const a = mark.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 280, easing: 'linear' });
    a.finished.then(() => a.cancel(), () => {});
  }
  const h = done.querySelector('.done__h');
  h?.focus({ preventScroll: true });
  wrap.scrollIntoView?.({ block: 'nearest', behavior: RM.matches ? 'auto' : 'smooth' });
}

function reset(btn) {
  const wrap = btn.closest('[data-form-wrap]');
  const nodes = wrap && saved.get(wrap);
  if (!nodes) return;
  wrap.replaceChildren(...nodes);
  saved.delete(wrap);
  const form = wrap.querySelector('form');
  form.reset();
  form.querySelectorAll('[aria-invalid]').forEach((el) => el.removeAttribute('aria-invalid'));
  form.querySelectorAll('[data-err]').forEach((el) => { el.hidden = true; });
  form.querySelectorAll('[role="switch"]').forEach((sw) => sw.setAttribute('aria-checked', String(sw.checked)));
  form.querySelector('input, textarea')?.focus({ preventScroll: true });
}

function ripple(el, e) {
  const r = el.getBoundingClientRect();
  el.style.setProperty('--px', `${(e.clientX - r.left).toFixed(0)}px`);
  el.style.setProperty('--py', `${(e.clientY - r.top).toFixed(0)}px`);
  el.classList.remove('is-rp');
  void el.offsetWidth;                                  // restart the ring
  el.classList.add('is-rp');
}

// C2: the specular light leans toward a fine pointer; springs back on leave.
function initPointerLight() {
  if (!FINE.matches || RM.matches) return;
  let raf = 0, target = null, ev = null;
  addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    const el = e.target.closest && e.target.closest('.capsule, .bar, .sheet:not([data-state="home"]):not([data-state="morph"])');
    if (target && target !== el) { target.style.removeProperty('--hx'); target.style.removeProperty('--hy'); }
    target = el; ev = e;
    if (!el || raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (!target) return;
      const r = target.getBoundingClientRect();
      const hx = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * 28;
      const hy = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)) * 22;
      target.style.setProperty('--hx', `${hx.toFixed(1)}%`);
      target.style.setProperty('--hy', `${hy.toFixed(1)}%`);
    });
  }, { passive: true });
  document.addEventListener('mouseout', (e) => { if (!e.relatedTarget && target) { target.style.removeProperty('--hx'); target.style.removeProperty('--hy'); target = null; } });
}

let io = null;
export function hydrate(root = document) {
  root.querySelectorAll('form[data-form]').forEach((f) => {
    f.noValidate = true;
    f.querySelectorAll('input[type="email"]').forEach((i) => i.setAttribute('pattern', '[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}'));
  });
  const fs = root.querySelector('[data-film-switch]');
  if (fs) {
    const playing = prefs.film !== 'held';
    fs.checked = playing; fs.setAttribute('aria-checked', String(playing));
    const st = root.querySelector('[data-film-state]');
    if (st) st.textContent = playing ? st.dataset.on : st.dataset.off;
  }
  if (io) {
    // A swapped-in page replaces everything the observer was watching: start it afresh.
    if (root === S.main) io.disconnect();
    root.querySelectorAll('.group, .notify').forEach((el) => io.observe(el));
  }
}

export function initForms() {
  if ('IntersectionObserver' in window) io = new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle('is-offscreen', !e.isIntersecting)));
  hydrate(document);
  initPointerLight();

  document.addEventListener('submit', (e) => {
    const form = e.target.closest('form[data-form]');
    if (!form) return;
    e.preventDefault();
    if (form.getAttribute('aria-busy') === 'true') return;
    if (!validate(form)) { if (isPhone() && S.state !== 'full') detentTo('full'); return; }
    setBusy(form, true);
    setTimeout(() => succeed(form), 700);
  });
  document.addEventListener('input', (e) => {
    const form = e.target.form; if (!form || !form.dataset.form) return;
    const n = e.target.name;
    if (e.target.getAttribute('aria-invalid') === 'true') setError(form, n, false);
    if (n === 'reply' && e.target.value !== 'phone') setError(form, 'phone', false);
  });
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t.matches('[role="switch"]')) {
      t.setAttribute('aria-checked', String(t.checked));
      if (t.form && t.checked) setError(t.form, t.name, false);
    }
    if (t.matches('[data-film-switch]')) {
      prefs.film = t.checked ? 'playing' : 'held';
      const st = document.querySelector('[data-film-state]');
      if (st) st.textContent = t.checked ? st.dataset.on : st.dataset.off;
    }
  });
  document.addEventListener('click', (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const addr = copy.dataset.copy;
      const ok = () => toast(copy.dataset.ok), bad = () => toast(copy.dataset.fail);
      try { navigator.clipboard.writeText(addr).then(ok, bad); } catch { bad(); }
      return;
    }
    const rs = e.target.closest('[data-reset]');
    if (rs) { reset(rs); return; }
    const chip = e.target.closest('.ph-chip');
    if (chip) {
      const on = document.documentElement.classList.toggle('show-ph');
      document.querySelectorAll('.ph-chip').forEach((c) => c.setAttribute('aria-pressed', String(on)));
    }
  });
  document.addEventListener('pointerdown', (e) => {
    const el = e.target.closest && e.target.closest('.rp');
    if (el && !RM.matches) ripple(el, e);
  }, { passive: true });
}
