#!/usr/bin/env python3
"""TABOUKHI website demo: the acceptance suite (SPEC section 9, checks 1-39).

    python3 e2e.py                                  local build on :8261 (starts serve.py in the background if nothing listens)
    python3 e2e.py http://localhost:8261/           explicit base URL
    python3 e2e.py --live                           https://ilyatabrizi.github.io/taboukhi-site/ (after a deploy)
    python3 e2e.py --only 1,2,20-26                 run some checks
    python3 e2e.py --viewport 390x844,1440x900      limit viewports (also: phone, tablet, desktop, or a width like 390)
    python3 e2e.py -v                               print every sub-assertion under its check
    python3 e2e.py --list                           list the checks

Each check prints PASS or FAIL with one line of reason, then a summary line
"N passed, M failed". The exit code is the number of failures.

Every selector the suite touches is in SEL, below, so the integrator can realign
it with the DOM in one place. The browser is the system Chrome, through Playwright
(p.chromium.launch(channel="chrome")); service workers are blocked in every context
so request interception and counts are honest.

Pixel checks follow the brand book: visible motion is a mean L difference between two
screenshots; blur and legibility are Laplacian variances (OpenCV); contrast is the p16
method (render twice, glyphs transparent the second time, diff, measure only fully
inked stroke centres), over a film frozen at chosen loop times with playbackRate 0.
"""
from __future__ import annotations

import argparse
import asyncio
import html.parser
import json
import math
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
PORT = 8261
DEFAULT_BASE = "http://localhost:%d/" % PORT
LIVE_BASE = "https://ilyatabrizi.github.io/taboukhi-site/"
BASE_PATH = "/taboukhi-site/"
FOUNDING = "https://taboukhi.com/"
FOUNDER = "TABOUKHI is the first eyewear brand established by Ilya Taboukhi."
EMAIL = "info@taboukhi.com"
PREVIEW_OK = "This is a preview. Nothing was sent."

# --------------------------------------------------------------------------------------
# Every selector in one place. SPEC names; realign here if the DOM differs.
# --------------------------------------------------------------------------------------
SEL = {
    "root_meta": 'meta[name="tbk-root"]',
    "film": ".film",
    "cam": ".film__cam",
    "still": ".film__still",
    "video": ".film__video",
    "ambient": ".film__ambient",
    "veil": ".veil",
    "capsule": ".capsule",
    "dock": ".dock",
    "bar": ".bar",
    "tab": ".bar a",
    "thumb": ".bar__thumb",
    "sheet": ".sheet",
    "scroll": ".sheet__scroll",
    "grab": ".sheet__grab",
    "page": "#page",
    "h1": "#page h1",
    "lens": ".lens",
    "handle": ".lens__handle",
    "hint": "#hint",
    "btn": ".btn",
    "notify": ".notify",
    "notify_input": '.notify input[type="email"]',
    "notify_go": ".notify__go",
    "cform": "#page form:not(.notify)",
    "cform_submit": '#page form:not(.notify) button[type="submit"], #page form:not(.notify) .btn',
    "seg": ".seg",
    "reply": 'input[name="reply"]',
    "consent": 'input[name="consent"]',
    "switch": 'input[role="switch"]',
    "toast": ".toast",
    "eyebrow": ".eyebrow",
    "ph": "[data-ph]",
    "defs": "svg.defs",
    "wm_def": "#p-wm",
    "notices_link": '#page a[href$="notices/"]',
    "skip_text": "Skip to content",
    "copy_text": "Copy",
    "preview_text": "Preview",
    "glass": [".capsule", ".bar", ".sheet"],
}
TAB = {"house": "House", "collection": "Collection", "commission": "Commission", "contact": "Contact"}
TITLES = {
    "home": "TABOUKHI", "house": "The house — TABOUKHI", "collection": "Collection — TABOUKHI",
    "commission": "Commission — TABOUKHI", "contact": "Contact — TABOUKHI", "notices": "Notices — TABOUKHI",
    "404": "Nothing here — TABOUKHI", "offline": "Offline — TABOUKHI",
}
ROUTE_PATH = {"home": "", "house": "house/", "collection": "collection/", "commission": "commission/",
              "contact": "contact/", "notices": "notices/", "404": "404.html", "offline": "offline.html"}
INNER = ["house", "collection", "commission", "contact", "notices"]
# station frame points in master fractions; None = the k-track mean (the engraving centre)
STATION_PT = {"house": None, "collection": (0.30, 0.44), "commission": None, "contact": (0.72, 0.80)}
BANNED = re.compile(r"titanium|luxury|luxurious|exciting|passionate", re.I)
YEAR = re.compile(r"\b(19|20)\d{2}\b")

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")

VIEWPORTS = {
    "320x568": dict(w=320, h=568, dpr=2, touch=True),
    "375x812": dict(w=375, h=812, dpr=3, touch=True),
    "390x844": dict(w=390, h=844, dpr=3, touch=True),
    "430x932": dict(w=430, h=932, dpr=3, touch=True),
    "768x1024": dict(w=768, h=1024, dpr=2, touch=True),
    "1024x768": dict(w=1024, h=768, dpr=1, touch=False),
    "1440x900": dict(w=1440, h=900, dpr=1, touch=False),
    "1920x1080": dict(w=1920, h=1080, dpr=1, touch=False),
}
ALL_VP = list(VIEWPORTS)
PHONE, TABLET, DESK = "390x844", "768x1024", "1440x900"

TEXT_OK = {
    "bone": (244, 242, 237, 1.0), "label-2": (244, 242, 237, 0.84), "label-3": (244, 242, 237, 0.78),
    "ti": (169, 168, 163, 1.0), "ti-2": (195, 194, 189, 1.0), "ti-3": (142, 141, 137, 1.0),
}
ONYX = (2, 2, 2, 1.0)
ERROR = (255, 138, 128, 1.0)


def rmin_rmax(w):
    return (104, 136) if w < 720 else (120, 170) if w < 1080 else (132, 248)   # desktop max 248 since T-32 (a 1920 name is ~427 px)


# --------------------------------------------------------------------------------------
# In-page JavaScript. __PRELUDE__ is replaced by the helpers below with SEL injected.
# --------------------------------------------------------------------------------------
PRELUDE = r"""
const S = __SEL__;
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const cs = (e, p) => getComputedStyle(e, p || null);
const desc = (e) => {
  if (!e || e.nodeType !== 1) return String(e);
  let s = e.tagName.toLowerCase();
  if (e.id) s += '#' + e.id;
  const c = typeof e.className === 'string' ? e.className : ((e.className && e.className.baseVal) || '');
  if (c.trim()) s += '.' + c.trim().split(/\s+/).slice(0, 3).join('.');
  return s;
};
const rect = (e) => { if (!e) return null; const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
const rgba = (c) => {
  if (!c) return null;
  let m = c.match(/rgba?\(([^)]+)\)/);
  if (m) { const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
  m = c.match(/color\(srgb\s+([^)]+)\)/);
  if (m) { const p = m[1].split(/[\s\/]+/).filter(Boolean).map(parseFloat); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
  return null;
};
const rendered = (e) => {
  if (!e || !e.getClientRects().length) return false;
  if (cs(e).visibility !== 'visible') return false;
  for (let a = e; a && a.nodeType === 1; a = a.parentElement) if (parseFloat(cs(a).opacity) === 0) return false;
  return true;
};
const tiny = (e) => { const r = e.getBoundingClientRect(); return r.width <= 2 || r.height <= 2; };
const ownText = (e) => Array.from(e.childNodes).filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent).join(' ').replace(/\s+/g, ' ').trim();
const SKIPTAG = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
const textElems = (root) => $$('*', root || document.body).filter(e => !SKIPTAG.has(e.tagName) && !(e.closest && e.closest('svg')) && ownText(e) && rendered(e) && !tiny(e));
const hasBF = (s) => !!((s.backdropFilter && s.backdropFilter !== 'none') || (s.webkitBackdropFilter && s.webkitBackdropFilter !== 'none'));
const glassEls = () => $$('*').filter(e => e.getClientRects().length && cs(e).visibility === 'visible' && hasBF(cs(e)));
const lineRects = (e) => {
  const out = [];
  for (const n of e.childNodes) {
    if (n.nodeType !== 3 || !n.textContent.trim()) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const q of rg.getClientRects()) if (q.width > 1 && q.height > 1) out.push([q.left, q.top, q.right, q.bottom]);
  }
  return out;
};
const homeCopy = () => { const p = $(S.page); return p ? textElems(p).flatMap(lineRects) : []; };
// every laid-out line of copy, faded ones included (a faded hint may still hold the clamp)
const homeCopyAll = () => { const p = $(S.page); return p ? $$('*', p).filter(e => !SKIPTAG.has(e.tagName) && ownText(e) && e.getClientRects().length && cs(e).visibility === 'visible').flatMap(lineRects) : []; };
const objpos = (v) => v ? cs(v).objectPosition.split(' ').map(x => x.endsWith('%') ? parseFloat(x) / 100 : 0.5) : [0.5, 0.5];
const geom = () => {
  const v = $(S.video), st = $(S.still), s = $(S.sheet), c = $(S.capsule), b = $(S.bar), cam = $(S.cam);
  const tf = s ? cs(s).transform : 'none';
  let mask = 'none';
  if (s) { const a = cs(s).maskImage, w = cs(s).webkitMaskImage; mask = (a && a !== 'none') ? a : (w || 'none'); }
  return {
    W: innerWidth, H: innerHeight, route: (document.body && document.body.dataset.route) || '',
    station: (document.body && document.body.dataset.station) || '', title: document.title, url: location.href,
    film: {v: rect(v), still: rect(st), src: v ? (v.currentSrc || '') : '', stillSrc: st ? (st.currentSrc || st.src || '') : '',
           objpos: objpos(v || st), camT: cam ? cs(cam).transform : null, t: v ? v.currentTime : 0, paused: v ? v.paused : true},
    caps: rect(c), bar: rect(b),
    sheet: s ? {rect: rect(s), state: s.dataset.state || '', clip: cs(s).clipPath, mask: mask, h: s.offsetHeight,
                ty: (tf && tf !== 'none') ? new DOMMatrix(tf).m42 : 0, lr: parseFloat(cs(s).getPropertyValue('--lr')),
                det: (window.__tbk && window.__tbk.sheet) ? window.__tbk.sheet().detents : null} : null,
    current: $$(S.tab).filter(a => a.getAttribute('aria-current') === 'page').map(a => a.textContent.trim()),
  };
};
const splitTop = (v) => { const out = []; let d = 0, cur = ''; for (const ch of v) { if (ch === '(') d++; if (ch === ')') d--; if (ch === ',' && d === 0) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; };
const outerShadows = (v) => (!v || v === 'none') ? [] : splitTop(v).filter(p => !/\binset\b/.test(p));
const isDrop = (p) => { const n = (p.replace(/[a-z-]+\([^)]*\)/gi, ' ').match(/-?[\d.]+px/g) || []).map(parseFloat); return Math.abs(n[0] || 0) > 0 || Math.abs(n[1] || 0) > 0 || (n[2] || 0) > 0; };
const rootWalk = () => {
  const glass = glassEls(); const bad = [];
  for (const g of glass) for (let a = g.parentElement; a; a = a.parentElement) {
    const s = cs(a); const why = [];
    if (s.transform !== 'none') why.push('transform ' + s.transform);
    if (s.filter !== 'none') why.push('filter ' + s.filter);
    if (s.perspective !== 'none') why.push('perspective');
    if (parseFloat(s.opacity) < 1) why.push('opacity ' + s.opacity);
    if ((s.maskImage && s.maskImage !== 'none') || (s.webkitMaskImage && s.webkitMaskImage !== 'none')) why.push('mask');
    if (s.clipPath && s.clipPath !== 'none') why.push('clip-path');
    if (s.mixBlendMode !== 'normal') why.push('mix-blend-mode');
    if (hasBF(s)) why.push('backdrop-filter (glass in glass)');
    if (s.willChange && s.willChange !== 'auto' && /transform|filter|opacity|mask|clip-path|mix-blend|perspective/.test(s.willChange)) why.push('will-change: ' + s.willChange);
    if (why.length) bad.push(desc(g) + ' under ' + desc(a) + ' (' + why.join(', ') + ')');
  }
  const anims = [], notes = [];
  for (const an of document.getAnimations()) {
    const ef = an.effect; const t = ef && ef.target; if (!t) continue;
    if (!glass.some(g => t === g || t.contains(g))) continue;
    const tim = ef.getTiming();
    const label = desc(t) + (ef.pseudoElement || '') + ' ' + (an.animationName || an.transitionProperty || an.id || 'waapi') + ' ' + an.playState + ' fill=' + tim.fill;
    if (ef.pseudoElement) { if (an.playState === 'finished' && /both|forwards/.test(tim.fill)) notes.push(label); continue; }
    anims.push(label);
  }
  return {glass: glass.map(desc), bad: bad, anims: anims, notes: notes};
};
const kUnder = (el) => {
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    if (S.glass.some(g => a.matches(g))) return 'glass';
    const c = rgba(cs(a).backgroundColor);
    if (c && c[3] >= 0.9 && c[0] <= 12 && c[1] <= 12 && c[2] <= 12) return 'onyx';
    if (c && c[3] > 0.2) return 'other ' + cs(a).backgroundColor;
  }
  return 'none';
};
"""


def J(src):
    return src.replace("__PRELUDE__", PRELUDE.replace("__SEL__", json.dumps(SEL)))


GEOM_JS = J("() => { __PRELUDE__ return geom(); }")

VEIL_GONE_JS = J("""() => { __PRELUDE__ const v = $(S.veil); if (!v || !v.isConnected) return true;
  const s = cs(v); return s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.01; }""")

LENS_CLOSED_JS = J("""() => { __PRELUDE__ const s = $(S.sheet); if (!s || s.dataset.state !== 'home') return true;
  const r = parseFloat(cs(s).getPropertyValue('--lr')); const W = innerWidth;
  const max = W < 720 ? 136 : W < 1080 ? 170 : 200;
  return r > 0 && r <= max + 2 && document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.target === s).length === 0; }""")

HINT_SHOWN_JS = J("""() => { __PRELUDE__ const h = $(S.hint); return !h || parseFloat(cs(h).opacity) > 0.98; }""")

ANIMS_DONE_JS = """() => document.getAnimations().every(a => { if (a.playState !== 'running') return true;
  const t = a.effect && a.effect.getComputedTiming(); return !t || !isFinite(t.endTime); })"""

STABLE_JS = """([sels, frames, timeout]) => new Promise(res => {
  let last = null, same = 0; const t0 = performance.now();
  const tick = () => {
    const cur = sels.map(s => { const e = document.querySelector(s); if (!e) return '-'; const r = e.getBoundingClientRect(); const c = getComputedStyle(e);
      return [r.x.toFixed(1), r.y.toFixed(1), r.width.toFixed(1), r.height.toFixed(1), c.transform, c.opacity, c.clipPath].join(','); }).join('|');
    same = (cur === last) ? same + 1 : 0; last = cur;
    if (same >= frames || performance.now() - t0 > timeout) return res(same >= frames);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})"""

TAB_HIT_JS = J("""(l) => { __PRELUDE__ const a = $$(S.tab).find(x => x.textContent.trim() === l); const b = $(S.bar); if (!a || !b) return false;
  const r = a.getBoundingClientRect(), q = b.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
  if (x < q.left || x > q.right || y < q.top || y > q.bottom) return false;
  const h = document.elementFromPoint(x, y); return !!h && (h === a || a.contains(h)) && parseFloat(cs(a).opacity) > 0.5; }""")

PLAYING_JS = J("""() => { __PRELUDE__ const v = $(S.video); return !!v && !v.paused && v.readyState >= 2 && v.currentTime > 0.1; }""")

LENS_JS = J("""() => { __PRELUDE__ const l = $(S.lens), s = $(S.sheet), v = $(S.video), st = $(S.still);
  if (!l || !s) return null; const r = l.getBoundingClientRect(); const c = cs(s);
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, rw: r.width / 2, lr: parseFloat(c.getPropertyValue('--lr')),
    lx: c.getPropertyValue('--lx').trim(), ly: c.getPropertyValue('--ly').trim(), state: s.dataset.state || '',
    shown: cs(l).display !== 'none' && cs(l).visibility === 'visible',
    t: v ? v.currentTime : 0, paused: v ? v.paused : true, src: v ? (v.currentSrc || '') : '', stillSrc: st ? (st.currentSrc || st.src || '') : '',
    vrect: rect(v || st), objpos: objpos(v || st), W: innerWidth, H: innerHeight, copy: homeCopy(), copyAll: homeCopyAll(),
    caps: rect($(S.capsule)), bar: rect($(S.bar))}; }""")

SNAPSHOT_JS = J(r"""() => { __PRELUDE__
  const o = geom();
  o.dpr = devicePixelRatio; o.lang = document.documentElement.getAttribute('lang'); o.htmlClass = document.documentElement.className;
  o.backdrop = glassEls().map(desc);
  o.walk = rootWalk();
  o.glassShadow = glassEls().flatMap(e => outerShadows(cs(e).boxShadow).map(p => desc(e) + ': ' + p));
  o.drops = [];
  for (const e of $$('*')) {
    if (!e.getClientRects().length) continue;
    for (const p of [null, '::before', '::after']) {
      const s = cs(e, p);
      if (p && (s.content === 'none' || s.content === 'normal')) continue;
      for (const sh of outerShadows(s.boxShadow)) if (isDrop(sh)) o.drops.push(desc(e) + (p || '') + ': ' + sh);
      if (s.filter && /drop-shadow/.test(s.filter)) o.drops.push(desc(e) + (p || '') + ': filter ' + s.filter);
    }
  }
  const inv = $$('[aria-invalid="true"]');
  const errIds = new Set(inv.flatMap(f => ((f.getAttribute('aria-describedby') || '') + ' ' + (f.getAttribute('aria-errormessage') || '')).split(/\s+/).filter(Boolean)));
  const isErr = (e) => {
    for (const id of errIds) { const m = document.getElementById(id); if (m && (m === e || m.contains(e))) return true; }
    if (!inv.length) return false;
    if (e.matches('label') || e.closest('label')) return false;
    const boxes = inv.map(f => f.closest('.row, .group, .notify, fieldset, .field')).filter(Boolean);
    if (boxes.some(b => b.contains(e))) return true;
    const idc = (e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : '');
    return /err/i.test(idc);
  };
  o.colours = textElems().map(e => { const s = cs(e); return {d: desc(e), c: s.color, f: s.webkitTextFillColor, t: ownText(e).slice(0, 48), err: isErr(e), filled: !!e.closest(S.btn + ', ' + S.notify_go + ', .skip')}; });
  o.placeholders = $$('input[placeholder], textarea[placeholder]').filter(rendered).map(e => ({d: desc(e), c: cs(e, '::placeholder').color, t: e.placeholder}));
  o.fieldColours = $$('input:not([type=checkbox]):not([type=radio]):not([type=hidden]), textarea, select').filter(e => rendered(e) && !tiny(e)).map(e => ({d: desc(e), c: cs(e).color, f: cs(e).webkitTextFillColor}));
  o.type = [];
  for (const e of textElems().concat($$('input, textarea, select, button').filter(rendered))) {
    const s = cs(e), v = [], eb = !!e.closest(S.eyebrow);
    if (s.fontStyle !== 'normal') v.push('font-style ' + s.fontStyle);
    const w = Math.round(parseFloat(s.fontWeight));
    if (![400, 500, 600, 700].includes(w)) v.push('font-weight ' + s.fontWeight);
    if (s.textTransform !== 'none' && !eb) v.push('text-transform ' + s.textTransform);
    const ls = s.letterSpacing === 'normal' ? 0 : parseFloat(s.letterSpacing);
    if (ls > 0.01 && !eb) v.push('letter-spacing ' + s.letterSpacing);
    if (v.length) o.type.push(desc(e) + ' "' + (ownText(e) || e.value || '').slice(0, 30) + '": ' + v.join(', '));
  }
  o.eyebrows = $$(S.eyebrow).filter(rendered).map(e => [e.textContent.trim(), cs(e).textTransform]);
  o.text = document.body.innerText;
  o.attrs = $$('[alt], [aria-label], [title], [placeholder]').flatMap(e => ['alt', 'aria-label', 'title', 'placeholder'].filter(a => e.hasAttribute(a)).map(a => [desc(e), a, e.getAttribute(a)]));
  o.metas = $$('meta[content]').map(m => [m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv') || m.getAttribute('itemprop') || '', m.getAttribute('content')]);
  o.overflow = {doc: document.documentElement.scrollWidth, body: document.body.scrollWidth, sheet: $(S.scroll) ? [$(S.scroll).scrollWidth, $(S.scroll).clientWidth] : null};
  const INTER = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=switch], [role=tab], summary, [tabindex]:not([tabindex="-1"])';
  o.small = [];
  for (const e of $$(INTER)) {
    if (!rendered(e) && !(e.matches('input') && e.getClientRects().length && cs(e).visibility === 'visible')) continue;
    if ((e.textContent || '').trim() === S.skip_text) continue;
    let r = e.getBoundingClientRect(), who = e;
    if (e.matches('input[type=radio], input[type=checkbox]')) {
      const lab = (e.labels && e.labels[0]) || e.closest('label');
      if (lab) { const q = lab.getBoundingClientRect(); if (q.width * q.height > r.width * r.height) { r = q; who = lab; } }
    }
    if (e.matches('a') && cs(e).display === 'inline') { const p = e.parentElement; if (p && p.textContent.trim().length > e.textContent.trim().length + 2) continue; }
    if (r.width >= 43.5 && r.height >= 43.5) continue;
    if (r.width === 0 && r.height === 0) continue;
    o.small.push(desc(who) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height) + ' "' + ((e.textContent || '').trim() || e.getAttribute('aria-label') || e.name || '').slice(0, 24) + '"');
  }
  o.inputs = $$('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, select').filter(e => e.getClientRects().length).map(e => [desc(e), parseFloat(cs(e).fontSize)]);
  o.barTrunc = [];
  for (const a of $$(S.tab)) for (const e of [a].concat($$('*', a))) {
    if (e.closest('svg')) continue;
    if (e.clientWidth > 0 && (ownText(e) || e === a) && e.scrollWidth > e.clientWidth + 0.5 && rendered(e)) o.barTrunc.push(desc(e) + ' "' + e.textContent.trim() + '" ' + e.scrollWidth + '>' + e.clientWidth);
  }
  o.tabs = $$(S.tab).map(a => [a.textContent.trim(), rect(a), a.getAttribute('href')]);
  o.ks = $$('use').filter(u => /#p-k$/.test(u.getAttribute('href') || u.getAttribute('xlink:href') || ''))
    .filter(u => !u.closest(S.defs + ', defs, symbol, ' + S.wm_def))
    .map(u => { const svg = u.ownerSVGElement || u; return {d: desc(svg), rect: rect(svg), use: rect(u), shown: rendered(svg), under: kUnder(svg)}; });
  o.kImgs = $$('img').filter(i => /k\.svg/.test(i.getAttribute('src') || '')).map(i => ({d: desc(i), rect: rect(i), shown: rendered(i), under: kUnder(i)}));
  const cap = $(S.capsule);
  o.capsule = cap ? {text: cap.textContent.replace(/\s+/g, ' ').trim(), wm: !!cap.querySelector('use[href="#p-wm"], use[*|href="#p-wm"]'), label: cap.getAttribute('aria-label')} : null;
  o.wmText = !!$(S.wm_def + ' text');
  o.phCount = $$(S.ph).length;
  return o;
}""")

CONTRAST_TARGETS_JS = J(r"""() => { __PRELUDE__
  const clipOf = (e) => { let r = [0, 0, innerWidth, innerHeight];
    for (let a = e.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const s = cs(a);
      if (/(hidden|auto|scroll|clip)/.test(s.overflowX + ' ' + s.overflowY)) {
        const q = a.getBoundingClientRect(); const l = q.left + a.clientLeft, t = q.top + a.clientTop;
        r = [Math.max(r[0], l), Math.max(r[1], t), Math.min(r[2], l + a.clientWidth), Math.min(r[3], t + a.clientHeight)];
      }
    } return r; };
  // A length expression (px, %, calc) along a box of `total` px, resolved by the engine itself.
  const resolveLen = (expr, total) => {
    const host = document.createElement('div'), kid = document.createElement('div');
    Object.assign(host.style, {position: 'fixed', left: '0', top: '0', width: total + 'px', height: total + 'px', visibility: 'hidden', pointerEvents: 'none'});
    Object.assign(kid.style, {position: 'absolute', left: '0', top: expr});
    host.append(kid); document.documentElement.append(host);
    const v = parseFloat(cs(kid).top); host.remove(); return v;
  };
  // Where an element's mask is fully opaque, as intervals on one axis in viewport px.
  // null = no mask (or a radial lens hole, which the copy clamp keeps clear); 'unknown' = not parsed.
  const maskBand = (a) => {
    const s = cs(a); const m = (s.maskImage && s.maskImage !== 'none') ? s.maskImage : ((s.webkitMaskImage && s.webkitMaskImage !== 'none') ? s.webkitMaskImage : null);
    if (!m) return null;
    const layers = splitTop(m);
    if (layers.length === 1 && /^radial-gradient\(/.test(layers[0])) return null;
    if (layers.length !== 1 || !/^linear-gradient\(/.test(layers[0])) return 'unknown';
    const inner = layers[0].slice(layers[0].indexOf('(') + 1, layers[0].lastIndexOf(')'));
    const args = splitTop(inner);
    let axis = 'y', rev = false;
    const dir = args[0].trim();
    if (/^to\s|deg$|turn$|rad$/.test(dir)) {
      args.shift();
      if (dir === 'to bottom' || dir === '180deg') { axis = 'y'; rev = false; }
      else if (dir === 'to top' || dir === '0deg') { axis = 'y'; rev = true; }
      else if (dir === 'to right' || dir === '90deg') { axis = 'x'; rev = false; }
      else if (dir === 'to left' || dir === '270deg') { axis = 'x'; rev = true; }
      else return 'unknown';
    }
    const r = a.getBoundingClientRect(); const total = axis === 'y' ? r.height : r.width; const start = axis === 'y' ? r.top : r.left;
    const stops = args.map(t => { t = t.trim(); const mm = t.match(/^(rgba?\([^)]*\)|color\([^)]*\)|transparent|#[0-9a-f]{3,8}|[a-z]+)\s*(.*)$/i); if (!mm) return null;
      const al = mm[1] === 'transparent' ? 0 : ((rgba(mm[1]) || [0, 0, 0, 1])[3]); return {a: al, p: mm[2].trim() ? resolveLen(mm[2].trim(), total) : null}; });
    if (!stops.length || stops.some(x => !x)) return 'unknown';
    if (stops[0].p === null) stops[0].p = 0;
    if (stops[stops.length - 1].p === null) stops[stops.length - 1].p = total;
    for (let i = 1; i < stops.length - 1; i++) if (stops[i].p === null) { let j = i; while (stops[j].p === null) j++; stops[i].p = stops[i - 1].p + (stops[j].p - stops[i - 1].p) / (j - i + 1); }
    for (let i = 1; i < stops.length; i++) stops[i].p = Math.max(stops[i].p, stops[i - 1].p);
    let pts = stops.map(x => ({v: start + (rev ? total - x.p : x.p), a: x.a}));
    if (rev) pts = pts.reverse();
    const iv = []; const op = (x) => x >= 0.99;
    let cur = op(pts[0].a) ? -1e9 : null;
    for (let i = 1; i < pts.length; i++) {
      const both = op(pts[i - 1].a) && op(pts[i].a);
      if (both && cur === null) cur = pts[i - 1].v;
      if (!both && cur !== null) { iv.push([cur, pts[i - 1].v]); cur = null; }
    }
    if (op(pts[pts.length - 1].a)) iv.push([cur === null ? pts[pts.length - 1].v : cur, 1e9]); else if (cur !== null) iv.push([cur, pts[pts.length - 1].v]);
    return {axis: axis, iv: iv, by: desc(a)};
  };
  const bandsOf = (e) => { const out = []; for (let a = e; a && a !== document.documentElement; a = a.parentElement) { const b = maskBand(a); if (b) out.push(b); } return out; };
  // Something that paints, stacked over the line (a chip, a caption): the diff there is not this text.
  const paints = (el) => { const s = cs(el); const bg = rgba(s.backgroundColor); const bc = rgba(s.borderTopColor) || [0, 0, 0, 0];
    return (bg && bg[3] > 0.02) || s.backgroundImage !== 'none' || /^(img|svg|video|canvas)$/i.test(el.tagName) || !!ownText(el) || (parseFloat(s.borderTopWidth) > 0 && bc[3] > 0.05); };
  const occluder = (e, q) => {
    const y = (q[1] + q[3]) / 2;
    for (let k = 0; k < 7; k++) {
      const x = q[0] + (q[2] - q[0]) * (k + 0.5) / 7;
      if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
      const h = document.elementFromPoint(x, y);
      if (!h || h === e || e.contains(h) || h.contains(e)) continue;
      for (let a = h; a && !a.contains(e); a = a.parentElement) if (paints(a)) return desc(a);
    }
    return null;
  };
  const cover = [$(S.bar), $(S.capsule)].filter(Boolean).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; });
  const hit = (q, c) => q[0] < c[2] && q[2] > c[0] && q[1] < c[3] && q[3] > c[1];
  const out = [], all = [], skipped = {};
  const skip = (why) => { skipped[why] = (skipped[why] || 0) + 1; };
  for (const root of [$(S.page), $(S.bar)].filter(Boolean)) for (const e of textElems(root)) {
    if (e.closest('[disabled], [aria-disabled="true"]')) continue;
    const go = e.closest(S.notify_go); if (go && cs(go).pointerEvents === 'none') continue;
    const inBar = !!e.closest(S.bar);
    const key = desc(e) + ' "' + ownText(e).slice(0, 36) + '"';
    const c = clipOf(e);
    const lines = lineRects(e);
    // a line counts as reachable text if any of it is inside its scroll/overflow clip
    if (lines.some(q => Math.min(q[2], c[2]) - Math.max(q[0], c[0]) > 1 && Math.min(q[3], c[3]) - Math.max(q[1], c[1]) > 1)) all.push(key);
    const bands = bandsOf(e).filter(b => b !== 'unknown');
    let rs = [];
    for (const q of lines) {
      const h = q[3] - q[1];
      // cut by a scroll edge by more than a quarter of its height: not a reading position
      const cut = Math.max(0, c[1] - q[1]) + Math.max(0, q[3] - c[3]);
      if (cut > 0.25 * h || q[0] < c[0] - 0.25 * h || q[2] > c[2] + 0.25 * h) { if (hit(q, c)) skip('cut by a scroll edge'); continue; }
      // measure only inside the clip, 2px in from its edge (the sheet's specular rim lives on the edge)
      let r = [Math.max(q[0], c[0] + 2), Math.max(q[1], c[1] + 2), Math.min(q[2], c[2] - 2), Math.min(q[3], c[3] - 2)];
      // and only where every ancestor mask is fully opaque; a line mostly in a fade is not read
      let ok = true;
      for (const b of bands) {
        const lo = b.axis === 'y' ? r[1] : r[0], hi = b.axis === 'y' ? r[3] : r[2];
        let best = null;
        for (const [a0, a1] of b.iv) { const s0 = Math.max(lo, a0), s1 = Math.min(hi, a1); if (s1 > s0 && (!best || s1 - s0 > best[1] - best[0])) best = [s0, s1]; }
        if (!best || (best[1] - best[0]) < 0.5 * (hi - lo)) { ok = false; skip('in a mask fade (' + b.by + ')'); break; }
        if (b.axis === 'y') { r[1] = best[0]; r[3] = best[1]; } else { r[0] = best[0]; r[2] = best[1]; }
      }
      if (!ok || r[2] - r[0] <= 1 || r[3] - r[1] <= 1) continue;
      if (!inBar && cover.some(k => hit(r, k))) { skip('under the bar or capsule'); continue; }
      const oc = occluder(e, r); if (oc) { skip('under ' + oc); continue; }
      rs.push(r);
    }
    if (!rs.length) continue;
    const s = cs(e);
    const fill = (s.webkitTextFillColor && s.webkitTextFillColor !== s.color) ? s.webkitTextFillColor : s.color;
    out.push({key: key, color: rgba(fill), fs: parseFloat(s.fontSize), fw: parseFloat(s.fontWeight), bar: inBar, rects: rs});
  }
  return {targets: out, all: all, skipped: skipped}; }""")

SEEK_JS = J("""async (t) => { __PRELUDE__
  const vids = $$(S.film + ' video').filter(v => v.currentSrc);
  await Promise.all(vids.map(v => new Promise(res => {
    try { v.playbackRate = 0; } catch (e) {}
    let done = false; const fin = () => { if (done) return; done = true; v.removeEventListener('seeked', fin); res(); };
    v.addEventListener('seeked', fin); setTimeout(fin, 3000);
    const d = isFinite(v.duration) && v.duration > 0 ? v.duration : 16;
    v.currentTime = Math.max(0, Math.min(t % d, d - 0.05));
  })));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r))));
  return vids.map(v => v.currentTime); }""")

UNFREEZE_JS = J("""() => { __PRELUDE__ for (const v of $$(S.film + ' video')) { try { v.playbackRate = 1; } catch (e) {} } return true; }""")

BRIGHT_SCAN_JS = J("""async (step) => { __PRELUDE__
  const v = $(S.video); if (!v || !v.currentSrc) return null;
  if (v.readyState < 1) await new Promise(r => { v.addEventListener('loadedmetadata', r, {once: true}); setTimeout(r, 5000); });
  const d = isFinite(v.duration) && v.duration > 0 ? v.duration : 16;
  const c = document.createElement('canvas'); c.width = 48; c.height = 48; const g = c.getContext('2d', {willReadFrequently: true});
  try { v.playbackRate = 0; } catch (e) {}
  const out = [];
  for (let t = 0; t < d - 0.01; t += step) {
    await new Promise(res => { let done = false; const fin = () => { if (done) return; done = true; v.removeEventListener('seeked', fin); res(); };
      v.addEventListener('seeked', fin); setTimeout(fin, 3000); v.currentTime = t; });
    g.drawImage(v, 0, 0, 48, 48); const px = g.getImageData(0, 0, 48, 48).data; let s = 0;
    for (let i = 0; i < px.length; i += 4) s += .2126 * px[i] + .7152 * px[i + 1] + .0722 * px[i + 2];
    out.push([t, s / (px.length / 4)]);
  }
  return {d: d, lum: out}; }""")

REC_GLASS_JS = J("""([ms]) => { __PRELUDE__ window.__e2eGlass = {done: false, max: 0, at: ''};
  const t0 = performance.now();
  const tick = () => { const g = glassEls(); if (g.length > window.__e2eGlass.max) { window.__e2eGlass.max = g.length; window.__e2eGlass.at = g.map(desc).join(', '); }
    if (performance.now() - t0 < ms) requestAnimationFrame(tick); else window.__e2eGlass.done = true; };
  requestAnimationFrame(tick); return true; }""")

REC_RECT_JS = J("""([ms]) => { __PRELUDE__ window.__e2eRect = {done: false, bad: [], n: 0};
  const t0 = performance.now();
  const tick = () => { const v = $(S.video) || $(S.still); const r = v.getBoundingClientRect(); window.__e2eRect.n++;
    if (r.left > 0.5 || r.top > 0.5 || r.right < innerWidth - 0.5 || r.bottom < innerHeight - 0.5)
      window.__e2eRect.bad.push([performance.now() - t0, r.left, r.top, r.right, r.bottom].map(x => Math.round(x)));
    if (performance.now() - t0 < ms) requestAnimationFrame(tick); else window.__e2eRect.done = true; };
  requestAnimationFrame(tick); return true; }""")

REC_CAM_JS = J("""([ms]) => { __PRELUDE__ window.__e2eCam = {done: false, vals: []};
  const t0 = performance.now();
  const tick = () => { const c = $(S.cam); const v = c ? cs(c).transform : 'none'; const a = window.__e2eCam.vals;
    if (!a.length || a[a.length - 1][1] !== v) a.push([Math.round(performance.now() - t0), v]);
    if (performance.now() - t0 < ms) requestAnimationFrame(tick); else window.__e2eCam.done = true; };
  requestAnimationFrame(tick); return true; }""")

ANIM_REC_BODY = """
  const rec = (window.__e2eAn = window.__e2eAn || {seen: {}});
  const t0 = performance.now();
  const tick = () => {
    for (const a of document.getAnimations()) {
      const ef = a.effect; if (!ef) continue; const tm = ef.getTiming();
      const dur = typeof tm.duration === 'number' ? tm.duration : 0; const t = ef.target;
      const k = (t ? (t.tagName.toLowerCase() + (t.id ? '#' + t.id : '') + (typeof t.className === 'string' && t.className.trim() ? '.' + t.className.trim().split(/\\s+/).slice(0, 2).join('.') : '')) : '?')
        + (ef.pseudoElement || '') + ' ' + (a.animationName || a.transitionProperty || a.id || 'waapi') + (tm.iterations === Infinity ? ' (infinite)' : '');
      rec.seen[k] = Math.max(rec.seen[k] || 0, dur);
    }
    if (performance.now() - t0 < MS) requestAnimationFrame(tick); else rec.done = true;
  };
  requestAnimationFrame(tick);
"""
ANIM_REC_INIT = "(() => {" + ANIM_REC_BODY.replace("MS", "9000") + "})();"
ANIM_REC_JS = "([ms]) => { window.__e2eAn = {seen: {}}; const MS = ms;" + ANIM_REC_BODY.replace("MS", "ms") + " return true; }"

TRANS_SCAN_JS = J("""() => { __PRELUDE__ const bad = [];
  const secs = (v) => (v || '0s').split(',').map(x => { x = x.trim(); return x.endsWith('ms') ? parseFloat(x) / 1000 : (parseFloat(x) || 0); });
  for (const e of $$('*')) { if (!e.getClientRects().length) continue;
    for (const p of [null, '::before', '::after']) {
      const s = cs(e, p); if (p && (s.content === 'none' || s.content === 'normal')) continue;
      const mt = Math.max(...secs(s.transitionDuration)); const an = s.animationName; const ma = (an && an !== 'none') ? Math.max(...secs(s.animationDuration)) : 0;
      if (mt > 0.2001) bad.push(desc(e) + (p || '') + ' transition ' + s.transitionProperty + ' ' + s.transitionDuration);
      if (ma > 0.2001) bad.push(desc(e) + (p || '') + ' animation ' + an + ' ' + s.animationDuration);
    } }
  return bad; }""")

CHECKER_ADD_JS = J("""() => { __PRELUDE__ if (document.getElementById('__e2e_checker')) return true;
  const d = document.createElement('div'); d.id = '__e2e_checker';
  Object.assign(d.style, {position: 'fixed', left: '0', top: '0', right: '0', bottom: '0', zIndex: '-1', pointerEvents: 'none',
    backgroundImage: 'repeating-conic-gradient(#F4F2ED 0 25%, #020202 0 50%)', backgroundSize: '8px 8px'});
  const f = $(S.film); if (f) f.after(d); else document.body.prepend(d); return true; }""")
CHECKER_DEL_JS = "() => { const d = document.getElementById('__e2e_checker'); if (d) d.remove(); return true; }"


def css_join(sels, suffix=""):
    return ", ".join(s + suffix for s in sels)


GLASS_CONTENT_OFF_CSS = (
    f"{SEL['scroll']}, {SEL['grab']}, {SEL['thumb']}, {SEL['lens']}, {SEL['toast']} {{ visibility: hidden !important; }}\n"
    f"{SEL['bar']}, {SEL['bar']} *, {SEL['capsule']}, {SEL['capsule']} * {{ color: transparent !important; -webkit-text-fill-color: transparent !important; }}\n"
    f"{SEL['capsule']} svg {{ visibility: hidden !important; }}\n"
    f"{css_join(SEL['glass'], '::before')}, {css_join(SEL['glass'], '::after')} {{ opacity: 0 !important; }}\n"
)
GLASS_HIDE_CSS = f"{css_join(SEL['glass'])} {{ visibility: hidden !important; }}\n"
NOGLYPH_CSS = (
    "*, *::before, *::after { color: transparent !important; -webkit-text-fill-color: transparent !important; "
    "text-shadow: none !important; text-decoration-color: transparent !important; caret-color: transparent !important; "
    "transition: none !important; }\n::placeholder { color: transparent !important; }\n"
)
MASK_OFF_CSS = f"{SEL['sheet']} {{ -webkit-mask-image: none !important; mask-image: none !important; }}\n{SEL['lens']} {{ visibility: hidden !important; }}\n"
# The frost-free reference turns the filter off. (visibility:hidden on the sheet is NOT enough while its
# children stay visible: Chrome keeps the backdrop-filter effect node alive and the film stays frosted.)
FROST_OFF_CSS = f"{SEL['sheet']} {{ backdrop-filter: none !important; -webkit-backdrop-filter: none !important; background: none !important; box-shadow: none !important; }}\n"


# --------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------
def http(url, method="GET", headers=None, timeout=20):
    try:
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "taboukhi-e2e", **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, (r.read() if method != "HEAD" else b"")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b""
    except Exception as e:  # network, DNS, refused
        return 0, {}, str(e).encode()


def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def parse_rgba(s):
    if not s:
        return None
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        p = [float(x) for x in re.split(r"[\s,/]+", m.group(1).strip()) if x]
        return (p[0], p[1], p[2], p[3] if len(p) > 3 else 1.0)
    m = re.match(r"color\(srgb\s+([^)]*)\)", s)
    if m:
        p = [float(x) for x in re.split(r"[\s/]+", m.group(1).strip()) if x]
        return (p[0] * 255, p[1] * 255, p[2] * 255, p[3] if len(p) > 3 else 1.0)
    return None


def close(c, ref, tol=2.5, atol=0.021):
    return c is not None and all(abs(c[i] - ref[i]) <= tol for i in range(3)) and abs(c[3] - ref[3]) <= atol


def colour_name(c):
    for n, ref in TEXT_OK.items():
        if close(c, ref):
            return n
    if close(c, ONYX):
        return "onyx"
    if close(c, ERROR):
        return "error"
    return None


def split_selectors(prelude):
    out, depth, cur = [], 0, ""
    for ch in prelude:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [re.sub(r"\s+", " ", s) for s in out]


def css_rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []

    def walk(s, ctx):
        i, n = 0, len(s)
        while i < n:
            j = s.find("{", i)
            if j < 0:
                break
            prelude = s[i:j]
            if ";" in prelude:
                prelude = prelude.rsplit(";", 1)[1]
            prelude = prelude.strip()
            depth, k = 1, j + 1
            while k < n and depth:
                if s[k] == "{":
                    depth += 1
                elif s[k] == "}":
                    depth -= 1
                k += 1
            body = s[j + 1:k - 1]
            if prelude.startswith("@"):
                if re.match(r"@(media|supports|layer|container|document|scope)\b", prelude):
                    walk(body, ctx + [prelude])
            else:
                out.append((prelude, body, ctx))
            i = k

    walk(css, [])
    return out


def css_backdrop_selectors(css):
    sels = set()
    for prelude, body, ctx in css_rules(css):
        for m in re.finditer(r"(?:^|[;{\s])(?:-webkit-)?backdrop-filter\s*:\s*([^;}]+)", body):
            if m.group(1).strip().lower().startswith("none"):
                continue
            for s in split_selectors(prelude):
                sels.add(s)
    return sorted(sels)


def strip_js_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(?<![:'\"\\\w])//.*$", "", src)


STR_RE = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`")
IMPORT_RE = re.compile(r"""(?:^|[;\s}])(?:import\s*(?:[\w$*{}\s,]+?\s*from\s*)?|export\s+(?:\*|\{[^}]*\})\s*from\s*)['"]([^'"]+)['"]""", re.M)


class Grab(html.parser.HTMLParser):
    """Text nodes (outside script/style), attributes, metas and every link in a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.attrs, self.metas, self.links, self.tags = [], [], [], [], []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        self.tags.append((tag, a))
        if tag in ("script", "style"):
            self._skip += 1
        for k in ("alt", "aria-label", "title", "placeholder"):
            if a.get(k):
                self.attrs.append((tag, k, a[k]))
        if tag == "meta" and "content" in a:
            self.metas.append((a.get("name") or a.get("property") or a.get("http-equiv") or "", a["content"]))
        for k in ("href", "src", "data-sq", "poster", "action", "xlink:href", "data-src"):
            if k in a and a[k] != "":
                self.links.append((tag, k, a[k], a))
        if a.get("srcset"):
            for part in a["srcset"].split(","):
                u = part.strip().split(" ")[0]
                if u:
                    self.links.append((tag, "srcset", u, a))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, d):
        if not self._skip and d.strip():
            self.text.append(d)


def grab(html_text):
    g = Grab()
    g.feed(html_text)
    return g


def img_of(png):
    im = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def luma(img):
    f = img.astype(np.float64)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def lap_var(img, box=None, mask=None):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float64)
    lap = cv2.Laplacian(g, cv2.CV_64F, ksize=3)
    if box is not None:
        x0, y0, x1, y1 = box
        lap = lap[y0:y1, x0:x1]
        if mask is not None:
            mask = mask[y0:y1, x0:x1]
    vals = lap[mask] if mask is not None else lap.ravel()
    return float(vals.var()) if vals.size >= 64 else float("nan")


def dev_box(css_box, scale, shape):
    """CSS-px box [x0,y0,x1,y1] to a device-px box clipped to the image."""
    x0, y0, x1, y1 = css_box
    h, w = shape[:2]
    return (max(0, int(math.floor(x0 * scale))), max(0, int(math.floor(y0 * scale))),
            min(w, int(math.ceil(x1 * scale))), min(h, int(math.ceil(y1 * scale))))


def srgb_lum(rgb):
    c = rgb / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


def glyph_contrast(A, B, rects, colour, scale):
    """Worst contrast over fully inked stroke centres (brand book p16).

    A is the render, B the same frame with glyphs transparent. A pixel is fully inked when
    A - B equals alpha * (text - B), i.e. its coverage estimate is ~1. Its colour is taken from A,
    the colour behind it from B."""
    if colour is None or colour[3] <= 0.01:
        return None, 0, "transparent"
    T = np.array(colour[:3], np.float64)
    a = float(colour[3])
    worst, n, faint = None, 0, None
    for q in rects:
        x0, y0, x1, y1 = dev_box(q, scale, A.shape)
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        pa = A[y0:y1, x0:x1].astype(np.float64)
        pb = B[y0:y1, x0:x1].astype(np.float64)
        tb = T - pb
        den = a * (tb ** 2).sum(-1)
        num = ((pa - pb) * tb).sum(-1)
        cov = np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)
        sig = np.sqrt((tb ** 2).sum(-1)) * a
        m = (cov >= 0.95) & (cov <= 1.15) & (sig >= 12)
        if m.any():
            la, lb = srgb_lum(pa[m]), srgb_lum(pb[m])
            r = (np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)
            w = float(r.min())
            worst = w if worst is None else min(worst, w)
            n += int(m.sum())
        else:
            d = np.abs(pa - pb).max(-1)
            if (d > 8).any():
                k = d >= np.percentile(d[d > 8], 90)
                la, lb = srgb_lum(pa[k]), srgb_lum(pb[k])
                r = float(((np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)).min())
                faint = r if faint is None else min(faint, r)
    if worst is not None:
        return worst, n, "inked"
    if faint is not None:
        return faint, 0, "faint (no fully inked pixel)"
    return 1.0, 0, "invisible (no glyph pixels at all)"


def track_at(track, t):
    pts = track["pts"]
    n = track.get("frames", len(pts)) or len(pts)
    f = (t * track.get("fps", 24)) % n
    i0 = int(math.floor(f))
    a = f - i0
    p0, p1 = pts[i0 % len(pts)], pts[(i0 + 1) % len(pts)]
    return [p0[k] + (p1[k] - p0[k]) * a for k in range(3)]


def enc_of(film, media, W, H):
    src = (film.get("src") or "") + " " + (film.get("stillSrc") or "")
    if "film-pt" in src:
        return media["pt"]
    if "film-sq" in src:
        return media["sq"]
    return media["pt"] if W / H <= 0.8 else media["sq"]


def to_screen(fx, fy, film, media, W, H):
    """A master-frame point to screen px, through the encode crop, object-fit: cover and the camera."""
    enc = enc_of(film, media, W, H)
    x0, y0, cw, ch = enc["crop"]
    u, v = (fx - x0) / cw, (fy - y0) / ch
    L, T, RW, RH = film["v"] or film["still"]
    s = max(RW / enc["w"], RH / enc["h"])
    dw, dh = enc["w"] * s, enc["h"] * s
    px, py = (film.get("objpos") or [0.5, 0.5])[:2]
    return L + (RW - dw) * px + u * dw, T + (RH - dh) * py + v * dh, dw / cw


def rect_dist(x, y, r):
    l, t, rr, b = r
    dx = max(l - x, 0, x - rr)
    dy = max(t - y, 0, y - b)
    return math.hypot(dx, dy)


def covers(rect_, W, H, tol=0.5):
    if not rect_:
        return False
    L, T, RW, RH = rect_
    return L <= tol and T <= tol and L + RW >= W - tol and T + RH >= H - tol


def expected_sheet_rect(W, H, state, det=None):
    """SPEC C5. Phone Peek/Glance: the spec heights are the MINIMUM since T-14 — the page grows them
    to show its [data-peek] block / title whole (capped at 70% / 50% of H). Pass the live detents
    (geom()["sheet"]["det"]) to get the offset the page chose; detents_ok() bounds that choice."""
    if W >= 720:
        gutter = 40 if W >= 1080 else 28
        top = 24 if W >= 1080 else 12
        w = min(max(400, 0.32 * W), 460)
        return [W - gutter - w, top, w, H - 2 * top]
    top, bottom = 64, 12 + 56 + 8
    hs = H - top - bottom
    off = {"full": 0, "peek": hs - min(max(300, 0.46 * H), 420), "glance": hs - 148}.get(state, 0)
    if det and state in det:
        off = det[state]
    return [8, top + off, W - 16, hs]


def detents_ok(W, H, det):
    """The page's own detents stay inside T-14's bounds: Peek shows at least the spec height and at
    most 70% of H, Glance at least 148 px and at most half of H."""
    hs = H - 64 - (12 + 56 + 8)
    spec_peek = min(max(300, 0.46 * H), 420)
    pv, gv = hs - det["peek"], hs - det["glance"]
    ok = (spec_peek - 1 <= pv <= max(spec_peek, 0.70 * H) + 1) and (147 <= gv <= max(148, 0.5 * H) + 1) and det["full"] == 0
    return ok, f"peek shows {pv:.0f} (spec {spec_peek:.0f}, cap {0.7 * H:.0f}), glance shows {gv:.0f} (min 148, cap {0.5 * H:.0f})"


def panel_rect_ok(got, W, H, bar, tol=1.5, det=None):
    """The inner-page sheet box against SPEC C5. Panel (>= 720px): top/right/bottom var(--top), width
    clamp(400px, 32vw, 460px). Accepted variant: where the centred bar would come within 24px of the
    panel, the panel may stand on the bar (bottom edge 12px above the bar's top) so they never overlap
    (SPEC §5 "it never overlaps the panel"). Returns (ok, note)."""
    exp = expected_sheet_rect(W, H, "peek", det)
    same = lambda a, b: all(abs(a[i] - b[i]) <= tol for i in range(4))
    if same(got, exp):
        return True, "spec rect"
    if W >= 720 and bar:
        bl, bt, bw, bh = bar
        near = bl + bw > exp[0] - 24
        on_bar = [exp[0], exp[1], exp[2], (bt - 12) - exp[1]]
        if near and same(got, on_bar):
            return True, f"panel stands on the bar (bar right {bl + bw:.0f} vs panel left {exp[0]:.0f}; spec bottom would be {exp[1] + exp[3]:.0f}, got {got[1] + got[3]:.0f})"
    return False, f"{[round(x, 1) for x in got]} vs spec {[round(x, 1) for x in exp]}"


def free_area(g):
    W, H = g["W"], g["H"]
    caps = g["caps"] or [0, 12, 0, 44]
    sh = g["sheet"]["rect"] if g.get("sheet") else [W, 0, 0, H]
    if W >= 720:
        return [0, caps[1] + 44, sh[0] - 24, H - 104]
    return [0, caps[1] + caps[3] + 12, W, sh[1] - 12]


class Result:
    def __init__(self):
        self.subs = []

    def add(self, ok, label, detail=""):
        self.subs.append((bool(ok), label, detail))
        return bool(ok)

    @property
    def ok(self):
        return bool(self.subs) and all(s[0] for s in self.subs)

    def reason(self, summary=""):
        if not self.subs:
            return "nothing was asserted"
        bad = [s for s in self.subs if not s[0]]
        if bad:
            b = bad[0]
            txt = b[1] + (": " + str(b[2]) if b[2] else "")
            txt = txt.replace("\n", " ")
            if len(txt) > 300:
                txt = txt[:297] + "..."
            return txt + (f"  (+{len(bad) - 1} more)" if len(bad) > 1 else "")
        return summary or f"{len(self.subs)} assertions"


async def soft_wait(page, expr, timeout, arg=None):
    try:
        if arg is None:
            await page.wait_for_function(expr, timeout=timeout)
        else:
            await page.wait_for_function(expr, arg=arg, timeout=timeout)
        return True
    except Exception:
        return False


async def shot(page, clip=None):
    png = await page.screenshot(clip=clip) if clip else await page.screenshot()
    return img_of(png)


class _Style:
    def __init__(self, page, sid):
        self.page, self.sid = page, sid

    async def remove(self):
        await self.page.evaluate("id => { const e = document.getElementById(id); if (e) e.remove(); }", self.sid)


async def inject_style(page, css):
    """A test-only <style>, added through evaluate (works with JavaScript disabled, unlike add_style_tag)."""
    sid = "__e2e_style_%d" % int(time.time() * 1e6)
    await page.evaluate("([id, css]) => { const s = document.createElement('style'); s.id = id; s.textContent = css; document.head.append(s); "
                        "return getComputedStyle(document.documentElement).color; }", [sid, css])
    return _Style(page, sid)


async def stable(page, sels, frames=8, timeout=4000):
    return await page.evaluate(STABLE_JS, [sels, frames, timeout])


class Touch:
    """Real touch input through CDP (Input.dispatchTouchEvent): pointer events of type touch."""

    def __init__(self, cdp):
        self.cdp = cdp

    @classmethod
    async def of(cls, page):
        t = getattr(page, "_e2e_touch", None)
        if t is None:
            t = cls(await page.context.new_cdp_session(page))
            page._e2e_touch = t
        return t

    async def _send(self, typ, pts):
        await self.cdp.send("Input.dispatchTouchEvent", {"type": typ, "touchPoints": pts})

    async def down(self, x, y):
        await self._send("touchStart", [{"x": x, "y": y, "id": 1, "radiusX": 3, "radiusY": 3, "force": 1}])

    async def move(self, x, y):
        await self._send("touchMove", [{"x": x, "y": y, "id": 1, "radiusX": 3, "radiusY": 3, "force": 1}])

    async def up(self):
        await self._send("touchEnd", [])

    async def cancel(self):
        await self._send("touchCancel", [])

    async def drag(self, x0, y0, x1, y1, steps=12, dt=0.016, release=True, settle_before_release=0.0):
        await self.down(x0, y0)
        for i in range(1, steps + 1):
            await asyncio.sleep(dt)
            await self.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        if settle_before_release:
            await asyncio.sleep(settle_before_release)
            await self.move(x1, y1)
            await asyncio.sleep(0.02)
            await self.move(x1, y1)
        if release:
            await self.up()


async def mouse_path(page, x0, y0, x1, y1, steps=12, dt=0.016, press=False):
    await page.mouse.move(x0, y0)
    if press:
        await page.mouse.down()
    for i in range(1, steps + 1):
        await asyncio.sleep(dt)
        await page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    if press:
        await page.mouse.up()


async def robust_click(loc, timeout=3000):
    """A real click; if the control is visually hidden (a 1px radio, an overlaid input), a DOM click."""
    try:
        await loc.click(timeout=timeout)
    except Exception:
        await loc.evaluate("e => e.click()")


def frost_box(info, W, H, size=96):
    """A square of frost that avoids the lens disc, the copy and the chrome."""
    size = min(size, W * 0.3)
    avoid = [[r[0], r[1], r[0] + r[2], r[1] + r[3]] for r in (info.get("caps"), info.get("bar")) if r] + list(info.get("copy") or [])
    for fy in (H * 0.12, H * 0.3, H * 0.5, H * 0.66):
        for fx in (W - size - 16, 16, W / 2 - size / 2):
            b = [fx, fy, fx + size, fy + size]
            if rect_dist(info["x"], info["y"], b) < info["rw"] + 8:
                continue
            if any(not (b[2] < q[0] - 8 or b[0] > q[2] + 8 or b[3] < q[1] - 8 or b[1] > q[3] + 8) for q in avoid):
                continue
            return b
    return [16, H * 0.12, 16 + size, H * 0.12 + size]


# --------------------------------------------------------------------------------------
# Runner: server, browser, contexts, shared sweeps
# --------------------------------------------------------------------------------------
CHECKS = {}


def check(num, title, timeout=420):
    def deco(fn):
        CHECKS[num] = dict(fn=fn, title=title, timeout=timeout)
        return fn
    return deco


class Runner:
    def __init__(self, base, live, vp_filter, verbose):
        self.base = base
        self.live = live
        self.origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(base))
        self.vp_filter = vp_filter
        self.verbose = verbose
        self.browser = None
        self.media = None
        self.track = None
        self.track_mean = None
        self._sweeps = {}
        self._forms = {}
        self._sem = asyncio.Semaphore(4)
        self._http = {}
        self.console = []

    # ---- viewports
    def vps(self, wanted):
        return [v for v in wanted if v in self.vp_filter]

    # ---- http
    def get(self, path_or_url, method="GET", headers=None):
        url = path_or_url if re.match(r"https?://", path_or_url) else self.base + path_or_url
        key = (url, method, json.dumps(headers or {}, sort_keys=True))
        if key not in self._http:
            self._http[key] = http(url, method, headers)
        return self._http[key]

    def text(self, path):
        st, _, body = self.get(path)
        return body.decode("utf-8", "replace") if st == 200 else ""

    def load_media(self):
        st, _, body = self.get("assets/media/media.json")
        self.media = json.loads(body) if st == 200 else None
        st, _, body = self.get("assets/media/k-track.json")
        self.track = json.loads(body) if st == 200 else None
        if self.track:
            pts = self.track["pts"]
            self.track_mean = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def station_point(self, station, g=None):
        """SPEC C1 station points, with T-20's two framing rules: on wide desktops (>= 1080) Collection
        aims at (0.335, 0.44); on panel layouts House and Commission take Collection's framing when the
        free area beside the panel is narrower than 1.15x the engraved name at their own zoom."""
        pt = STATION_PT.get(station) or self.track_mean
        if g is None:
            return pt
        W, H = g["W"], g["H"]
        if station == "collection" and W >= 1080:
            return (0.335, 0.44)
        z_st = {"house": 1.2, "commission": 1.35}.get(station)
        if z_st and W >= 720 and self.track and self.media and g.get("sheet"):
            film = g["film"]
            rect_ = film["v"] or film["still"]
            enc = enc_of(film, self.media, W, H)
            w_rest = max(W, H * enc["w"] / enc["h"])
            z_now = rect_[2] / w_rest
            _, _, per = to_screen(0.5, 0.5, film, self.media, W, H)
            ew = max(q[2] for q in self.track["pts"]) * per / z_now * min(z_st, 1.35)
            fa = free_area(g)
            if fa[2] - fa[0] < 1.15 * ew:
                return STATION_PT["collection"]
        return pt

    # ---- contexts and pages
    async def ctx(self, vp, reduced_motion=None, js=True, dpr=None, permissions=None, touch=None):
        v = VIEWPORTS[vp]
        kw = dict(viewport={"width": v["w"], "height": v["h"]}, device_scale_factor=dpr or v["dpr"],
                  is_mobile=v["touch"], has_touch=v["touch"] if touch is None else touch,
                  service_workers="block", java_script_enabled=js, bypass_csp=True)
        if v["touch"]:
            kw["user_agent"] = IPAD_UA if v["w"] >= 720 else IPHONE_UA
        if reduced_motion:
            kw["reduced_motion"] = reduced_motion
        c = await self.browser.new_context(**kw)
        c.set_default_timeout(15000)
        if permissions:
            await c.grant_permissions(permissions, origin=self.origin)
        if not self.live:
            await c.route(re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?/taboukhi-site/.*"), self._pages_emulation)
        return c

    async def _pages_emulation(self, route):
        """Locally, 404.html's BASE_PATH-absolute URLs are served the way Pages would serve them."""
        u = urllib.parse.urlparse(route.request.url)
        rest = u.path.split(BASE_PATH, 1)[1]
        target = self.base + rest + (("?" + u.query) if u.query else "")
        try:
            resp = await route.fetch(url=target)
            if resp.status == 404 or (rest.endswith("/") or rest == "") and resp.status != 200:
                nf = await route.fetch(url=self.base + "404.html")
                await route.fulfill(status=404, body=await nf.body(), headers={"content-type": "text/html; charset=utf-8"})
            else:
                await route.fulfill(response=resp)
        except Exception:
            await route.abort()

    async def page(self, context):
        p = await context.new_page()
        p.on("pageerror", lambda e: self.console.append(f"pageerror {p.url}: {e}"))
        p.on("console", lambda m: self.console.append(f"console {p.url}: {m.text}") if m.type == "error" else None)
        return p

    async def load(self, page, path="", tier="1", settle=True, query="", first=True):
        q = []
        if tier:
            q.append("tier=" + tier)
        if query:
            q.append(query)
        await page.goto(self.base + path + ("?" + "&".join(q) if q else ""), wait_until="load", timeout=30000)
        if settle:
            await self.settle(page, first=first)

    async def settle(self, page, first=False):
        await soft_wait(page, "() => !!window.__tbk", 8000)
        await soft_wait(page, VEIL_GONE_JS, 6000)
        route = await page.evaluate("() => (document.body && document.body.dataset.route) || ''")
        if route == "home":
            await soft_wait(page, LENS_CLOSED_JS, 10000)
            if first:
                await soft_wait(page, HINT_SHOWN_JS, 3000)
        await soft_wait(page, ANIMS_DONE_JS, 4000)
        await stable(page, [SEL["sheet"], SEL["cam"], SEL["bar"], SEL["thumb"], SEL["page"]], 8, 5000)

    async def nav(self, page, rid, timeout=10000):
        """Soft navigation through the page's own link: the tab, the capsule, or the Notices row."""
        if rid == "home":
            await page.locator(SEL["capsule"]).first.click(timeout=5000)
        elif rid == "notices":
            await page.locator(SEL["notices_link"]).first.evaluate("e => e.click()")
        else:
            await self.click_tab(page, TAB[rid])
        await page.wait_for_function("r => document.body.dataset.route === r", arg=rid, timeout=timeout)
        await self.settle(page)

    async def click_tab(self, page, label):
        """Click a tab like a visitor: if the bar is folded (the tab is not under the pointer), tap the bar open first."""
        if not await page.evaluate(TAB_HIT_JS, label):
            bar = await page.locator(SEL["bar"]).first.bounding_box()
            await page.mouse.click(bar["x"] + bar["width"] / 2, bar["y"] + bar["height"] / 2)
            await stable(page, [SEL["bar"]], 8, 2500)
        loc = page.locator(SEL["tab"]).filter(has_text=re.compile(r"^\s*%s\s*$" % re.escape(label))).first
        await loc.click(timeout=5000)

    async def geom(self, page):
        return await page.evaluate(GEOM_JS)

    async def lens(self, page):
        return await page.evaluate(LENS_JS)

    def engraving(self, info):
        cx, cy, w = track_at(self.track, info["t"])
        film = {"v": info["vrect"], "still": info["vrect"], "objpos": info["objpos"], "src": info["src"], "stillSrc": info["stillSrc"]}
        ex, ey, per = to_screen(cx, cy, film, self.media, info["W"], info["H"])
        return ex, ey, w * per

    def lens_error(self, info):
        ex, ey, _ = self.engraving(info)
        d = math.hypot(info["x"] - ex, info["y"] - ey)
        # "at the clamp": the disc sits r + 8 from the copy, measured per line box or against the
        # union of the copy's line boxes (the spec's per-rect push, or the stricter block push)
        rects = info.get("copyAll") or info["copy"]
        gaps = [rect_dist(info["x"], info["y"], r) - info["rw"] for r in rects]
        if rects:
            u = [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]
            gaps.append(rect_dist(info["x"], info["y"], u) - info["rw"])
        clamped = any(abs(g - 8) <= 4 for g in gaps)
        return d, clamped, (ex, ey)

    def station_eval(self, g):
        st = g["station"]
        if st not in STATION_PT or not self.media:
            return None
        fx, fy = self.station_point(st, g)
        W, H = g["W"], g["H"]
        px, py, _ = to_screen(fx, fy, g["film"], self.media, W, H)
        fa = free_area(g)
        cx, cy = (fa[0] + fa[2]) / 2, (fa[1] + fa[3]) / 2
        L, T, RW, RH = g["film"]["v"] or g["film"]["still"]
        clx = abs(L) < 1.5 or abs(L + RW - W) < 1.5
        cly = abs(T) < 1.5 or abs(T + RH - H) < 1.5
        dx, dy = px - cx, py - cy
        ok = math.hypot(dx, dy) <= 24 or ((abs(dx) <= 24 or clx) and (abs(dy) <= 24 or cly))
        return dict(ok=ok, pt=(round(px), round(py)), centre=(round(cx), round(cy)), d=round(math.hypot(dx, dy), 1),
                    clamp=(clx, cly), covers=covers(g["film"]["v"] or g["film"]["still"], W, H))

    # ---- shared sweeps (one per viewport, reused by many checks)
    async def sweep(self, vp):
        if vp not in self._sweeps:
            self._sweeps[vp] = asyncio.ensure_future(self._sweep(vp))
        return await self._sweeps[vp]

    async def sweeps(self, vps):
        res = await asyncio.gather(*(self.sweep(v) for v in vps))
        return dict(zip(vps, res))

    async def _sweep(self, vp):
        out = {}
        async with self._sem:
            c = await self.ctx(vp)
            try:
                p = await self.page(c)
                try:
                    await self.load(p, "")
                    out["home"] = await p.evaluate(SNAPSHOT_JS)
                except Exception as e:
                    out["home"] = {"error": "load /: " + str(e).splitlines()[0]}
                for rid in INNER + ["home"]:
                    key = rid if rid != "home" else "home-again"
                    try:
                        await self.nav(p, rid)
                    except Exception as e:
                        out.setdefault("_navfail", []).append(f"{rid}: {str(e).splitlines()[0]}")
                        try:
                            await self.load(p, ROUTE_PATH[rid])
                        except Exception as e2:
                            out[key] = {"error": f"{rid}: {str(e2).splitlines()[0]}"}
                            continue
                    try:
                        out[key] = await p.evaluate(SNAPSHOT_JS)
                    except Exception as e:
                        out[key] = {"error": f"{rid}: {str(e).splitlines()[0]}"}
                for rid in ("404", "offline"):
                    try:
                        await self.load(p, ROUTE_PATH[rid])
                        out[rid] = await p.evaluate(SNAPSHOT_JS)
                    except Exception as e:
                        out[rid] = {"error": f"{rid}: {str(e).splitlines()[0]}"}
            finally:
                await c.close()
        return out

    async def forms(self, vp):
        if vp not in self._forms:
            self._forms[vp] = asyncio.ensure_future(self._forms_sweep(vp))
        return await self._forms[vp]

    async def _forms_sweep(self, vp):
        out = {}
        async with self._sem:
            c = await self.ctx(vp, permissions=["clipboard-read", "clipboard-write"])
            try:
                p = await self.page(c)

                async def step(name, coro):
                    try:
                        await coro
                        await soft_wait(p, ANIMS_DONE_JS, 3000)
                        out[name] = await p.evaluate(SNAPSHOT_JS)
                    except Exception as e:
                        out[name] = {"error": f"{name}: {str(e).splitlines()[0]}"}

                async def coll_error():
                    await self.load(p, "collection/")
                    await p.fill(SEL["notify_input"], "name@")
                    await p.press(SEL["notify_input"], "Enter")
                    await p.wait_for_function("t => document.querySelector('#page').innerText.includes(t)",
                                              arg="That address does not look complete.", timeout=4000)

                async def coll_success():
                    await p.fill(SEL["notify_input"], "name@example.com")
                    await click_notify(p)
                    await wait_text(p, PREVIEW_OK, 5000)

                async def comm_errors():
                    await self.load(p, "commission/")
                    await submit_commission(p)
                    await wait_text(p, "Your name, please.", 4000)

                async def comm_success():
                    await fill_commission(p)
                    await submit_commission(p)
                    await wait_text(p, PREVIEW_OK, 5000)

                async def toast():
                    await self.load(p, "contact/")
                    await p.locator(f"{SEL['page']} button").filter(has_text=re.compile(r"^\s*Copy\s*$")).first.click()
                    await p.wait_for_function(J("() => { __PRELUDE__ const t = $(S.toast); return t && rendered(t) && t.textContent.trim().length > 0; }"), timeout=4000)

                async def preview():
                    chip = p.locator("button, [role=button], [role=switch]").filter(has_text=re.compile(r"^\s*Preview\s*$")).first
                    await robust_click(chip)
                    await p.wait_for_function("() => document.documentElement.classList.contains('show-ph')", timeout=3000)

                await step("collection:error", coll_error())
                await step("collection:success", coll_success())
                await step("commission:errors", comm_errors())
                await step("commission:success", comm_success())
                await step("contact:toast", toast())
                await step("contact:preview", preview())
            finally:
                await c.close()
        return out

    def raw_pages(self):
        out = {}
        for rid, path in ROUTE_PATH.items():
            st, h, body = self.get(path)
            out[rid] = (st, body.decode("utf-8", "replace"), self.base + path)
        return out

    def module_graph(self, page_url, html_text):
        g = grab(html_text)
        entries = [urllib.parse.urljoin(page_url, a.get("src")) for t, a in g.tags if t == "script" and a.get("type") == "module" and a.get("src")]
        seen, todo = set(), list(entries)
        while todo:
            u = todo.pop()
            if u in seen:
                continue
            seen.add(u)
            st, _, body = self.get(u)
            if st != 200:
                continue
            src = strip_js_comments(body.decode("utf-8", "replace"))
            for m in IMPORT_RE.finditer(src):
                spec = m.group(1)
                if spec.startswith((".", "/")):
                    todo.append(urllib.parse.urljoin(u, spec))
        return entries, seen


# --------------------------------------------------------------------------------------
# Form helpers
# --------------------------------------------------------------------------------------
async def wait_text(p, text, timeout):
    await p.wait_for_function("t => { const m = document.querySelector('#page'); return !!m && m.innerText.includes(t); }", arg=text, timeout=timeout)


async def page_has(p, text):
    return await p.evaluate("t => { const m = document.querySelector('#page'); return !!m && m.innerText.includes(t); }", text)


async def click_notify(p):
    try:
        await p.locator(SEL["notify_go"]).first.click(timeout=3000)
    except Exception:
        await p.locator(SEL["notify"]).first.evaluate("f => f.requestSubmit ? f.requestSubmit() : f.submit()")


async def submit_commission(p):
    try:
        await p.locator(SEL["cform_submit"]).first.click(timeout=4000)
    except Exception:
        await p.locator(SEL["cform"]).first.evaluate("f => f.requestSubmit()")


async def set_reply(p, value):
    await p.locator(f"{SEL['cform']} {SEL['reply']}[value=\"{value}\"]").first.evaluate("e => { if (!e.checked) e.click(); }")


async def set_consent(p, on):
    await p.locator(f"{SEL['cform']} {SEL['consent']}").first.evaluate("(e, on) => { if (e.checked !== on) e.click(); }", on)


async def fill_commission(p, **over):
    vals = dict(name="Test Client", email="client@example.com", phone="", based="", idea="A round frame, worn every day.",
                reply="email", consent=True)
    vals.update(over)
    f = SEL["cform"]
    for k in ("name", "email", "phone", "based", "idea"):
        await p.fill(f"{f} [name=\"{k}\"]", vals[k])
    await set_reply(p, vals["reply"])
    await set_consent(p, vals["consent"])


async def storage(p):
    return await p.evaluate("() => { const f = (s) => { try { return Object.entries(s).sort(); } catch (e) { return 'n/a'; } }; return JSON.stringify([f(localStorage), f(sessionStorage)]); }")


# --------------------------------------------------------------------------------------
# Glass and brand rules (1-11)
# --------------------------------------------------------------------------------------
def snaps(sw):
    """(key, snapshot) pairs of a sweep, skipping bookkeeping and noting failed routes."""
    for k, s in sw.items():
        if k.startswith("_"):
            continue
        yield k, s


async def hold_start(R, p, vp, where=None):
    info = await R.lens(p)
    x, y = where or (info["x"], info["y"])
    if VIEWPORTS[vp]["touch"]:
        t = await Touch.of(p)
        await t.down(x, y)
        return t
    await p.mouse.move(x, y)
    await p.mouse.down()
    return None


async def hold_end(p, t):
    if t:
        await t.up()
    else:
        await p.mouse.up()


async def wait_lr(p, cmp, value, timeout):
    return await soft_wait(p, J("""([cmp, v]) => { __PRELUDE__ const s = $(S.sheet); const r = parseFloat(cs(s).getPropertyValue('--lr'));
        return cmp === '>=' ? r >= v : r <= v; }"""), timeout, arg=[cmp, value])


# The bottom of a block inside the sheet vs the bar's top (the sheet's content ends 8 px above
# the bar, with a 28 px fade: whole means bottom <= barTop - 36).
PEEK_FIT_JS = """(sel) => { const e = document.querySelector('#page ' + sel) || document.querySelector(sel);
  const b = document.querySelector('.bar').getBoundingClientRect();
  return {b: e ? Math.round(e.getBoundingClientRect().bottom * 10) / 10 : null, bar: Math.round(b.top * 10) / 10}; }"""


async def set_detent(R, p, want, touch_only=False):
    """Drive the phone sheet to a detent with real touch: taps on the grabber, slow drags for Glance."""
    g = await R.geom(p)
    cur = g["sheet"]["state"]
    if cur == want:
        return True
    t = await Touch.of(p)
    grab = await p.locator(SEL["grab"]).first.bounding_box()
    gx, gy = grab["x"] + grab["width"] / 2, grab["y"] + grab["height"] / 2
    wait_js = "w => document.querySelector(%s).dataset.state === w" % json.dumps(SEL["sheet"])
    if {cur, want} == {"peek", "full"}:
        await p.locator(SEL["grab"]).first.tap()
        await soft_wait(p, wait_js, 3000, arg=want)
    else:
        tgt = expected_sheet_rect(g["W"], g["H"], want, g["sheet"].get("det"))[1]
        dy = tgt - g["sheet"]["rect"][1]
        await t.drag(gx, gy, gx, gy + dy, steps=24, dt=0.02, settle_before_release=0.12)
        if not await soft_wait(p, wait_js, 1500, arg=want) and not touch_only:
            # the touch drag did not land (check 25 reports that); reach the state with a mouse drag
            await stable(p, [SEL["sheet"]], 8, 2000)
            g = await R.geom(p)
            grab = await p.locator(SEL["grab"]).first.bounding_box()
            gx, gy = grab["x"] + grab["width"] / 2, grab["y"] + grab["height"] / 2
            dy = tgt - g["sheet"]["rect"][1]
            await p.mouse.move(gx, gy)
            await p.mouse.down()
            for i in range(1, 25):
                await asyncio.sleep(0.02)
                await p.mouse.move(gx, gy + dy * i / 24)
            await asyncio.sleep(0.12)
            await p.mouse.move(gx, gy + dy)
            await asyncio.sleep(0.02)
            await p.mouse.move(gx, gy + dy)
            await p.mouse.up()
            await soft_wait(p, wait_js, 3000, arg=want)
    await stable(p, [SEL["sheet"], SEL["bar"]], 8, 3000)
    return (await R.geom(p))["sheet"]["state"] == want


@check(1, "Backdrop count", timeout=900)
async def c01(R, res):
    css = R.text("css/site.css")
    sels = css_backdrop_selectors(css) if css else []
    res.add(bool(css), "css/site.css answers 200")
    res.add(len(sels) == 3, "site.css: exactly three selectors carry backdrop-filter", ", ".join(sels) or "none")
    mx = 0
    sw = await R.sweeps(R.vps(ALL_VP))
    for vp, s in sw.items():
        for key, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {key}", snap["error"])
                continue
            n = len(snap["backdrop"])
            mx = max(mx, n)
            res.add(n <= 3, f"{vp} {key} at rest: ≤ 3 backdrop-filter elements", f"{n}: {', '.join(snap['backdrop'])}")
    states = 0
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp, permissions=["clipboard-read", "clipboard-write"])
        try:
            p = await R.page(c)
            await R.load(p, "")
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            t = await hold_start(R, p, vp)
            grew = await wait_lr(p, ">=", math.hypot(W, H) - 1, 2500)
            n = await p.evaluate(J("() => { __PRELUDE__ return glassEls().map(desc); }"))
            await hold_end(p, t)
            res.add(len(n) <= 3, f"{vp} home hold: ≤ 3", f"{len(n)} {n} (lens grew: {grew})")
            await wait_lr(p, "<=", rmin_rmax(W)[1] + 2, 3000)
            await p.evaluate(REC_GLASS_JS, [1800])
            await R.click_tab(p, "House")
            await p.wait_for_function("() => window.__e2eGlass.done", timeout=5000)
            rec = await p.evaluate("() => window.__e2eGlass")
            res.add(rec["max"] <= 3, f"{vp} mid-morph Home→House (every frame for 1.8 s): ≤ 3", f"{rec['max']}: {rec['at']}")
            await R.settle(p)
            if VIEWPORTS[vp]["touch"]:
                for want in ("peek", "full", "glance"):
                    ok = await set_detent(R, p, want)
                    n = await p.evaluate(J("() => { __PRELUDE__ return glassEls().map(desc); }"))
                    res.add(ok and len(n) <= 3, f"{vp} house {want}{' (bar folded)' if want == 'full' else ''}: ≤ 3",
                            f"reached={ok} {len(n)} {n}")
                    states += 1
            await R.nav(p, "contact")
            await p.locator(f"{SEL['page']} button").filter(has_text=re.compile(r"^\s*Copy\s*$")).first.click()
            shown = await soft_wait(p, J("() => { __PRELUDE__ const t = $(S.toast); return !!t && rendered(t); }"), 3000)
            n = await p.evaluate(J("() => { __PRELUDE__ return glassEls().map(desc); }"))
            res.add(shown and len(n) <= 3, f"{vp} toast shown: ≤ 3", f"toast shown={shown}; {len(n)} {n}")
            states += 3
        finally:
            await c.close()
    return f"max {mx} at rest over {sum(1 for s in sw.values() for _ in snaps(s))} route states, {states} interaction states ≤ 3; site.css: {', '.join(sels)}"


@check(2, "Backdrop-root walk", timeout=900)
async def c02(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    n = 0
    for vp, s in sw.items():
        for key, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {key}", snap["error"])
                continue
            w = snap["walk"]
            n += 1
            res.add(not w["bad"], f"{vp} {key}: no backdrop root above glass", "; ".join(w["bad"][:3]))
            res.add(not w["anims"], f"{vp} {key}: no animation left on glass or its ancestors", "; ".join(w["anims"][:3]))
    for vp in R.vps([PHONE, TABLET, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "house/")
            if VIEWPORTS[vp]["w"] < 720:
                await set_detent(R, p, "full")
                await soft_wait(p, ANIMS_DONE_JS, 3000)
                w = await p.evaluate(J("() => { __PRELUDE__ return rootWalk(); }"))
                res.add(not w["bad"] and not w["anims"], f"{vp} house at Full, bar folded: clean walk", "; ".join((w["bad"] + w["anims"])[:3]))
                n += 1
            await R.nav(p, "home")
            w = await p.evaluate(J("() => { __PRELUDE__ return rootWalk(); }"))
            res.add(not w["bad"] and not w["anims"], f"{vp} house→home soft nav, settled: clean walk", "; ".join((w["bad"] + w["anims"])[:3]))
            n += 1
        finally:
            await c.close()
    return f"{n} settled states: no transformed/filtered/opacity/mask/clip/blend/backdrop/will-change ancestor, no lingering animation"


@check(3, "Glass actually blurs", timeout=300)
async def c03(R, res):
    worst = 0.0
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "house/")
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]

            async def measure(label, box, target):
                nonlocal worst
                others = [g for g in SEL["glass"] if g != target]
                await p.evaluate(CHECKER_ADD_JS)
                h1 = await inject_style(p, GLASS_CONTENT_OFF_CSS + f"{', '.join(others)} {{ visibility: hidden !important; }}\n")
                await stable(p, [SEL["sheet"], SEL["bar"]], 4, 1500)
                a = await shot(p)
                h2 = await inject_style(p, GLASS_HIDE_CSS)
                b = await shot(p)
                await h2.remove()
                await h1.remove()
                await p.evaluate(CHECKER_DEL_JS)
                sc = a.shape[1] / W
                db = dev_box(box, sc, a.shape)
                va, vb = lap_var(a, db), lap_var(b, db)
                ratio = va / vb if vb and vb == vb and vb > 0 else float("nan")
                worst = max(worst, ratio if ratio == ratio else 0)
                res.add(ratio == ratio and ratio < 0.35, f"{vp} {label}: Laplacian var with glass < 35% of hidden",
                        f"{va:.1f} / {vb:.1f} = {ratio:.4f}")

            g = await R.geom(p)
            bx, by, bw, bh = g["bar"]
            await measure("bar (house)", [bx + bh / 2 + 4, by + 8, bx + bw - bh / 2 - 4, by + bh - 8], SEL["bar"])
            cx, cy, cw, chh = g["caps"]
            await measure("capsule", [cx + chh / 2, cy + 8, cx + cw - chh / 2, cy + chh - 8], SEL["capsule"])
            if W < 720:
                for want in ("peek", "full"):
                    await set_detent(R, p, want)
                    g = await R.geom(p)
                    sx, sy, sw_, sh_ = g["sheet"]["rect"]
                    bottom = min(sy + sh_, H, g["bar"][1] - 8)
                    await measure(f"sheet at {want}", [sx + 24, sy + 24, sx + sw_ - 24, bottom - 16], SEL["sheet"])
            else:
                sx, sy, sw_, sh_ = g["sheet"]["rect"]
                await measure("panel", [sx + 24, sy + 24, sx + sw_ - 24, sy + sh_ - 24], SEL["sheet"])
            await R.nav(p, "home")
            info = await R.lens(p)
            await measure("home frost", frost_box(info, W, H), SEL["sheet"])
        finally:
            await c.close()
    return f"worst ratio {worst:.4f} (glass over an injected 8px checker vs the same region with the glass hidden)"


@check(4, "No shadows", timeout=600)
async def c04(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    n = 0
    for vp, s in sw.items():
        for key, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {key}", snap["error"])
                continue
            n += 1
            res.add(not snap["glassShadow"], f"{vp} {key}: glass box-shadow is inset only", "; ".join(snap["glassShadow"][:3]))
            res.add(not snap["drops"], f"{vp} {key}: no drop shadow anywhere (book p15)", "; ".join(snap["drops"][:3]))
    return f"{n} route states: glass inset-only, no offset or blurred outer shadow anywhere"


def all_states(R, sw, fm):
    for vp, s in sw.items():
        for k, snap in snaps(s):
            yield vp, k, snap
    for vp, s in fm.items():
        for k, snap in s.items():
            yield vp, k, snap


@check(5, "Colours", timeout=600)
async def c05(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    fm = {vp: await R.forms(vp) for vp in R.vps([PHONE, DESK])}
    n, seen_err = 0, False
    for vp, k, snap in all_states(R, sw, fm):
        if "error" in snap:
            res.add(False, f"{vp} {k}", snap["error"])
            continue
        bad = []
        for c in snap["colours"]:
            col = parse_rgba(c["f"]) or parse_rgba(c["c"])
            if col and col[3] <= 0.01:
                continue
            name = colour_name(col)
            n += 1
            if name in TEXT_OK:
                continue
            if name == "onyx" and c["filled"]:
                continue
            if name == "error" and c["err"]:
                seen_err = True
                continue
            bad.append(f"{c['d']} \"{c['t'][:24]}\" {c['f'] or c['c']}" + (" (error colour off an invalid message)" if name == "error" else ""))
        for c in snap["placeholders"]:
            if colour_name(parse_rgba(c["c"])) not in TEXT_OK:
                bad.append(f"placeholder {c['d']} {c['c']}")
        for c in snap["fieldColours"]:
            col = parse_rgba(c["f"]) or parse_rgba(c["c"])
            if colour_name(col) not in TEXT_OK:
                bad.append(f"field text {c['d']} {c['f'] or c['c']}")
        res.add(not bad, f"{vp} {k}: text colours in the palette", "; ".join(bad[:3]))
    if fm:
        res.add(seen_err, "the error colour is seen on an invalid message (forms sweep)", "no --error text found on the error states")
    return f"{n} text runs over every route and form state: bone, --label-*, --ti steps; onyx only on the filled button; --error only on invalid messages"


@check(6, "Type", timeout=600)
async def c06(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    fm = {vp: await R.forms(vp) for vp in R.vps([PHONE, DESK])}
    eyebrows = 0
    for vp, k, snap in all_states(R, sw, fm):
        if "error" in snap:
            res.add(False, f"{vp} {k}", snap["error"])
            continue
        res.add(not snap["type"], f"{vp} {k}: normal style, weights 400-700, caps and tracking only on .eyebrow", "; ".join(snap["type"][:3]))
        eyebrows += len(snap["eyebrows"])
    res.add(eyebrows > 0, "eyebrows exist on the inner pages", "no .eyebrow rendered anywhere")
    return f"no italics, weights in {{400,500,600,700}}; uppercase and positive tracking only on .eyebrow ({eyebrows} eyebrow renders)"


def raw_texts(R):
    """Text nodes, attributes and metas of every page as served (hidden text included)."""
    out = []
    for rid, (st, body, url) in R.raw_pages().items():
        if st != 200 and not (rid == "404" and st in (200, 404)):
            continue
        g = grab(body)
        out.append((rid, g))
    return out


def js_literals(R):
    lits = []
    st, _, body = R.get("")
    if st != 200:
        return lits
    _, mods = R.module_graph(R.base, body.decode("utf-8", "replace"))
    for u in sorted(mods):
        s2, _, b = R.get(u)
        if s2 == 200:
            src = strip_js_comments(b.decode("utf-8", "replace"))
            lits += [(u.rsplit("/", 1)[-1], m.group(0)[1:-1]) for m in STR_RE.finditer(src)]
    return lits


@check(7, "Banned words", timeout=600)
async def c07(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    fm = {vp: await R.forms(vp) for vp in R.vps([PHONE, DESK])}
    n = 0
    for vp, k, snap in all_states(R, sw, fm):
        if "error" in snap:
            res.add(False, f"{vp} {k}", snap["error"])
            continue
        hay = [("text", snap["text"])] + [(f"{a[0]}[{a[1]}]", a[2]) for a in snap["attrs"]] + [(f"meta {m[0]}", m[1]) for m in snap["metas"]] + [("title", snap["title"])]
        hits = [f"{w}: {BANNED.search(t).group(0)}" for w, t in hay if t and BANNED.search(t)]
        res.add(not hits, f"{vp} {k}: no banned word", "; ".join(hits[:3]))
        res.add("!" not in snap["text"], f"{vp} {k}: no '!' in visible text", re.findall(r".{0,20}!.{0,10}", snap["text"])[:2])
        n += 1
    for rid, g in raw_texts(R):
        hay = [("text", t) for t in g.text] + [(f"{t}[{k}]", v) for t, k, v in g.attrs] + [(f"meta {m[0]}", m[1]) for m in g.metas]
        hits = [f"{w}: {t.strip()[:40]}" for w, t in hay if BANNED.search(t)]
        res.add(not hits, f"served {rid}: no banned word in text, alt, aria-label, title, placeholder, meta", "; ".join(hits[:3]))
        bang = [t.strip()[:40] for t in g.text if "!" in t]
        res.add(not bang, f"served {rid}: no '!' in any text node", "; ".join(bang[:3]))
    lits = js_literals(R)
    hits = [f"{f}: {s[:40]}" for f, s in lits if BANNED.search(s)]
    res.add(not hits, f"shipped JS string literals ({len(lits)}): no banned word", "; ".join(hits[:3]))
    return f"{n} rendered states + served HTML + {len(lits)} JS literals clean; no '!'"


def house_name_bad(s):
    t = s.replace("Ilya Taboukhi", "").replace("info@taboukhi.com", "").replace("taboukhi.com", "")
    return [m.group(0) for m in re.finditer(r"taboukhi", t, re.I) if m.group(0) != "TABOUKHI"]


URL_META = ("og:url", "og:image", "twitter:image", "og:image:url", "og:image:secure_url", "tbk-root")


def copy_meta(m):
    """Metas that carry copy (description, og:title, ...), not URLs or paths."""
    return m[0] not in URL_META and not re.match(r"(https?:)?/|\.\.?/", m[1] or "")


@check(8, "House name", timeout=600)
async def c08(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    fm = {vp: await R.forms(vp) for vp in R.vps([PHONE, DESK])}
    for vp, k, snap in all_states(R, sw, fm):
        if "error" in snap:
            res.add(False, f"{vp} {k}", snap["error"])
            continue
        hay = [snap["text"], snap["title"]] + [a[2] for a in snap["attrs"]] + [m[1] for m in snap["metas"] if copy_meta(m)]
        bad = [b for t in hay if t for b in house_name_bad(t)]
        res.add(not bad, f"{vp} {k}: TABOUKHI always in caps", ", ".join(sorted(set(bad))))
    for rid, g in raw_texts(R):
        hay = g.text + [v for _, _, v in g.attrs] + [m[1] for m in g.metas if copy_meta(m)]
        bad = [b for t in hay for b in house_name_bad(t)]
        res.add(not bad, f"served {rid}: TABOUKHI always in caps", ", ".join(sorted(set(bad))))
    for vp, s in sw.items():
        home = s.get("home", {})
        res.add("error" not in home and FOUNDER in home.get("text", ""), f"{vp} home: the founder sentence verbatim", "not found in Home's visible text")
    return "every match is TABOUKHI except Ilya Taboukhi / taboukhi.com / info@taboukhi.com; founder sentence verbatim on Home"


async def k_background_ok(p, box):
    """Pixels inside the K's box that are not the glyph are onyx (median luma ≤ 24)."""
    x, y, w, h = box
    img = await shot(p, clip={"x": max(0, x - 2), "y": max(0, y - 2), "width": w + 4, "height": h + 4})
    L = luma(img)
    bg = L[L < 128]
    return (float(np.median(bg)) if bg.size else 999.0)


@check(9, "K size", timeout=600)
async def c09(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    seen = 0
    for vp, s in sw.items():
        for k, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {k}", snap["error"])
                continue
            for kk in snap["ks"] + snap["kImgs"]:
                if not kk["shown"]:
                    continue
                seen += 1
                res.add(kk["rect"][3] >= 16, f"{vp} {k}: K {kk['d']} ≥ 16px tall", f"{kk['rect'][3]:.1f}px")
            cap = snap.get("capsule")
            res.add(cap and cap["wm"] and "TABOUKHI" not in (cap["text"] or ""), f"{vp} {k}: capsule wordmark is the traced SVG, not text", str(cap))
            res.add(not snap["wmText"], f"{vp} {k}: #p-wm has no <text>")
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            states = []
            await R.load(p, "collection/")
            await p.fill(SEL["notify_input"], "name@example.com")
            await click_notify(p)
            await wait_text(p, PREVIEW_OK, 5000)
            await soft_wait(p, ANIMS_DONE_JS, 2000)
            for label, path in (("collection success", None), ("commission success", "commission/"), ("404", "404.html"), ("offline", "offline.html")):
                if path == "commission/":
                    await R.load(p, path)
                    await fill_commission(p)
                    await submit_commission(p)
                    await wait_text(p, PREVIEW_OK, 5000)
                    await soft_wait(p, ANIMS_DONE_JS, 2000)
                elif path:
                    await R.load(p, path)
                ks = await p.evaluate(J("""() => { __PRELUDE__ return $$('use').filter(u => /#p-k$/.test(u.getAttribute('href') || u.getAttribute('xlink:href') || ''))
                    .filter(u => !u.closest(S.defs + ', defs, symbol, ' + S.wm_def)).map(u => u.ownerSVGElement || u).filter(rendered)
                    .map(s => ({d: desc(s), rect: rect(s), under: kUnder(s)})); }"""))
                res.add(len(ks) >= 1, f"{vp} {label}: the still K is shown", "no standalone K rendered")
                for kk in ks:
                    seen += 1
                    x, y, w, h = kk["rect"]
                    res.add(h >= 16, f"{vp} {label}: K ≥ 16px tall", f"{h:.1f}px")
                    med = await k_background_ok(p, [x, y, w, h])
                    res.add(kk["under"] in ("onyx", "glass") or med <= 24, f"{vp} {label}: onyx or glass beneath the K",
                            f"ancestor: {kk['under']}, median background luma {med:.0f}")
        finally:
            await c.close()
    return f"{seen} K renders ≥ 16px on onyx/glass; capsule wordmark is traced SVG"


@check(10, "No year", timeout=600)
async def c10(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    fm = {vp: await R.forms(vp) for vp in R.vps([PHONE, DESK])}
    for vp, k, snap in all_states(R, sw, fm):
        if "error" in snap:
            res.add(False, f"{vp} {k}", snap["error"])
            continue
        m = YEAR.search(snap["text"])
        res.add(not m, f"{vp} {k}: no year in visible text", m.group(0) if m else "")
    for rid, g in raw_texts(R):
        hits = [t.strip()[:40] for t in g.text if YEAR.search(t)]
        res.add(not hits, f"served {rid}: no year in any text node", "; ".join(hits[:3]))
    return "no 19xx/20xx in any rendered state or served text node"


@check(11, "Email", timeout=600)
async def c11(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    for vp, s in sw.items():
        for rid in ("contact", "notices"):
            snap = s.get(rid, {"error": "not reached"})
            res.add("error" not in snap and EMAIL in snap.get("text", ""), f"{vp} {rid}: shows {EMAIL}", snap.get("error", "absent"))
    return f"{EMAIL} on Contact and Notices at every viewport"


# --------------------------------------------------------------------------------------
# Film (12-15)
# --------------------------------------------------------------------------------------
@check(12, "Film covers the viewport", timeout=600)
async def c12(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    n = 0
    for vp, s in sw.items():
        for k, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {k}", snap["error"])
                continue
            W, H = snap["W"], snap["H"]
            f = snap["film"]
            res.add(covers(f["v"], W, H), f"{vp} {k}: video box covers the viewport", f"video {f['v']} cam {f['camT']}")
            res.add(covers(f["still"], W, H), f"{vp} {k}: still box covers the viewport", f"still {f['still']}")
            n += 1
    return f"{n} route states: the transformed film box covers every pixel of the viewport (object-fit: cover fills that box)"


def mean_l_diff(a, b, box):
    x0, y0, x1, y1 = box
    la, lb = luma(a[y0:y1, x0:x1]), luma(b[y0:y1, x0:x1])
    return float(np.abs(la - lb).mean()) if la.size else float("nan")


@check(13, "Visible motion by pixel diff", timeout=300)
async def c13(R, res):
    low = 1e9
    for rm in (None, "reduce"):
        for vp in R.vps([PHONE, DESK]):
            c = await R.ctx(vp, reduced_motion=rm)
            try:
                p = await R.page(c)
                await R.load(p, "")
                await soft_wait(p, PLAYING_JS, 8000)
                W = VIEWPORTS[vp]["w"]
                info = await R.lens(p)
                h = info["lr"] * 0.6
                box = [info["x"] - h, info["y"] - h, info["x"] + h, info["y"] + h]
                a = await shot(p)
                await asyncio.sleep(1.2)
                b = await shot(p)
                d = mean_l_diff(a, b, dev_box(box, a.shape[1] / W, a.shape))
                low = min(low, d)
                tag = "reduced motion" if rm else "motion"
                res.add(d > 0.15, f"{vp} home ({tag}): mean L diff inside the lens over 1.2 s > 0.15", f"{d:.3f}")
                await R.nav(p, "house")
                g = await R.geom(p)
                fa = free_area(g)
                box = [fa[0] + 8, fa[1] + 8, fa[2] - 8, fa[3] - 8]
                a = await shot(p)
                await asyncio.sleep(1.2)
                b = await shot(p)
                d = mean_l_diff(a, b, dev_box(box, a.shape[1] / W, a.shape))
                low = min(low, d)
                res.add(d > 0.15, f"{vp} house ({tag}): mean L diff in the free area over 1.2 s > 0.15", f"{d:.3f} box {[round(x) for x in box]}")
            finally:
                await c.close()
    return f"lowest mean L diff {low:.3f} (> 0.15), with and without prefers-reduced-motion"


def mp4_boxes(buf, start, end):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr:
            break
        yield typ.decode("latin1"), i, i + hdr, min(i + size, end)
        i += size


def mp4_tracks(url):
    """Top-level box order and every trak's handler type, read with byte ranges (moov is small)."""
    st, h, first = http(url, headers={"Range": "bytes=0-65535"})
    if st not in (200, 206):
        return None, f"HTTP {st}"
    whole = first if st == 200 else None
    total = len(first) if st == 200 else int((h.get("content-range", "/0").split("/")[-1]) or 0)

    def read(a, b):
        if whole is not None:
            return whole[a:b + 1]
        if b < len(first):
            return first[a:b + 1]
        return http(url, headers={"Range": f"bytes={a}-{b}"})[2]

    order, moov, pos = [], None, 0
    while pos + 8 <= total and len(order) < 64:
        hdr = read(pos, pos + 15)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr[:8])
        if size == 1:
            size = struct.unpack(">Q", hdr[8:16])[0]
        elif size == 0:
            size = total - pos
        t = typ.decode("latin1")
        order.append(t)
        if t == "moov":
            moov = read(pos, pos + size - 1)
        if size < 8:
            break
        pos += size
    if moov is None:
        return {"order": order, "handlers": []}, "no moov"
    handlers = []
    for t, s0, b0, e0 in mp4_boxes(moov, 0, len(moov)):
        if t != "moov":
            continue
        for t2, s2, b2, e2 in mp4_boxes(moov, b0, e0):
            if t2 != "trak":
                continue
            for t3, s3, b3, e3 in mp4_boxes(moov, b2, e2):
                if t3 != "mdia":
                    continue
                for t4, s4, b4, e4 in mp4_boxes(moov, b3, e3):
                    if t4 == "hdlr":
                        handlers.append(moov[b4 + 8:b4 + 12].decode("latin1"))
    return {"order": order, "handlers": handlers}, ""


@check(14, "No audio track; replays; held stays paused and visible", timeout=300)
async def c14(R, res):
    files = []
    st, _, body = R.get("")
    if st == 200:
        g = grab(body.decode("utf-8", "replace"))
        files = [urllib.parse.urljoin(R.base, a["src"]) for t, a in g.tags if t == "source" and a.get("src", "").endswith(".mp4")]
    files = sorted(set(files + [R.base + "assets/media/film-ambient-320.mp4"]))
    for u in files:
        info, err = mp4_tracks(u)
        if info is None:
            res.add(False, f"{u.rsplit('/', 1)[-1]} answers", err)
            continue
        hs = info["handlers"]
        o = info["order"]
        res.add(hs.count("vide") == 1 and "soun" not in hs, f"{u.rsplit('/', 1)[-1]}: one video track, no audio track", f"handlers {hs} {err}")
        res.add("moov" in o and ("mdat" not in o or o.index("moov") < o.index("mdat")), f"{u.rsplit('/', 1)[-1]}: moov before mdat", str(o))
    for vp in R.vps([DESK, PHONE]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            playing = await soft_wait(p, PLAYING_JS, 8000)
            res.add(playing, f"{vp}: the film plays by itself")
            await asyncio.sleep(1.0)
            a = await p.evaluate(J("() => { __PRELUDE__ const v = $(S.video); return {muted: v.muted, audio: v.webkitAudioDecodedByteCount || 0, moz: v.mozHasAudio}; }"))
            res.add(a["muted"] and a["audio"] == 0, f"{vp}: muted, no audio bytes decoded", str(a))
            await p.evaluate(J("() => { __PRELUDE__ $(S.video).pause(); }"))
            back = await soft_wait(p, J("() => { __PRELUDE__ return !$(S.video).paused; }"), 3000)
            res.add(back, f"{vp}: pause() from devtools replays within 3 s")
            await R.nav(p, "house")
            await R.nav(p, "notices")
            await robust_click(p.locator(f"{SEL['page']} {SEL['switch']}").first)
            held = await soft_wait(p, J("() => { __PRELUDE__ return $(S.video).paused; }"), 3000)
            txt = await p.evaluate("() => document.querySelector('#page').innerText")
            res.add(held, f"{vp}: the Film switch holds the film")
            res.add("Held" in txt, f"{vp}: the state text reads Held", "Playing/Held text not updated")
            t0 = await p.evaluate(J("() => { __PRELUDE__ return $(S.video).currentTime; }"))
            await p.mouse.click(5, VIEWPORTS[vp]["h"] // 2)
            await asyncio.sleep(1.5)
            v = await p.evaluate(J("""() => { __PRELUDE__ const v = $(S.video), s = cs(v);
                let op = 1; for (let a = v; a && a.nodeType === 1; a = a.parentElement) op *= parseFloat(cs(a).opacity);
                return {paused: v.paused, t: v.currentTime, display: s.display, vis: s.visibility, op: op, rect: rect(v), still: rect($(S.still))}; }"""))
            res.add(v["paused"] and abs(v["t"] - t0) < 0.05, f"{vp}: held stays paused after a gesture kick", str(v))
            res.add(v["display"] != "none" and v["vis"] == "visible" and (v["op"] > 0.99 or covers(v["still"], VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"])),
                    f"{vp}: held film stays on screen", str(v))
            await p.reload(wait_until="load")
            await R.settle(p)
            await asyncio.sleep(1.0)
            still_held = await p.evaluate(J("() => { __PRELUDE__ return $(S.video).paused; }"))
            res.add(still_held, f"{vp}: held survives a reload (localStorage)")
            await robust_click(p.locator(f"{SEL['page']} {SEL['switch']}").first)
            resumed = await soft_wait(p, J("() => { __PRELUDE__ return !$(S.video).paused; }"), 3000)
            res.add(resumed, f"{vp}: switching back plays again")
        finally:
            await c.close()
    return f"{len(files)} MP4s: one video track, no audio, moov first; pause() replays; held stays paused, visible, across reload"


@check(15, "Station clamp", timeout=600)
async def c15(R, res):
    if not R.media or not R.track:
        res.add(False, "media.json and k-track.json load", "missing")
        return ""
    sw = await R.sweeps(R.vps(ALL_VP))
    n = 0
    for vp, s in sw.items():
        for k in INNER:
            snap = s.get(k, {"error": "not reached"})
            if "error" in snap:
                res.add(False, f"{vp} {k}", snap["error"])
                continue
            ev = R.station_eval(snap)
            if ev is None:
                res.add(False, f"{vp} {k}: station known", f"data-station={snap['station']!r}")
                continue
            n += 1
            res.add(ev["covers"], f"{vp} {k}: camera never exposes the film edge", str(snap["film"]["v"]))
            res.add(ev["ok"], f"{vp} {k}: station point within 24px of the free-area centre or at the clamp",
                    f"point {ev['pt']} centre {ev['centre']} d={ev['d']} clamp x/y={ev['clamp']}")
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            for rid in ("commission", "contact", "home"):
                await p.evaluate(REC_RECT_JS, [1600])
                if rid == "home":
                    await p.locator(SEL["capsule"]).first.click()
                else:
                    await R.click_tab(p, TAB[rid])
                await p.wait_for_function("() => window.__e2eRect.done", timeout=6000)
                rec = await p.evaluate("() => window.__e2eRect")
                res.add(not rec["bad"], f"{vp} every frame of the camera move to {rid}: film covers", f"{len(rec['bad'])}/{rec['n']} frames: {rec['bad'][:2]}")
                await R.settle(p)
        finally:
            await c.close()
    return f"{n} station states covered and centred (or clamped); camera moves sampled every frame"


# --------------------------------------------------------------------------------------
# Lens (16-19)
# --------------------------------------------------------------------------------------
@check(16, "Lens tracks the engraving", timeout=300)
async def c16(R, res):
    if not R.media or not R.track:
        res.add(False, "media.json and k-track.json load", "missing")
        return ""
    worst = 0
    for vp in R.vps([PHONE, TABLET, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            await soft_wait(p, PLAYING_JS, 8000)
            W = VIEWPORTS[vp]["w"]
            rmin, rmax = rmin_rmax(W)
            for i in range(3):
                info = await R.lens(p)
                d, clamped, (ex, ey) = R.lens_error(info)
                worst = max(worst, d if not clamped else 0)
                res.add(d <= 12 or clamped, f"{vp} sample {i + 1}: lens centre within 12px of the tracked engraving",
                        f"lens ({info['x']:.0f},{info['y']:.0f}) engraving ({ex:.0f},{ey:.0f}) d={d:.1f} t={info['t']:.2f}")
                res.add(info["lr"] >= rmin - 0.5 and info["lr"] <= rmax + 0.5, f"{vp}: rest radius in [{rmin}, {rmax}]", f"--lr {info['lr']}")
                res.add(abs(info["rw"] - info["lr"]) <= 1.5, f"{vp}: .lens box radius equals the hole's --lr", f"box r {info['rw']:.1f} vs --lr {info['lr']}")
                await asyncio.sleep(0.6)
        finally:
            await c.close()
    return f"worst tracking error {worst:.1f}px; radius ≥ 104 phone / 120 tablet / 132 desktop"


@check(17, "Engraving legible in the lens", timeout=300)
async def c17(R, res):
    low = 1e9
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            W = VIEWPORTS[vp]["w"]
            info = await R.lens(p)
            h = info["lr"] * 0.6
            box = [info["x"] - h, info["y"] - h, info["x"] + h, info["y"] + h]
            a = await shot(p)
            st = await inject_style(p, MASK_OFF_CSS)
            b = await shot(p)
            await st.remove()
            db = dev_box(box, a.shape[1] / W, a.shape)
            va, vb = lap_var(a, db), lap_var(b, db)
            ratio = va / vb if vb > 0 else float("inf")
            low = min(low, ratio)
            res.add(ratio >= 4, f"{vp}: Laplacian var inside the lens ≥ 4x the same region frosted", f"{va:.1f} / {vb:.1f} = {ratio:.1f}x")
        finally:
            await c.close()
    return f"lowest ratio {low:.1f}x (lens vs the same region with the hole closed)"


@check(18, "Drag and throw", timeout=300)
async def c18(R, res):
    for vp in R.vps([DESK, PHONE]):
        touch = VIEWPORTS[vp]["touch"]
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            await soft_wait(p, PLAYING_JS, 8000)
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            i0 = await R.lens(p)
            tx, ty = i0["x"] - min(160, W * 0.28), i0["y"] - min(120, H * 0.14)
            if touch:
                t = await Touch.of(p)
                await t.drag(i0["x"], i0["y"], tx, ty, steps=14, dt=0.016)
            else:
                await mouse_path(p, i0["x"], i0["y"], tx, ty, steps=14, press=True)
            await stable(p, [SEL["lens"]], 8, 2500)
            i1 = await R.lens(p)
            moved = math.hypot(i1["x"] - i0["x"], i1["y"] - i0["y"])
            res.add(moved >= 60, f"{vp} {'touch' if touch else 'mouse'} drag moves the lens", f"moved {moved:.0f}px to ({i1['x']:.0f},{i1['y']:.0f})")
            copy = i1["copy"]
            if not copy:
                res.add(False, f"{vp}: home copy rects found")
                continue
            l, tt, r, b = copy[0]
            cx, cy = (l + r) / 2, (tt + b) / 2
            if touch:
                await t.drag(i1["x"], i1["y"], cx, cy, steps=14, dt=0.016)
                t_release = time.time()
            else:
                await mouse_path(p, i1["x"], i1["y"], cx, cy, steps=14)
            await stable(p, [SEL["lens"]], 8, 3000)
            i2 = await R.lens(p)
            hits = [q for q in i2["copy"] if rect_dist(i2["x"], i2["y"], q) < i2["rw"] - 0.5]
            res.add(not hits, f"{vp}: dragged onto the copy, the disc stays outside every copy rect",
                    f"lens ({i2['x']:.0f},{i2['y']:.0f}) r={i2['rw']:.0f} overlaps {len(hits)} line box(es)")
            if touch:
                # T-24: off the metal (more than two radii from the engraving) the lens waits 2.2 s,
                # not 6, before its 1.2 s glide back; on the arm it still waits 6 s.
                d_rel, _, _ = R.lens_error(i2)
                off_metal = d_rel > 2 * i2["rw"]
                back, took = False, None
                while time.time() - t_release < 10.5:
                    info = await R.lens(p)
                    d, clamped, _ = R.lens_error(info)
                    if d <= 12:
                        back, took = True, time.time() - t_release
                        break
                    await asyncio.sleep(0.25)
                res.add(back, f"{vp}: after its idle (touch) the lens is back on the engraving", f"back after {took:.1f}s" if took else "not back within 10.5 s")
                if took is not None:
                    if off_metal:
                        res.add(2.0 <= took <= 4.0, f"{vp}: released off the metal, it waited the short idle (2.2 s) and was back within 4 s", f"released {d_rel:.0f}px from the name (r {i2['rw']:.0f}); returned after {took:.1f}s")
                    else:
                        res.add(took >= 5.0, f"{vp}: released on the arm, it waited the full idle before returning", f"returned after {took:.1f}s")
        finally:
            await c.close()
    return "mouse and touch drags move the lens; the copy clamp holds; touch returns to the engraving after idle"


@check(19, "Hold to clear", timeout=300)
async def c19(R, res):
    for vp in R.vps([DESK, PHONE]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            rmax = rmin_rmax(W)[1]
            full = math.hypot(W, H)
            info = await R.lens(p)
            where = (info["x"], info["y"]) if not VIEWPORTS[vp]["touch"] else (W * 0.82, H * 0.2)
            t = await hold_start(R, p, vp, where)
            await asyncio.sleep(0.5)
            grew = await wait_lr(p, ">=", full - 1, 1500)
            lr = (await R.geom(p))["sheet"]["lr"]
            res.add(grew, f"{vp}: a press held past 400 ms grows --lr to ≥ hypot(W,H) = {full:.0f}", f"--lr {lr}")
            # the whole screen clear: compare with the frost-free reference, copy and chrome masked out
            a = await shot(p)
            st = await inject_style(p, FROST_OFF_CSS)
            ref = await shot(p)
            await st.remove()
            await hold_end(p, t)
            sc = a.shape[1] / W
            mask = np.ones(a.shape[:2], bool)
            for q in info["copy"]:
                x0, y0, x1, y1 = dev_box([q[0] - 10, q[1] - 10, q[2] + 10, q[3] + 10], sc, a.shape)
                mask[y0:y1, x0:x1] = False
            for rr in (info["caps"], info["bar"]):
                if rr:
                    x0, y0, x1, y1 = dev_box([rr[0] - 6, rr[1] - 6, rr[0] + rr[2] + 6, rr[1] + rr[3] + 6], sc, a.shape)
                    mask[y0:y1, x0:x1] = False
            mask = cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            va, vr = lap_var(a, mask=mask), lap_var(ref, mask=mask)
            ratio = va / vr if vr > 0 else float("nan")
            res.add(vr > 3 and 0.7 <= ratio <= 1.43, f"{vp}: held, the screen's Laplacian matches the frost-free reference (±30%)",
                    f"{va:.1f} vs {vr:.1f} = {ratio:.2f}")
            closed = await wait_lr(p, "<=", rmax + 2, 2500)
            res.add(closed, f"{vp}: release closes the glass back to the rest radius", f"--lr {(await R.geom(p))['sheet']['lr']}")
            # keyboard
            await p.focus(SEL["handle"])
            await p.keyboard.down("Space")
            kgrew = await wait_lr(p, ">=", full - 1, 2000)
            await p.keyboard.up("Space")
            kclosed = await wait_lr(p, "<=", rmax + 2, 2500)
            res.add(kgrew and kclosed, f"{vp}: Space on the focused handle clears, release closes", f"grew={kgrew} closed={kclosed}")
            await stable(p, [SEL["lens"]], 6, 2000)
            await p.focus(SEL["handle"])
            b0 = await R.lens(p)
            await p.keyboard.press("ArrowRight")
            await stable(p, [SEL["lens"]], 6, 2000)
            b1 = await R.lens(p)
            await p.keyboard.press("Shift+ArrowUp")
            await stable(p, [SEL["lens"]], 6, 2000)
            b2 = await R.lens(p)
            dx, dy = b1["x"] - b0["x"], b2["y"] - b1["y"]
            res.add(abs(dx - 24) <= 6, f"{vp}: ArrowRight moves the lens 24px", f"dx={dx:.1f}")
            res.add(abs(dy + 96) <= 8, f"{vp}: Shift+ArrowUp moves it 96px", f"dy={dy:.1f}")
            await p.keyboard.press("Escape")
            back = False
            t0 = time.time()
            while time.time() - t0 < 3.0:
                d, clamped, _ = R.lens_error(await R.lens(p))
                if d <= 12:
                    back = True
                    break
                await asyncio.sleep(0.1)
            res.add(back, f"{vp}: Escape returns the lens to the engraving", f"d={d:.1f} after 3 s")
        finally:
            await c.close()
    return "press-and-hold and Space clear the whole screen and close on release; arrows move 24/96px; Escape returns"


# --------------------------------------------------------------------------------------
# Contrast (20)
# --------------------------------------------------------------------------------------
async def park_near_copy(R, p, touch):
    info = await R.lens(p)
    if not info or not info["copy"]:
        return None
    l, t, r, b = info["copy"][0]
    tx, ty = (l + r) / 2, (t + b) / 2
    if touch:
        # drag the lens onto the copy: the clamp holds it r + 8 out
        T = await Touch.of(p)
        await T.drag(info["x"], info["y"], tx, ty, steps=10, dt=0.016)
    else:
        # a fine pointer parks the lens while it is over the copy block, so aim just above the block:
        # the lens springs toward the pointer and the clamp stops it r + 8 from the copy
        blk = await p.evaluate(J("() => { __PRELUDE__ const p = $(S.page); const rs = textElems(p).map(e => e.getBoundingClientRect());"
                                 " return rs.length ? [Math.min(...rs.map(q => q.left)), Math.min(...rs.map(q => q.top)), Math.max(...rs.map(q => q.right)), Math.max(...rs.map(q => q.bottom))] : null; }"))
        if blk:
            tx = (blk[0] + blk[2]) / 2
            ty = blk[1] - 12 if blk[1] > 24 else blk[3] + 12
        await mouse_path(p, info["x"], info["y"], tx, ty, steps=10)
    await stable(p, [SEL["lens"]], 6, 2500)
    i2 = await R.lens(p)
    return min(rect_dist(i2["x"], i2["y"], q) for q in i2["copy"]) - i2["rw"]


async def contrast_run(R, vp, tier, full):
    """One (viewport, tier): Home with the lens parked by the copy, then every inner page."""
    out, notes = {}, []
    touch = VIEWPORTS[vp]["touch"]
    async with R._sem:
        c = await R.ctx(vp, dpr=2)
        try:
            p = await R.page(c)
            await R.load(p, "", tier=tier)
            await soft_wait(p, PLAYING_JS, 8000)
            scan = await p.evaluate(BRIGHT_SCAN_JS, 0.5)
            if not scan:
                return {}, ["no film to scan"], {}
            d = scan["d"]
            lum = scan["lum"]
            bt = max(lum, key=lambda x: x[1])[0]
            even = [i * d / 16 for i in range(16)]
            times = even + ([bt] if all(abs(bt - e) > 0.05 for e in even) else [])
            by_l = sorted(even, key=lambda t: -min(lum, key=lambda x: abs(x[0] - t))[1])
            short = sorted(set([bt] + by_l[:3]))
            notes.append(f"brightest t={bt:.2f}s")
            W = VIEWPORTS[vp]["w"]
            H = VIEWPORTS[vp]["h"]

            async def measure(label, ts, park=False):
                last_park = 0.0
                rid = re.split(r"[@~]", label)[0]
                for t in ts:
                    if park and time.time() - last_park > 3.0:
                        gap = await park_near_copy(R, p, touch)
                        last_park = time.time()
                        if gap is not None:
                            notes.append(f"lens gap to copy {gap:.0f}px")
                    await p.evaluate(SEEK_JS, t)
                    await soft_wait(p, ANIMS_DONE_JS, 1500)
                    found = await p.evaluate(CONTRAST_TARGETS_JS)
                    targets = found["targets"]
                    seen.setdefault(rid, set()).update(found["all"])
                    for why, k in found["skipped"].items():
                        skipped[why] = max(skipped.get(why, 0), k)
                    if not targets:
                        continue
                    xs = [q for tg in targets for q in tg["rects"]]
                    x0 = max(0, min(q[0] for q in xs) - 4)
                    y0 = max(0, min(q[1] for q in xs) - 4)
                    x1 = min(W, max(q[2] for q in xs) + 4)
                    y1 = min(H, max(q[3] for q in xs) + 4)
                    clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
                    await p.evaluate(SEEK_JS, t)
                    A = await shot(p, clip)
                    st = await inject_style(p, NOGLYPH_CSS)
                    B = await shot(p, clip)
                    await st.remove()
                    sc = A.shape[1] / clip["width"]
                    for tg in targets:
                        rects = [[q[0] - x0, q[1] - y0, q[2] - x0, q[3] - y0] for q in tg["rects"]]
                        w, n, how = glyph_contrast(A, B, rects, tg["color"], sc)
                        if w is None:
                            continue
                        need = 3.0 if (tg["bar"] and tg["fs"] >= 18.66 and tg["fw"] >= 700) else 4.5
                        key = f"{rid} {tg['key']}"
                        if key not in out or w < out[key][0]:
                            out[key] = (w, need, f"at {label} t={t:.2f}s {how} n={n}")
                        measured.setdefault(rid, set()).add(tg["key"])

            seen, measured, skipped = {}, {}, {}
            # Home: at rest (the hint is up), then with the lens parked as close to the copy as the clamp allows
            await measure("home", times)
            await measure("home~lens", times, park=True)
            scroll_js = J("() => { __PRELUDE__ const s = $(S.scroll); return s ? [s.scrollHeight, s.clientHeight, s.scrollTop] : null; }")
            for rid in INNER:
                try:
                    await p.evaluate(UNFREEZE_JS)
                    await R.nav(p, rid)
                except Exception as e:
                    notes.append(f"{rid}: nav failed {str(e).splitlines()[0]}")
                    continue
                await measure(rid, times)
                if touch and W < 720:
                    await p.evaluate(UNFREEZE_JS)
                    if not await set_detent(R, p, "full"):
                        notes.append(f"{rid}: could not reach Full; the rest of the sheet was not measured")
                        continue
                    await measure(rid + "@full", times if full else short)
                # walk the sheet's content down in 60% steps so every line is read fully in view once
                sc = await p.evaluate(scroll_js)
                if sc and sc[0] > sc[1] + 8:
                    end = sc[0] - sc[1]
                    step = max(80, int(sc[1] * 0.6))
                    stops = list(range(step, end, step)) + [end]
                    for y in stops:
                        await p.evaluate(UNFREEZE_JS)
                        await p.evaluate(J("(y) => { __PRELUDE__ $(S.scroll).scrollTop = y; }"), y)
                        await stable(p, [SEL["bar"], SEL["sheet"], SEL["page"]], 6, 2000)
                        await measure(rid + ("@end" if y == end else f"@{y}"), times if full else short)
            for rid, keys in seen.items():
                missing = sorted(keys - measured.get(rid, set()))
                if missing:
                    notes.append(f"NEVER MEASURED on {rid}: " + "; ".join(missing[:4]))
            if skipped:
                notes.append("lines left out: " + ", ".join(f"{k} ×{v}" for k, v in sorted(skipped.items())))
        finally:
            await c.close()
    return out, notes, {rid: sorted(k - measured.get(rid, set())) for rid, k in seen.items()}


@check(20, "Contrast (brand book p16 method)", timeout=2400)
async def c20(R, res):
    vps = R.vps([PHONE, DESK])
    jobs = [(vp, tier) for vp in vps for tier in ("1", "1b", "2", "3")]
    results = await asyncio.gather(*(contrast_run(R, vp, tier, R.full_contrast) for vp, tier in jobs), return_exceptions=True)
    worst = None
    total = 0
    for (vp, tier), r in zip(jobs, results):
        if isinstance(r, Exception):
            res.add(False, f"{vp} tier {tier}: run", str(r).splitlines()[0])
            continue
        out, notes, never = r
        res.add(bool(out), f"{vp} tier {tier}: text measured", "; ".join(notes) or "no text found")
        for rid, keys in sorted(never.items()):
            res.add(not keys, f"{vp} tier {tier} {rid}: every text run was measured fully in view at least once", "; ".join(keys[:4]))
        for key, (w, need, how) in sorted(out.items(), key=lambda kv: kv[1][0]):
            total += 1
            if worst is None or w < worst[0]:
                worst = (w, f"{vp} tier {tier} {key} ({how})")
            res.add(w >= need, f"{vp} tier {tier} {key}: ≥ {need}:1", f"{w:.2f}:1 {how}")
        if R.verbose and notes:
            print(f"      {vp} tier {tier}: " + "; ".join(sorted(set(notes))[:8]))
    return f"{total} text runs × tiers 1/1b/2/3, 16 loop times + the brightest frame (4 brightest per scroll step); worst {worst[0]:.2f}:1 — {worst[1]}" if worst else "nothing measured"


# --------------------------------------------------------------------------------------
# Navigation and chrome (21-26)
# --------------------------------------------------------------------------------------
@check(21, "Relative hrefs answer 200", timeout=300)
async def c21(R, res):
    base = urllib.parse.urlparse(R.base)
    checked = {}
    for rid, (st, body, url) in R.raw_pages().items():
        ok_status = st == 200 or (rid == "404" and st in (200, 404))
        res.add(ok_status, f"{ROUTE_PATH[rid] or '/'} answers 200", f"HTTP {st}")
        if not ok_status:
            continue
        g = grab(body)
        for tag, attr, v, a in g.links:
            if v.startswith("#") or re.match(r"(mailto|tel|javascript):", v):
                continue
            if v.startswith("data:"):
                res.add(False, f"{rid}: no data: URI (CSP)", f"<{tag} {attr}>")
                continue
            u = urllib.parse.urlparse(v)
            if u.scheme in ("http", "https"):
                if u.netloc == base.netloc:
                    res.add(False, f"{rid}: <{tag} {attr}> is relative", f"own-origin absolute {v}")
                elif v.rstrip("/") + "/" == FOUNDING:
                    checked.setdefault(FOUNDING, None)
                continue
            if v.startswith("/"):
                if rid in ("404", "offline") and v.startswith(BASE_PATH):
                    target = (R.base + v[len(BASE_PATH):]) if not R.live else (R.origin + v)
                else:
                    res.add(False, f"{rid}: <{tag} {attr}> is relative", f"root-absolute {v}")
                    continue
            else:
                target = urllib.parse.urljoin(url, v)
            target = target.split("#")[0]
            checked.setdefault(target, None)
        tbk = [a.get("content") for t, a in g.tags if t == "meta" and a.get("name") == "tbk-root"]
        want = "../" if rid in INNER else "./"
        if rid in ("404", "offline"):
            res.add(tbk in ([want], [BASE_PATH]), f"{rid}: meta tbk-root is {want!r} or BASE_PATH", str(tbk))
        else:
            res.add(tbk == [want], f"{rid}: meta tbk-root is {want!r}", str(tbk))
    css = R.text("css/site.css")
    for m in re.finditer(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        v = m.group(1)
        if v.startswith("#"):
            continue
        if v.startswith("data:"):
            res.add(False, "site.css: no data: URI (CSP)", v[:40])
            continue
        checked.setdefault(urllib.parse.urljoin(R.base + "css/site.css", v), None)
    res.add(FOUNDING in checked, f"a Founding team link to {FOUNDING} exists", "no page links to it")
    bad = []
    for u in sorted(checked):
        if u == FOUNDING:
            st, h, body = http(u)
            title = re.search(r"<title>(.*?)</title>", body.decode("utf-8", "replace"), re.S)
            res.add(st == 200, f"Founding team link {FOUNDING} answers 200", f"HTTP {st} {body[:80]!r}" if st != 200 else "")
            continue
        st, _, _ = R.get(u, "HEAD")
        if st != 200:
            st, _, _ = R.get(u)
        if st != 200:
            bad.append(f"{u.replace(R.base, '')} {st}")
    res.add(not bad, f"{len(checked)} internal URLs answer 200", "; ".join(bad[:5]))
    return f"{len(checked)} URLs from 8 pages and site.css: relative (404/offline: BASE_PATH) and 200; Founding team link 200"


@check(22, "Zero third-party requests", timeout=300)
async def c22(R, res):
    total = 0
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp, permissions=["clipboard-read", "clipboard-write"])
        reqs = []
        c.on("request", lambda r: reqs.append(r.url))
        try:
            p = await R.page(c)
            await R.load(p, "")
            info = await R.lens(p)
            if VIEWPORTS[vp]["touch"]:
                t = await Touch.of(p)
                await t.drag(info["x"], info["y"], info["x"] - 60, info["y"] - 60)
            else:
                await mouse_path(p, info["x"], info["y"], info["x"] - 80, info["y"] - 60)
            for rid in ("house", "collection"):
                await R.nav(p, rid)
            await p.fill(SEL["notify_input"], "name@example.com")
            await click_notify(p)
            await wait_text(p, PREVIEW_OK, 5000)
            await R.nav(p, "commission")
            await fill_commission(p)
            await submit_commission(p)
            await wait_text(p, PREVIEW_OK, 5000)
            await R.nav(p, "contact")
            await p.locator(f"{SEL['page']} button").filter(has_text=re.compile(r"^\s*Copy\s*$")).first.click()
            await R.nav(p, "notices")
            await R.nav(p, "home")
        finally:
            await c.close()
        third = sorted({u for u in reqs if not u.startswith(R.origin) and not u.startswith("data:") and not u.startswith("blob:")})
        datas = [u[:40] for u in reqs if u.startswith("data:")]
        total += len(reqs)
        res.add(not third, f"{vp}: no third-party request over load, lens, five pages, two forms, copy", "; ".join(third[:4]))
        res.add(not datas, f"{vp}: no data: URI requests (CSP-ready)", "; ".join(datas[:3]))
    return f"{total} requests, all same-origin"


TAB_ORDER = ["house", "collection", "commission", "contact"]


@check(23, "Soft navigation", timeout=420)
async def c23(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        failed_resp = []
        c.on("response", lambda r: failed_resp.append(f"{r.status} {r.url.replace(R.base, '')}") if r.status >= 400 and r.url.startswith(R.origin) else None)
        # favicon fetches never surface as responses; Chrome still logs them
        c.on("console", lambda m: failed_resp.append(f"console: {m.text[:90]} @ {(m.location or {}).get('url', '')}") if m.type == "error" and "Failed to load resource" in m.text else None)
        try:
            p = await R.page(c)
            await R.load(p, "")
            await p.evaluate(J("() => { __PRELUDE__ window.__e2eProbe = 1; window.__e2eVid = $(S.video); }"))
            last = await p.evaluate(J("() => { __PRELUDE__ return $(S.video).currentTime; }"))
            for rid in TAB_ORDER:
                await R.nav(p, rid)
                s = await p.evaluate(J("""() => { __PRELUDE__ const v = $(S.video); return {probe: window.__e2eProbe === 1, same: v === window.__e2eVid && v.isConnected,
                    t: v.currentTime, paused: v.paused, title: document.title, route: document.body.dataset.route,
                    current: $$(S.tab).filter(a => a.getAttribute('aria-current') === 'page').map(a => a.textContent.trim())}; }"""))
                res.add(s["probe"] and s["same"], f"{vp} → {rid}: no reload, same film node", str(s))
                res.add(not s["paused"] and (s["t"] > last or last - s["t"] > 8), f"{vp} → {rid}: currentTime keeps increasing", f"{last:.2f} → {s['t']:.2f}")
                last = s["t"]
                res.add(s["title"] == TITLES[rid], f"{vp} → {rid}: document.title", repr(s["title"]))
                res.add(s["current"] == [TAB[rid]], f"{vp} → {rid}: aria-current on {TAB[rid]} only", str(s["current"]))
            # back / forward
            await p.go_back()
            await p.wait_for_function("r => document.body.dataset.route === r", arg="commission", timeout=6000)
            await R.settle(p)
            g = await p.evaluate("() => [window.__e2eProbe === 1, document.title, location.pathname]")
            cur = (await R.geom(p))["current"]
            res.add(g[0] and g[1] == TITLES["commission"] and cur == ["Commission"], f"{vp}: Back re-renders Commission without a reload", f"{g} {cur}")
            await p.go_forward()
            await p.wait_for_function("r => document.body.dataset.route === r", arg="contact", timeout=6000)
            await R.settle(p)
            g = await p.evaluate("() => [window.__e2eProbe === 1, document.title]")
            res.add(g[0] and g[1] == TITLES["contact"], f"{vp}: Forward returns to Contact", str(g))
            # same-URL click
            await p.evaluate(J("() => { __PRELUDE__ const m = $(S.page).firstElementChild; if (m) m.dataset.e2e = 'same'; window.__e2eLen = history.length; window.__e2eHref = location.href; }"))
            await R.click_tab(p, "Contact")
            await stable(p, [SEL["sheet"], SEL["page"], SEL["cam"]], 10, 3000)
            s = await p.evaluate(J("() => { __PRELUDE__ const m = $(S.page).firstElementChild; return {mark: !!m && m.dataset.e2e === 'same', len: history.length === window.__e2eLen, href: location.href === window.__e2eHref, probe: window.__e2eProbe === 1}; }"))
            res.add(all(s.values()), f"{vp}: a same-URL click is a no-op", str(s))
            # home: no current tab
            await R.nav(p, "home")
            g = await R.geom(p)
            res.add(g["current"] == [] and g["title"] == TITLES["home"], f"{vp}: Home has no current tab and title TABOUKHI", f"{g['current']} {g['title']!r}")
            res.add(not failed_resp, f"{vp}: no same-origin request fails during soft navigation", "; ".join(sorted(set(failed_resp))[:4]))
            # deep links
            for rid in INNER:
                p2 = await R.page(c)
                await R.load(p2, ROUTE_PATH[rid])
                g = await R.geom(p2)
                ev = R.station_eval(g) if R.media else None
                res.add(g["route"] == rid and g["title"] == TITLES[rid], f"{vp} deep link {rid}: route and title", f"{g['route']} {g['title']!r}")
                res.add(g["current"] == ([TAB[rid]] if rid in TAB else []), f"{vp} deep link {rid}: aria-current", str(g["current"]))
                res.add(ev is not None and ev["ok"] and ev["covers"], f"{vp} deep link {rid}: camera at its station", str(ev))
                if VIEWPORTS[vp]["w"] < 720:
                    res.add(g["sheet"]["state"] == "peek", f"{vp} deep link {rid}: phone sheet opens at Peek", g["sheet"]["state"])
                await p2.close()
        finally:
            await c.close()
    return "tabs swap content without reloading the film; titles, aria-current, back/forward, same-URL no-op, deep links at station"


async def push_counter(p):
    await p.evaluate("() => { if (window.__e2ePushWrapped) return; window.__e2ePushWrapped = 1; window.__e2ePush = 0; const o = history.pushState; history.pushState = function () { window.__e2ePush++; return o.apply(this, arguments); }; }")


async def seg_centre(p, label):
    return await p.evaluate(J("""(l) => { __PRELUDE__ const a = $$(S.tab).find(x => x.textContent.trim() === l); const r = a.getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2, r.width]; }"""), label)


async def thumb_centre(p):
    return await p.evaluate(J("() => { __PRELUDE__ const t = $(S.thumb); const r = t.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.width]; }"))


@check(24, "Tab bar", timeout=420)
async def c24(R, res):
    sw = await R.sweeps(R.vps(["320x568", "375x812", "390x844"]))
    for vp, s in sw.items():
        W = VIEWPORTS[vp]["w"]
        for k, snap in snaps(s):
            if "error" in snap or k in ("404", "offline"):
                continue
            res.add(not snap["barTrunc"], f"{vp} {k}: no tab label truncated", "; ".join(snap["barTrunc"][:2]))
            b = snap["bar"]
            res.add(b and b[0] >= -0.5 and b[0] + b[2] <= W + 0.5, f"{vp} {k}: the bar fits the viewport", str(b))
    for vp in R.vps([DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "house/")
            await push_counter(p)
            await p.evaluate("() => { window.__e2eProbe = 1; }")
            a = await seg_centre(p, "House")
            b = await seg_centre(p, "Commission")
            await mouse_path(p, a[0], a[1], b[0], b[1], steps=16, press=True)
            ok = await soft_wait(p, "r => document.body.dataset.route === r", 5000, arg="commission")
            await R.settle(p)
            th = await thumb_centre(p)
            cur = (await R.geom(p))["current"]
            res.add(ok and cur == ["Commission"], f"{vp}: a drag from House to Commission snaps and navigates", f"route ok={ok} current={cur}")
            res.add(abs(th[0] - b[0]) <= 3, f"{vp}: the thumb rests on the snapped segment", f"thumb x {th[0]:.1f} vs {b[0]:.1f}")
            n0 = await p.evaluate("() => window.__e2ePush")
            # a >6px drag that starts and ends on one tab: the click that follows must not add a navigation
            c0 = await seg_centre(p, "Contact")
            await mouse_path(p, c0[0] - 8, c0[1], c0[0] + 8, c0[1], steps=8, press=True)
            await soft_wait(p, ANIMS_DONE_JS, 2000)
            await R.settle(p)
            n1 = await p.evaluate("() => window.__e2ePush")
            probe = await p.evaluate("() => window.__e2eProbe === 1")
            res.add(probe and n1 - n0 <= 1, f"{vp}: the click after a drag is suppressed (one drag = at most one navigation)", f"pushState +{n1 - n0}, no reload={probe}")
            # arrows
            await R.nav(p, "house")
            await p.locator(SEL["tab"]).filter(has_text=re.compile(r"^\s*House\s*$")).first.focus()
            await p.keyboard.press("ArrowRight")
            f1 = await p.evaluate("() => document.activeElement.textContent.trim()")
            await p.keyboard.press("Enter")
            ok = await soft_wait(p, "r => document.body.dataset.route === r", 5000, arg="collection")
            res.add(f1 == "Collection" and ok, f"{vp}: ArrowRight moves focus to Collection, Enter opens it", f"focus={f1!r} navigated={ok}")
            await R.settle(p)
            await p.locator(SEL["tab"]).filter(has_text=re.compile(r"^\s*Collection\s*$")).first.focus()
            await p.keyboard.press("ArrowLeft")
            f2 = await p.evaluate("() => document.activeElement.textContent.trim()")
            res.add(f2 == "House", f"{vp}: ArrowLeft moves focus back", repr(f2))
        finally:
            await c.close()
    for vp in R.vps([PHONE]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "house/")
            await push_counter(p)
            t = await Touch.of(p)
            a = await seg_centre(p, "House")
            th0 = await thumb_centre(p)
            await t.down(a[0], a[1])
            for i in range(1, 6):
                await asyncio.sleep(0.016)
                await t.move(a[0] + 10 * i, a[1])
            await t.cancel()
            await stable(p, [SEL["thumb"]], 8, 2500)
            th1 = await thumb_centre(p)
            g = await R.geom(p)
            res.add(abs(th1[0] - th0[0]) <= 3 and g["route"] == "house", f"{vp}: pointercancel restores the thumb", f"thumb {th0[0]:.1f} → {th1[0]:.1f}, route {g['route']}")
            # C4: the drag works for touch as well as the mouse (implicit touch capture must not cancel it)
            a = await seg_centre(p, "House")
            b = await seg_centre(p, "Commission")
            await t.down(a[0], a[1])
            thx = []
            for i in range(1, 17):
                await asyncio.sleep(0.016)
                await t.move(a[0] + (b[0] - a[0]) * i / 16, a[1])
                if i == 12:
                    thx.append((await thumb_centre(p))[0])
            await t.up()
            ok = await soft_wait(p, "r => document.body.dataset.route === r", 5000, arg="commission")
            await R.settle(p)
            cur = (await R.geom(p))["current"]
            res.add(thx and abs(thx[0] - a[0]) > 20, f"{vp}: the thumb follows a touch drag", f"thumb x mid-drag {thx[0] if thx else None} (start {a[0]:.0f}, end {b[0]:.0f})")
            res.add(ok and cur == ["Commission"], f"{vp}: a touch drag from House to Commission snaps and navigates", f"route ok={ok} current={cur}")
            # The fold is exercised on Commission, the page long enough to scroll on every phone
            # (House no longer overflows the 390x844 sheet at Full since T-23 removed two items).
            if (await R.geom(p))["route"] != "commission":
                await R.nav(p, "commission")
            w0 = (await R.geom(p))["bar"][2]
            await set_detent(R, p, "full")
            await stable(p, [SEL["bar"]], 8, 2500)
            w1 = (await R.geom(p))["bar"][2]
            res.add(w1 < w0 * 0.6, f"{vp}: the bar folds at Full", f"width {w0:.0f} → {w1:.0f}")
            await p.locator(SEL["bar"]).first.tap()
            await stable(p, [SEL["bar"]], 8, 2500)
            w2 = (await R.geom(p))["bar"][2]
            res.add(abs(w2 - w0) <= 2, f"{vp}: a tap unfolds it", f"width {w2:.0f} vs {w0:.0f}")
            for y in range(30, 241, 30):
                await p.evaluate(J("(y) => { __PRELUDE__ $(S.scroll).scrollTop = y; }"), y)
                await p.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            await stable(p, [SEL["bar"]], 8, 2500)
            st = await p.evaluate(J("() => { __PRELUDE__ return $(S.scroll).scrollTop; }"))
            w3 = (await R.geom(p))["bar"][2]
            res.add(st > 24 and w3 < w0 * 0.6, f"{vp}: scrolling the sheet down > 24px folds it", f"scrollTop {st}, width {w3:.0f}")
            for y in (st - 20, st - 40, st - 60):
                await p.evaluate(J("(y) => { __PRELUDE__ $(S.scroll).scrollTop = y; }"), max(0, y))
                await p.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            await stable(p, [SEL["bar"]], 8, 2500)
            w4 = (await R.geom(p))["bar"][2]
            res.add(abs(w4 - w0) <= 2, f"{vp}: scrolling up unfolds it", f"width {w4:.0f} vs {w0:.0f}")
            await R.nav(p, "collection")
            w5 = (await R.geom(p))["bar"][2]
            res.add(abs(w5 - w0) <= 2 or (await R.geom(p))["sheet"]["state"] == "full", f"{vp}: the fold is recomputed on navigation", f"width {w5:.0f} vs {w0:.0f}")
        finally:
            await c.close()
    return "no truncation at 320/375/390; drag snaps and navigates; cancel restores; arrows; fold at Full and on scroll"


@check(25, "Detents", timeout=300)
async def c25(R, res):
    for vp in R.vps([PHONE]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "house/")
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            g = await R.geom(p)
            det = g["sheet"]["det"]
            exp = expected_sheet_rect(W, H, "peek", det)
            res.add(g["sheet"]["state"] == "peek" and abs(g["sheet"]["rect"][1] - exp[1]) <= 1.5, f"{vp}: arrives at Peek", f"{g['sheet']['state']} top {g['sheet']['rect'][1]:.1f} vs {exp[1]:.1f}")
            ok, note = detents_ok(W, H, det)
            res.add(ok, f"{vp}: Peek and Glance heights within the spec minimum and the 70%/50% caps (T-14)", note)
            fit = await p.evaluate(PEEK_FIT_JS, "[data-peek]")
            res.add(fit["b"] is not None and fit["b"] <= fit["bar"] - 36 + 0.5, f"{vp}: at Peek the page's last Peek block ends above the fade (T-14)", str(fit))
            t = await Touch.of(p)
            # at Peek the whole sheet is the drag surface: a drag on the title moves it
            h1 = await p.locator(SEL["h1"]).first.bounding_box()
            hx, hy = h1["x"] + min(40, h1["width"] / 2), h1["y"] + h1["height"] / 2
            ty0 = (await R.geom(p))["sheet"]["ty"]
            await t.down(hx, hy)
            ty_mid = ty0
            for i in range(1, 13):
                await asyncio.sleep(0.016)
                await t.move(hx, hy - 12 * i)
                if i == 10:
                    ty_mid = (await R.geom(p))["sheet"]["ty"]
            await t.up()
            res.add(ty0 - ty_mid >= 60, f"{vp}: at Peek a touch drag on the sheet body moves the sheet", f"translateY {ty0:.0f} → {ty_mid:.0f} mid-drag")
            await stable(p, [SEL["sheet"]], 8, 3000)
            await set_detent(R, p, "peek", touch_only=True)
            gb = await p.locator(SEL["grab"]).first.bounding_box()
            gx, gy = gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2
            await t.drag(gx, gy, gx, gy - (exp[1] - 64) - 20, steps=20, dt=0.018)
            await soft_wait(p, "() => document.querySelector(%s).dataset.state === 'full'" % json.dumps(SEL["sheet"]), 3000)
            await stable(p, [SEL["sheet"]], 8, 3000)
            g = await R.geom(p)
            res.add(g["sheet"]["state"] == "full" and abs(g["sheet"]["rect"][1] - 64) <= 1.5, f"{vp}: a grabber drag up reaches Full", f"{g['sheet']['state']} top {g['sheet']['rect'][1]:.1f}")
            res.add((await p.get_attribute(SEL["grab"], "aria-expanded")) == "true", f"{vp}: aria-expanded is true at Full")
            gb = await p.locator(SEL["grab"]).first.bounding_box()
            gx, gy = gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2
            # a fling: back-to-back moves and the lift right after the last one (T-06: the release
            # velocity is anchored at the lift, so a finger that stops first throws nothing). CDP
            # delivers touch moves one per frame (~33 ms apart here), so each step is 44 px: ~1300 px/s.
            await t.drag(gx, gy, gx, gy + 220, steps=5, dt=0.0)
            await soft_wait(p, "() => document.querySelector(%s).dataset.state === 'glance'" % json.dumps(SEL["sheet"]), 3000)
            await stable(p, [SEL["sheet"]], 8, 3000)
            g = await R.geom(p)
            eg = expected_sheet_rect(W, H, "glance", g["sheet"]["det"])
            fit = await p.evaluate(PEEK_FIT_JS, "#page h1")
            res.add(g["sheet"]["state"] != "glance" or (fit["b"] is not None and fit["b"] <= fit["bar"] - 36 + 0.5), f"{vp}: at Glance the title ends above the fade (T-14)", str(fit))
            res.add(g["sheet"]["state"] == "glance" and abs(g["sheet"]["rect"][1] - eg[1]) <= 1.5, f"{vp}: a fast fling down from Full lands on Glance (velocity rule)",
                    f"{g['sheet']['state']} top {g['sheet']['rect'][1]:.1f} vs {eg[1]:.1f}")
            await set_detent(R, p, "peek", touch_only=True)
            await p.locator(SEL["grab"]).first.tap()
            await stable(p, [SEL["sheet"]], 8, 3000)
            s1 = (await R.geom(p))["sheet"]["state"]
            await p.locator(SEL["grab"]).first.tap()
            await stable(p, [SEL["sheet"]], 8, 3000)
            s2 = (await R.geom(p))["sheet"]["state"]
            ae = await p.get_attribute(SEL["grab"], "aria-expanded")
            res.add(s1 == "full" and s2 == "peek" and ae == "false", f"{vp}: a grabber tap toggles Peek ↔ Full", f"{s1} → {s2}, aria-expanded {ae}")
            await p.focus(SEL["grab"])
            await p.keyboard.press("Enter")
            await stable(p, [SEL["sheet"]], 8, 3000)
            s3 = (await R.geom(p))["sheet"]["state"]
            res.add(s3 == "full", f"{vp}: Enter on the grabber toggles too", s3)
            # at Full with scrollTop 0 a downward drag moves the sheet
            await p.evaluate(J("() => { __PRELUDE__ $(S.scroll).scrollTop = 0; }"))
            sc = await p.locator(SEL["scroll"]).first.bounding_box()
            x, y = sc["x"] + sc["width"] / 2, sc["y"] + sc["height"] * 0.45
            ty0 = (await R.geom(p))["sheet"]["ty"]
            await t.down(x, y)
            ty_mid = ty0
            for i in range(1, 11):
                await asyncio.sleep(0.016)
                await t.move(x, y + 14 * i)
                if i == 8:
                    ty_mid = (await R.geom(p))["sheet"]["ty"]
            await t.up()
            res.add(ty_mid - ty0 >= 40, f"{vp}: at Full with scrollTop 0, a downward drag moves the sheet (handoff)", f"translateY {ty0:.0f} → {ty_mid:.0f} mid-drag")
            await stable(p, [SEL["sheet"]], 8, 3000)
            # focusing a field goes to Full
            await R.nav(p, "collection")
            s4 = (await R.geom(p))["sheet"]["state"]
            await p.locator(SEL["notify_input"]).first.tap()
            await soft_wait(p, "() => document.querySelector(%s).dataset.state === 'full'" % json.dumps(SEL["sheet"]), 3000)
            s5 = (await R.geom(p))["sheet"]["state"]
            res.add(s4 == "peek" and s5 == "full", f"{vp}: focusing a field moves the sheet to Full", f"{s4} → {s5}")
        finally:
            await c.close()
    return "Peek on arrival; drag up to Full; fling to Glance; tap and Enter toggle; handoff drag at scrollTop 0; focus → Full"


@check(26, "Morph", timeout=300)
async def c26(R, res):
    for vp in R.vps([PHONE, TABLET, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            await R.nav(p, "house")
            g = await R.geom(p)
            state = g["sheet"]["state"]
            want_state = "panel" if W >= 720 else "peek"
            got = g["sheet"]["rect"]
            res.add(state == want_state, f"{vp}: Home → House ends in data-state={want_state}", state)
            ok, note = panel_rect_ok(got, W, H, g["bar"], det=g["sheet"].get("det"))
            res.add(ok, f"{vp}: the sheet box equals the target rect (SPEC C5 layout)", note)
            # and the morph lands exactly where a deep link to /house/ rests (no jump after the morph)
            p2 = await R.page(c)
            await R.load(p2, "house/")
            rest = (await R.geom(p2))["sheet"]["rect"]
            await p2.close()
            res.add(all(abs(got[i] - rest[i]) <= 1.5 for i in range(4)), f"{vp}: the morph ends on the resting box of a deep-linked /house/",
                    f"{[round(x, 1) for x in got]} vs {[round(x, 1) for x in rest]}")
            res.add(g["sheet"]["clip"] in ("none", "") and g["sheet"]["mask"] in ("none", ""), f"{vp}: no clip-path or mask left", f"clip {g['sheet']['clip']} mask {g['sheet']['mask'][:60]}")
            rim = await p.evaluate(J("() => { __PRELUDE__ const s = cs($(S.sheet), '::before'); return [s.content, s.display, s.opacity, s.backgroundImage.slice(0, 40)]; }"))
            res.add(rim[0] not in ("none", "normal") and rim[1] != "none" and float(rim[2]) > 0.99, f"{vp}: the specular rim is visible", str(rim))
            # House → Home: the Home copy fades in where it stands (T-03). Sample its top every frame.
            await p.evaluate("""() => { window.__e2eCopy = []; const t0 = performance.now();
              const f = () => { const l = document.querySelector('#page .home__line');
                if (l) window.__e2eCopy.push([Math.round(performance.now() - t0), l.getBoundingClientRect().top, +getComputedStyle(l.closest('.home')).opacity * +getComputedStyle(document.getElementById('page')).opacity]);
                if (performance.now() - t0 < 1800) requestAnimationFrame(f); }; requestAnimationFrame(f); }""")
            await R.nav(p, "home")
            await p.wait_for_timeout(200)
            samples = await p.evaluate("() => window.__e2eCopy")
            rest_top = samples[-1][1] if samples else None
            vis = [q for q in samples if q[2] > 0.02]
            worst = max((abs(q[1] - rest_top) for q in vis), default=0) if rest_top is not None else 999
            res.add(rest_top is not None and worst <= 16, f"{vp}: the Home copy fades in at its own place (every visible frame within 16 px of rest)", f"worst {worst:.1f}px over {len(vis)} visible frames")
            g = await R.geom(p)
            r = g["sheet"]["rect"]
            res.add(g["sheet"]["state"] == "home" and abs(r[0]) <= 0.5 and abs(r[1]) <= 0.5 and abs(r[2] - W) <= 0.5 and abs(r[3] - H) <= 0.5,
                    f"{vp}: House → Home ends with the frost full-screen", f"{g['sheet']['state']} {[round(x, 1) for x in r]}")
            res.add("radial-gradient" in g["sheet"]["mask"] and g["sheet"]["clip"] in ("none", ""), f"{vp}: the lens hole is back, no clip-path", f"mask {g['sheet']['mask'][:60]} clip {g['sheet']['clip']}")
            if R.media and R.track:
                info = await R.lens(p)
                d, clamped, _ = R.lens_error(info)
                rmin, rmax = rmin_rmax(W)
                res.add((d <= 12 or clamped) and rmin - 0.5 <= info["lr"] <= rmax + 0.5, f"{vp}: the lens is at rest on the engraving", f"d={d:.1f} --lr={info['lr']}")
        finally:
            await c.close()
    return "frost gathers into the exact target rect with no clip/mask left and the rim on; and returns to full-screen frost with the lens at rest"


# --------------------------------------------------------------------------------------
# Forms (27-29)
# --------------------------------------------------------------------------------------
async def go_state(p):
    return await p.evaluate(J("""() => { __PRELUDE__ const b = $(S.notify_go); const s = cs(b); return {pe: s.pointerEvents, bg: s.backgroundColor, color: s.color}; }"""))


@check(27, "Collection form", timeout=300)
async def c27(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        reqs = []
        try:
            p = await R.page(c)
            await R.load(p, "collection/")
            s0 = await go_state(p)
            await p.fill(SEL["notify_input"], "name")
            s1 = await go_state(p)
            asleep = lambda s: s["pe"] == "none" and not close(parse_rgba(s["bg"]), TEXT_OK["bone"], tol=6)
            res.add(asleep(s0) and asleep(s1), f"{vp}: the button is asleep while the email is empty or invalid", f"{s0} {s1}")
            await p.fill(SEL["notify_input"], "name@example.com")
            await soft_wait(p, J("() => { __PRELUDE__ return cs($(S.notify_go)).pointerEvents !== 'none'; }"), 1500)
            await soft_wait(p, ANIMS_DONE_JS, 1500)
            s2 = await go_state(p)
            res.add(s2["pe"] != "none" and close(parse_rgba(s2["bg"]), TEXT_OK["bone"], tol=6) and close(parse_rgba(s2["color"]), ONYX, tol=6),
                    f"{vp}: a valid email wakes it (bone, onyx text)", str(s2))
            await p.fill(SEL["notify_input"], "name@")
            await p.press(SEL["notify_input"], "Enter")
            shown = await soft_wait(p, "t => document.querySelector('#page').innerText.includes(t)", 3000, arg="That address does not look complete.")
            inv = await p.get_attribute(SEL["notify_input"], "aria-invalid")
            foc = await p.evaluate(J("() => { __PRELUDE__ return document.activeElement === $(S.notify_input); }"))
            res.add(shown and inv == "true" and foc, f"{vp}: an invalid address shows its error, aria-invalid, focus", f"error shown={shown} aria-invalid={inv} focused={foc}")
            await p.fill(SEL["notify_input"], "name@example.com")
            before = await storage(p)
            p.on("request", lambda r: reqs.append(r.url))
            await click_notify(p)
            busy = await soft_wait(p, "() => !!document.querySelector('#page [aria-busy=\"true\"]')", 600)
            label = await p.evaluate(J("() => { __PRELUDE__ const b = $(S.notify_go); return b ? b.textContent.replace(/\\s+/g, ' ').trim() : ''; }"))
            res.add(busy and "Sending" in label, f"{vp}: sending shows aria-busy and 'Sending'", f"busy={busy} label={label!r}")
            ok = await soft_wait(p, "t => document.querySelector('#page').innerText.includes(t)", 5000, arg=PREVIEW_OK)
            await soft_wait(p, ANIMS_DONE_JS, 2000)
            txt = await p.evaluate("() => document.querySelector('#page').innerText")
            k = await p.evaluate(J("() => { __PRELUDE__ const u = $$('#page use').find(u => /#p-k$/.test(u.getAttribute('href') || '')); return u ? rect(u.ownerSVGElement) : null; }"))
            res.add(ok and "Thank you. On the live site we would write to name@example.com once, when the pieces are ready." in txt, f"{vp}: success names the address", "")
            res.add(k is not None and k[3] >= 16, f"{vp}: success shows the still K", str(k))
            foc = await p.evaluate("() => (document.activeElement && document.activeElement.textContent || '').trim().slice(0, 60)")
            res.add(foc.startswith("Thank you."), f"{vp}: focus moves to the success heading", repr(foc))
            after = await storage(p)
            res.add(not reqs, f"{vp}: zero requests fire on submit", "; ".join(reqs[:3]))
            res.add(before == after, f"{vp}: localStorage and sessionStorage unchanged", f"{before} → {after}")
            again = p.locator(f"{SEL['page']} a, {SEL['page']} button").filter(has_text=re.compile(r"^\s*Use another address\s*$")).first
            await robust_click(again)
            back = await soft_wait(p, J("() => { __PRELUDE__ return !!$(S.notify) && rendered($(S.notify)); }"), 3000)
            res.add(back, f"{vp}: 'Use another address' brings the form back")
        finally:
            await c.close()
    return "asleep until valid; error; aria-busy + Sending; success with the still K and the preview line; no requests; storage untouched"


@check(28, "Commission form", timeout=300)
async def c28(R, res):
    cases = [
        ("Your name, please.", dict(name=""), "name"),
        ("Your email, please.", dict(email=""), "email"),
        ("That address does not look complete.", dict(email="client@"), "email"),
        ("A few words about your idea, please.", dict(idea=""), "idea"),
        ("Add a phone number, or choose Email.", dict(reply="phone", phone=""), "phone"),
        ("Turn this on so we can answer you.", dict(consent=False), "consent"),
    ]
    for vp in R.vps([DESK, PHONE]):
        c = await R.ctx(vp)
        reqs = []
        try:
            p = await R.page(c)
            await R.load(p, "commission/")
            for msg, over, field in cases:
                await fill_commission(p, **over)
                await submit_commission(p)
                shown = await soft_wait(p, "t => document.querySelector('#page').innerText.includes(t)", 3000, arg=msg)
                st = await p.evaluate(J("""(f) => { __PRELUDE__ const form = $(S.cform); const el = form.querySelector('[name="' + f + '"]');
                    return {inv: el ? el.getAttribute('aria-invalid') : null, focus: document.activeElement === el,
                            n: $$('[aria-invalid="true"]', form).length}; }"""), field)
                res.add(shown and st["inv"] == "true", f"{vp}: '{msg}' fires when {field} is wrong", f"shown={shown} {st}")
                res.add(st["focus"], f"{vp}: focus goes to {field}", str(st))
            # segmented arrows
            await fill_commission(p)
            await p.locator(f"{SEL['cform']} {SEL['reply']}[value=\"email\"]").first.focus()
            await p.keyboard.press("ArrowRight")
            v = await p.evaluate(J("() => { __PRELUDE__ const r = $$(S.cform + ' ' + S.reply).find(x => x.checked); return r ? r.value : null; }"))
            res.add(v == "phone", f"{vp}: ArrowRight on Reply by selects Phone", repr(v))
            await p.keyboard.press("ArrowRight")
            v = await p.evaluate(J("() => { __PRELUDE__ const r = $$(S.cform + ' ' + S.reply).find(x => x.checked); return r ? r.value : null; }"))
            res.add(v == "either", f"{vp}: ArrowRight again selects Either", repr(v))
            await set_reply(p, "email")
            # switch
            sw_ = p.locator(f"{SEL['cform']} {SEL['consent']}").first
            a0 = await sw_.get_attribute("aria-checked")
            await sw_.click(force=True)
            a1 = await sw_.get_attribute("aria-checked")
            await sw_.click(force=True)
            a2 = await sw_.get_attribute("aria-checked")
            res.add(a0 != a1 and a1 != a2 and {a0, a1} == {"true", "false"}, f"{vp}: the switch toggles aria-checked", f"{a0} → {a1} → {a2}")
            await fill_commission(p)
            before = await storage(p)
            p.on("request", lambda r: reqs.append(r.url))
            await submit_commission(p)
            busy = await soft_wait(p, "() => !!document.querySelector('#page [aria-busy=\"true\"]')", 600)
            ok = await soft_wait(p, "t => document.querySelector('#page').innerText.includes(t)", 5000, arg=PREVIEW_OK)
            await soft_wait(p, ANIMS_DONE_JS, 2000)
            txt = await p.evaluate("() => document.querySelector('#page').innerText")
            k = await p.evaluate(J("() => { __PRELUDE__ const u = $$('#page use').find(u => /#p-k$/.test(u.getAttribute('href') || '')); return u ? rect(u.ownerSVGElement) : null; }"))
            res.add(busy, f"{vp}: sending shows aria-busy")
            res.add(ok and "Thank you. On the live site we would reply to client@example.com." in txt, f"{vp}: success names the address and says nothing was sent")
            res.add(k is not None and k[3] >= 16, f"{vp}: success shows the still K", str(k))
            res.add(not reqs, f"{vp}: zero requests fire on submit", "; ".join(reqs[:3]))
            res.add(before == await storage(p), f"{vp}: storage unchanged")
            again = p.locator(f"{SEL['page']} a, {SEL['page']} button").filter(has_text=re.compile(r"^\s*Start again\s*$")).first
            await robust_click(again)
            back = await soft_wait(p, J("() => { __PRELUDE__ return !!$(S.cform) && rendered($(S.cform)); }"), 3000)
            res.add(back, f"{vp}: 'Start again' brings the form back")
        finally:
            await c.close()
    return "each of the five errors fires in its condition with focus; segmented arrows; switch aria-checked; success as Collection"


@check(29, "Copy", timeout=120)
async def c29(R, res):
    for vp in R.vps([DESK]):
        c = await R.ctx(vp, permissions=["clipboard-read", "clipboard-write"])
        try:
            await c.add_init_script("(() => { try { const o = navigator.clipboard.writeText.bind(navigator.clipboard); navigator.clipboard.writeText = (t) => { window.__e2eClip = t; return o(t); }; } catch (e) {} })();")
            p = await R.page(c)
            await R.load(p, "contact/")
            await p.locator(f"{SEL['page']} button").filter(has_text=re.compile(r"^\s*Copy\s*$")).first.click()
            ok = await soft_wait(p, J("() => { __PRELUDE__ const t = $(S.toast); return !!t && rendered(t) && t.textContent.trim() === 'Copied.'; }"), 3000)
            role = await p.get_attribute(SEL["toast"], "role")
            txt = await p.evaluate(J("() => { __PRELUDE__ const t = $(S.toast); return t ? t.textContent.trim() : null; }"))
            res.add(ok and role == "status", f"{vp}: the toast reads 'Copied.' with role=status", f"text={txt!r} role={role}")
            try:
                clip = await p.evaluate("() => navigator.clipboard.readText()")
                how = "clipboard.readText"
            except Exception:
                clip = await p.evaluate("() => window.__e2eClip")
                how = "writeText argument (readText unavailable)"
            res.add(clip == EMAIL, f"{vp}: the clipboard holds {EMAIL}", f"{clip!r} via {how}")
            w = await p.evaluate("() => { const a = [...document.querySelectorAll('#page a')].find(x => x.textContent.trim() === 'Write'); return a ? a.getAttribute('href') : null; }")
            res.add(w == "mailto:" + EMAIL, f"{vp}: Write is mailto:{EMAIL}", repr(w))
        finally:
            await c.close()
    return f"clipboard holds {EMAIL}; toast 'Copied.' role=status; Write is a mailto"


# --------------------------------------------------------------------------------------
# Accessibility and layout (30-34)
# --------------------------------------------------------------------------------------
@check(30, "Layout and a11y basics", timeout=600)
async def c30(R, res):
    sw = await R.sweeps(R.vps(ALL_VP))
    for vp, s in sw.items():
        W = VIEWPORTS[vp]["w"]
        for k, snap in snaps(s):
            if "error" in snap:
                res.add(False, f"{vp} {k}", snap["error"])
                continue
            o = snap["overflow"]
            res.add(o["doc"] <= W and o["body"] <= W and (not o["sheet"] or o["sheet"][0] <= o["sheet"][1] + 1), f"{vp} {k}: no horizontal scroll", str(o))
            res.add(not snap["small"], f"{vp} {k}: interactive targets ≥ 44×44", "; ".join(snap["small"][:3]))
            small_in = [i for i in snap["inputs"] if i[1] < 16]
            res.add(not small_in, f"{vp} {k}: inputs ≥ 16px", str(small_in))
            res.add(snap["lang"] == "en", f"{vp} {k}: lang=en", repr(snap["lang"]))
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            await R.load(p, "")
            await p.keyboard.press("Tab")
            a = await p.evaluate("() => { const e = document.activeElement; const r = e.getBoundingClientRect(); return {t: e.textContent.trim(), href: e.getAttribute('href'), vis: r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < innerHeight}; }")
            res.add(a["t"] == SEL["skip_text"] and a["vis"], f"{vp}: the first Tab lands on a visible 'Skip to content'", str(a))
            await p.keyboard.press("Enter")
            await asyncio.sleep(0.1)
            f = await p.evaluate(J("() => { __PRELUDE__ const e = document.activeElement; const m = $(S.page); return !!m && (e === m || m.contains(e)); }"))
            res.add(f, f"{vp}: the skip link moves focus to the page content")
            await R.nav(p, "house")
            f = await p.evaluate(J("() => { __PRELUDE__ return document.activeElement === $(S.h1); }"))
            res.add(f, f"{vp}: Home → House focuses the h1")
            await R.nav(p, "collection")
            f = await p.evaluate(J("() => { __PRELUDE__ return document.activeElement === $(S.h1); }"))
            res.add(f, f"{vp}: House → Collection focuses the h1")
            ti = await p.evaluate(J("() => { __PRELUDE__ const h = $(S.h1); return h ? h.getAttribute('tabindex') : null; }"))
            res.add(ti == "-1", f"{vp}: the h1 carries tabindex=-1", repr(ti))
        finally:
            await c.close()
    return "no horizontal scroll at 8 viewports; targets ≥ 44; inputs ≥ 16px; skip link; h1 focused after navigation; lang=en"


def norm_matrix(t):
    if not t or t == "none":
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    nums = [float(x) for x in re.findall(r"-?[\d.]+(?:e-?\d+)?", t)]
    if t.startswith("matrix3d") and len(nums) >= 16:
        return tuple(round(x, 1) for x in (nums[0], nums[1], nums[4], nums[5], nums[12], nums[13]))
    return tuple(round(x, 1) for x in nums[:6])


@check(31, "Reduced motion", timeout=300)
async def c31(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp, reduced_motion="reduce")
        try:
            await c.add_init_script(ANIM_REC_INIT)
            p = await R.page(c)
            await R.load(p, "")
            playing = await soft_wait(p, PLAYING_JS, 8000)
            t0 = await p.evaluate(J("() => { __PRELUDE__ return $(S.video).currentTime; }"))
            await asyncio.sleep(1.0)
            t1 = await p.evaluate(J("() => { __PRELUDE__ return $(S.video).currentTime; }"))
            res.add(playing and (t1 - t0 > 0.4 or t0 - t1 > 8), f"{vp}: the film still plays", f"{t0:.2f} → {t1:.2f}")
            if R.media and R.track:
                for i in range(2):
                    d, clamped, _ = R.lens_error(await R.lens(p))
                    res.add(d <= 12 or clamped, f"{vp}: the lens still tracks the engraving (sample {i + 1})", f"d={d:.1f}")
                    await asyncio.sleep(0.8)
            bad = await p.evaluate(TRANS_SCAN_JS)
            res.add(not bad, f"{vp} home: no computed transition or animation longer than 200 ms", "; ".join(bad[:3]))
            await p.evaluate(REC_CAM_JS, [1500])
            await R.click_tab(p, "Commission")
            await p.wait_for_function("() => window.__e2eCam.done", timeout=6000)
            cam = await p.evaluate("() => window.__e2eCam.vals")
            distinct = []
            for _, v in cam:
                m = norm_matrix(v)
                if not distinct or distinct[-1] != m:
                    distinct.append(m)
            res.add(len(distinct) <= 2, f"{vp}: the camera move is instant (no intermediate frame)", f"{len(distinct)} distinct transforms: {distinct[:3]}")
            await R.settle(p)
            bad = await p.evaluate(TRANS_SCAN_JS)
            res.add(not bad, f"{vp} commission: no computed transition or animation longer than 200 ms", "; ".join(bad[:3]))
            await p.evaluate(ANIM_REC_JS, [2500])
            await R.nav(p, "contact")
            await R.nav(p, "home")
            seen = await p.evaluate("() => window.__e2eAn && window.__e2eAn.seen || {}")
            long_ = {k: v for k, v in seen.items() if v > 200.5}
            res.add(not long_, f"{vp}: no running animation longer than 200 ms during load and navigation", "; ".join(f"{k} {v:.0f}ms" for k, v in list(long_.items())[:3]))
        finally:
            await c.close()
    return "film plays and the lens tracks; nothing over 200 ms; the camera jumps"


@check(32, "Reduced transparency", timeout=300)
async def c32(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            cdp = await c.new_cdp_session(p)
            await cdp.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-transparency", "value": "reduce"}]})
            await p.goto(R.base, wait_until="load")
            await R.settle(p, first=True)
            m = await p.evaluate("() => matchMedia('(prefers-reduced-transparency: reduce)').matches")
            res.add(m, f"{vp}: prefers-reduced-transparency emulated")
            for rid in ("home", "house"):
                if rid == "house":
                    await R.nav(p, "house")
                st = await p.evaluate(J("""() => { __PRELUDE__ return S.glass.map(s => { const e = $(s); if (!e) return [s, 'missing']; const c = cs(e);
                    return [s, c.backdropFilter, c.webkitBackdropFilter, c.backgroundColor, c.backgroundImage.slice(0, 30), e.dataset.state || '']; }); }"""))
                for row in st:
                    if row[1] == "missing":
                        res.add(False, f"{vp} {rid}: {row[0]} present")
                        continue
                    s_, bf, wbf, bg, bgi, state = row
                    res.add((bf in ("none", None, "")) and (wbf in ("none", None, "")), f"{vp} {rid}: {s_} backdrop-filter none", f"{bf} / {wbf}")
                    col = parse_rgba(bg)
                    plain = close(col, (16, 16, 18, 0.97), tol=3, atol=0.03) and bgi == "none"
                    res.add(plain, f"{vp} {rid}: {s_} is the plain --paint-rt surface", f"{bg} {bgi}")
                if rid == "home":
                    g = await R.geom(p)
                    res.add("radial-gradient" in g["sheet"]["mask"], f"{vp}: the lens porthole is still cut in the sheet", g["sheet"]["mask"][:60])
                    info = await R.lens(p)
                    W = VIEWPORTS[vp]["w"]
                    img = await shot(p)
                    sc = img.shape[1] / W
                    h = info["lr"] * 0.6
                    inside = lap_var(img, dev_box([info["x"] - h, info["y"] - h, info["x"] + h, info["y"] + h], sc, img.shape))
                    outside = lap_var(img, dev_box(frost_box(info, W, VIEWPORTS[vp]["h"]), sc, img.shape))
                    res.add(inside > 4 * max(outside, 0.5), f"{vp}: the porthole shows the film through opaque paint", f"Laplacian inside {inside:.1f} vs paint {outside:.1f}")
        finally:
            await c.close()
    return "under reduced transparency the three surfaces are plain paint with no filter, and the Home porthole remains"


@check(33, "Tier 2 (ambient film)", timeout=300)
async def c33(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp)
        try:
            await c.add_init_script("(() => { window.__e2eSeeks = 0; document.addEventListener('seeking', (e) => { if (e.target && e.target.matches && e.target.matches(%s)) window.__e2eSeeks++; }, true); })();" % json.dumps(SEL["ambient"]))
            p = await R.page(c)
            await R.load(p, "", tier="2")
            s = await p.evaluate(J("""() => { __PRELUDE__ const a = $(S.ambient), v = $(S.video), sh = $(S.sheet);
                let op = 1; for (let e = a; e && e.nodeType === 1; e = e.parentElement) op *= parseFloat(cs(e).opacity);
                return {src: a ? a.currentSrc : '', display: a ? cs(a).display : 'missing', vis: a ? cs(a).visibility : '', op: op, rect: rect(a), paused: a ? a.paused : true,
                        clip: cs(v).clipPath, bf: cs(sh).backdropFilter, wbf: cs(sh).webkitBackdropFilter, bgi: cs(sh).backgroundImage, bgc: cs(sh).backgroundColor, state: sh.dataset.state}; }"""))
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            res.add("film-ambient-320" in s["src"] and s["display"] != "none" and s["vis"] == "visible" and s["op"] > 0.99 and covers(s["rect"], W, H) and not s["paused"],
                    f"{vp}: .film__ambient visible, full-screen, playing", str({k: s[k] for k in ("src", "display", "vis", "op", "rect", "paused")}))
            res.add(s["clip"].startswith("circle("), f"{vp}: the sharp video is clipped to a circle", s["clip"])
            res.add(s["bf"] in ("none", "", None) and s["wbf"] in ("none", "", None) and s["bgi"] == "none" and (parse_rgba(s["bgc"]) or (0, 0, 0, 0))[3] == 0,
                    f"{vp}: the Home sheet has no backdrop-filter and no background", f"backdrop {s['bf']} / {s['wbf']}, image {s['bgi']}, colour {s['bgc']}")
            await soft_wait(p, PLAYING_JS, 5000)
            await asyncio.sleep(1.0)
            ds = []
            t_end = time.time() + 10.0
            while time.time() < t_end:
                d = await p.evaluate(J("""() => { __PRELUDE__ const v = $(S.video), a = $(S.ambient); const D = isFinite(v.duration) ? v.duration : 16;
                    let d = v.currentTime - a.currentTime; while (d > D / 2) d -= D; while (d < -D / 2) d += D; return Math.max(-8, Math.min(8, d)); }"""))
                ds.append(d)
                await asyncio.sleep(0.25)
            seeks = await p.evaluate("() => window.__e2eSeeks")
            mx = max(abs(x) for x in ds) if ds else 99
            res.add(mx < 0.2, f"{vp}: ambient drift stays below 0.2 s over 10 s", f"max |d| {mx:.3f}s over {len(ds)} samples")
            res.add(seeks <= 1, f"{vp}: at most one ambient seek", f"{seeks} seeks")
        finally:
            await c.close()
    return "?tier=2: ambient pre-blurred film full-screen, sharp film clipped to the lens circle, drift < 0.2 s, ≤ 1 seek"


@check(34, "No JS", timeout=300)
async def c34(R, res):
    for vp in R.vps([PHONE, DESK]):
        c = await R.ctx(vp, js=False)
        try:
            p = await R.page(c)
            W, H = VIEWPORTS[vp]["w"], VIEWPORTS[vp]["h"]
            for rid, path in ROUTE_PATH.items():
                await p.goto(R.base + path, wait_until="load")
                s = await p.evaluate(J("""() => { __PRELUDE__ const m = $(S.page), st = $(S.still), h = $('h1');
                    return {text: m ? m.innerText.trim().length : (document.body.innerText.trim().length), h1: h ? h.textContent.trim() : '', h1vis: h ? rendered(h) : false,
                            mailto: $$('#page a[href^="mailto:"], #page form[action^="mailto:"]').length,
                            still: st ? {ok: st.complete && st.naturalWidth > 0, vis: rendered(st), rect: rect(st)} : null,
                            cls: document.documentElement.className}; }"""))
                res.add(s["text"] > 20, f"{vp} {rid}: content renders without JS", f"{s['text']} chars")
                if rid in INNER or rid in ("404", "offline"):
                    res.add(bool(s["h1"]) and s["h1vis"], f"{vp} {rid}: the title is visible", repr(s["h1"]))
                if rid in ("collection", "commission"):
                    res.add(s["mailto"] >= 1, f"{vp} {rid}: the form carries a mailto: fallback", f"{s['mailto']} mailto links/forms")
                st = s["still"]
                res.add(st and st["ok"] and st["vis"] and covers(st["rect"], W, H), f"{vp} {rid}: the film poster is visible", str(st))
                if rid == "home":
                    lv = await p.evaluate(J("() => { __PRELUDE__ const s = cs($(S.sheet)); return [s.getPropertyValue('--lx').trim(), s.getPropertyValue('--ly').trim(), s.getPropertyValue('--lr').trim(), (s.maskImage && s.maskImage !== 'none') ? s.maskImage : s.webkitMaskImage]; }"))
                    want = ["54%", "43%", "110px"] if W < 720 else ["50%", "48%", "150px"]
                    res.add(lv[:3] == want and "radial-gradient" in (lv[3] or ""), f"{vp}: the Home lens hole sits at its CSS default {want}", str(lv[:3]) + " " + (lv[3] or "")[:40])
                    cx, cy, r = float(want[0][:-1]) / 100 * W, float(want[1][:-1]) / 100 * H, float(want[2][:-2])
                    a = await shot(p)
                    stt = await inject_style(p, MASK_OFF_CSS)
                    b = await shot(p)
                    await stt.remove()
                    h = r * 0.6
                    db = dev_box([cx - h, cy - h, cx + h, cy + h], a.shape[1] / W, a.shape)
                    va, vb = lap_var(a, db), lap_var(b, db)
                    res.add(va > 2 * max(vb, 0.5), f"{vp}: the hole is really open at the default position", f"Laplacian {va:.1f} vs closed {vb:.1f}")
        finally:
            await c.close()
    return "every page renders, forms carry mailto:, the Home hole sits at its default, the poster shows"


# --------------------------------------------------------------------------------------
# Hosting (35-39)
# --------------------------------------------------------------------------------------
@check(35, "noindex while DEMO", timeout=60)
async def c35(R, res):
    for rid, (st, body, url) in R.raw_pages().items():
        if st not in (200, 404):
            res.add(False, f"{rid} answers", f"HTTP {st}")
            continue
        g = grab(body)
        robots = [a.get("content", "") for t, a in g.tags if t == "meta" and a.get("name", "").lower() == "robots"]
        res.add(any("noindex" in r for r in robots), f"{rid}: meta robots noindex", str(robots))
    st, _, body = R.get("robots.txt")
    txt = body.decode("utf-8", "replace")
    res.add(st == 200 and re.search(r"(?im)^\s*User-agent:\s*\*", txt) and re.search(r"(?im)^\s*Disallow:\s*/\s*$", txt), "robots.txt: User-agent * / Disallow: /", f"HTTP {st} {txt[:80]!r}")
    return "noindex on all 8 pages; robots.txt disallows everything (DEMO)"


@check(36, "Manifest and icons", timeout=60)
async def c36(R, res):
    st, _, body = R.get("")
    g = grab(body.decode("utf-8", "replace")) if st == 200 else grab("")
    links = [a for t, a in g.tags if t == "link"]
    man = [a.get("href") for a in links if a.get("rel") == "manifest"]
    res.add(len(man) == 1, "index links one manifest", str(man))
    if not man:
        return ""
    murl = urllib.parse.urljoin(R.base, man[0])
    st, h, body = R.get(murl)
    try:
        m = json.loads(body)
    except Exception as e:
        res.add(False, "manifest parses as JSON", str(e))
        return ""
    res.add(True, "manifest parses")
    res.add(m.get("name") == "TABOUKHI" and m.get("short_name") == "TABOUKHI", "name and short_name TABOUKHI", f"{m.get('name')} / {m.get('short_name')}")
    res.add(m.get("start_url") == "./" and m.get("scope") == "./", "start_url and scope ./", f"{m.get('start_url')} / {m.get('scope')}")
    res.add(m.get("display") == "standalone", "display standalone", str(m.get("display")))
    res.add(str(m.get("background_color", "")).lower() == "#020202" and str(m.get("theme_color", "")).lower() == "#020202", "background and theme #020202", f"{m.get('background_color')} {m.get('theme_color')}")
    icons = m.get("icons", [])
    res.add(len(icons) >= 3 and any("maskable" in (i.get("purpose") or "") for i in icons), "icons include 192, 512 and a maskable", str([i.get("src") for i in icons]))
    bad = []
    for i in icons:
        u = urllib.parse.urljoin(murl, i["src"])
        s2, h2, _ = R.get(u)
        if s2 != 200 or not h2.get("content-type", "").startswith("image/"):
            bad.append(f"{i['src']} {s2} {h2.get('content-type')}")
    for a in links:
        if a.get("rel") in ("icon", "apple-touch-icon"):
            u = urllib.parse.urljoin(R.base, a.get("href", ""))
            s2, h2, _ = R.get(u)
            if s2 != 200:
                bad.append(f"{a.get('href')} {s2}")
    res.add(not bad, "every icon answers 200 as an image", "; ".join(bad))
    res.add("json" in h.get("content-type", ""), "manifest served as JSON", h.get("content-type", ""))
    return f"manifest parses; {len(icons)} icons 200"


@check(37, "Service worker source", timeout=60)
async def c37(R, res):
    st, _, body = R.get("sw.js")
    res.add(st == 200, "sw.js answers 200", f"HTTP {st}")
    if st != 200:
        return ""
    src = body.decode("utf-8", "replace")
    code = strip_js_comments(src)
    res.add(re.search(r"cache\s*:\s*['\"]reload['\"]", code), "network-first uses cache:'reload'")
    res.add(re.search(r"['\"]tbks-[0-9a-f]{10}['\"]", code), "VERSION is 'tbks-' + 10 hex (build stamp)", (re.search(r"VERSION[^\n]{0,60}", code) or [""])[0])
    pref = [r"['\"]tbks-['\"]"] + [re.escape(n) for n in re.findall(r"(\w+)\s*=\s*['\"]tbks-['\"]", code)]
    pref_re = "|".join(pref)
    res.add(re.search(r"startsWith\(\s*(" + pref_re + r")\s*\)|/\^tbks-/|indexOf\(\s*(" + pref_re + r")\s*\)\s*===?\s*0", code), "activate deletes only tbks- caches")
    guards = re.findall(r"(\w+)\s*=\s*[!\s(]*(?:self\.)?registration\.active", code)
    pat = r"registration\.active" + "".join(r"|\b" + re.escape(gv) + r"\b" for gv in guards)
    for fn in ("clients.claim", "skipWaiting"):
        for m in re.finditer(re.escape(fn) + r"\s*\(", code):
            window = code[max(0, m.start() - 600):m.start()]
            ok = re.search(r"if\s*\([^)]*(" + pat + r")", window) or re.search(r"(" + pat + r")[^;\n]{0,40}(&&|\?)[^;\n]*$", window)
            res.add(bool(ok), f"{fn}() only when a worker was already active", code[max(0, m.start() - 80):m.start() + 20].replace("\n", " "))
    reg = [u for u in (R.module_graph(R.base, R.text(""))[1]) if "sw-register" in u]
    if reg:
        rs = strip_js_comments(R.text(reg[0]))
        res.add("https:" in rs and "serviceWorker" in rs, "the worker registers only on https", "no https: guard in sw-register.js")
    else:
        res.add(False, "sw-register.js is in the module graph")
    return "cache:'reload', tbks- stamp and prefix filter, claim/skipWaiting guarded by registration.active, https-only registration"


@check(38, "modulepreload matches imports", timeout=60)
async def c38(R, res):
    for rid, (st, body, url) in R.raw_pages().items():
        if st != 200 or rid == "404":
            continue
        g = grab(body)
        pre = [urllib.parse.urljoin(url, a.get("href")) for t, a in g.tags if t == "link" and a.get("rel") == "modulepreload"]
        entries, graph = R.module_graph(url, body)
        if not entries:
            res.add(rid == "offline" or False, f"{rid}: has a module entry script")
            continue
        missing = sorted(graph - set(pre) - set(entries))
        extra = sorted(set(pre) - graph)
        dup = len(pre) != len(set(pre))
        res.add(not missing and not extra and not dup, f"{rid}: modulepreload = static import graph ({len(graph)} modules)",
                f"missing {[m.rsplit('/', 1)[-1] for m in missing]} extra {[e.replace(R.base, '') for e in extra]} dup={dup}")
        res.add(not any("?" in u for u in pre), f"{rid}: modulepreload links are unversioned", str([u for u in pre if "?" in u][:3]))
    return "every page preloads exactly its static import graph, unversioned"


@check(39, "404 renders styled on Pages", timeout=120)
async def c39(R, res):
    for vp in R.vps([DESK]):
        c = await R.ctx(vp)
        try:
            p = await R.page(c)
            url = (R.origin + BASE_PATH + "does/not/exist/") if R.live else (R.base + "taboukhi-site/does/not/exist/")
            resp = await p.goto(url, wait_until="load")
            await soft_wait(p, "() => document.fonts.status === 'loaded'", 3000)
            s = await p.evaluate(J("""() => { __PRELUDE__ const sheets = Array.from(document.styleSheets).map(x => { try { return [x.href, x.cssRules.length]; } catch (e) { return [x.href, -1]; } });
                const h = $('h1'); const k = $$('use').filter(u => /#p-k$/.test(u.getAttribute('href') || '')).map(u => rect(u.ownerSVGElement));
                return {bg: cs(document.documentElement).backgroundColor, sheets: sheets, h1: h ? h.textContent.trim() : '', h1vis: h ? rendered(h) : false,
                        font: h ? cs(h).fontFamily : '', k: k, title: document.title,
                        home: (Array.from(document.querySelectorAll('a')).find(a => a.textContent.includes('Go to the house')) || {}).href || null}; }"""))
            res.add(resp is not None and resp.status == 404, f"{'live' if R.live else 'emulated Pages'}: the unknown path answers 404", str(resp.status if resp else None))
            res.add(any((sh[0] or "").endswith("site.css") and sh[1] > 20 for sh in s["sheets"]), "site.css loaded from BASE_PATH", str(s["sheets"]))
            res.add(close(parse_rgba(s["bg"]), ONYX, tol=2), "onyx background", s["bg"])
            res.add(s["h1"] == "Nothing here." and s["h1vis"], "title 'Nothing here.' is shown", repr(s["h1"]))
            res.add("Times" not in s["font"] and ("apple-system" in s["font"] or "Inter" in s["font"]), "system/Inter type, not the browser default", s["font"][:60])
            res.add(any(k and k[3] >= 16 for k in s["k"]), "the still K is shown ≥ 16px", str(s["k"]))
            res.add(s["title"] == TITLES["404"], "document.title", repr(s["title"]))
            want_home = (R.origin + BASE_PATH) if R.live else (R.base + "taboukhi-site/")
            res.add(s["home"] in (want_home, R.base), "'Go to the house' goes to the site root", repr(s["home"]))
        finally:
            await c.close()
    return ("live" if R.live else "emulated Pages (BASE_PATH mapped to the local build)") + ": /taboukhi-site/does/not/exist/ renders the styled 404"


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def parse_only(s):
    out = set()
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def parse_vp(s):
    if not s:
        return list(ALL_VP)
    out = []
    for part in s.split(","):
        part = part.strip().lower()
        if part in VIEWPORTS:
            out.append(part)
        elif part == "phone":
            out += [v for v in ALL_VP if VIEWPORTS[v]["w"] < 720]
        elif part == "tablet":
            out += [v for v in ALL_VP if 720 <= VIEWPORTS[v]["w"] < 1080]
        elif part == "desktop":
            out += [v for v in ALL_VP if VIEWPORTS[v]["w"] >= 1080 or v == "1024x768"]
        elif part.isdigit():
            out += [v for v in ALL_VP if VIEWPORTS[v]["w"] == int(part)]
        else:
            raise SystemExit(f"unknown viewport {part!r}; use one of {', '.join(ALL_VP)}, phone, tablet, desktop")
    return list(dict.fromkeys(out))


def start_server(base):
    u = urllib.parse.urlparse(base)
    if u.hostname not in ("localhost", "127.0.0.1"):
        return None
    port = u.port or 80
    if port_open(port):
        return None
    serve = ROOT / "serve.py"
    if not serve.exists():
        raise SystemExit(f"nothing listens on :{port} and {serve} does not exist")
    env = dict(os.environ, PORT=str(port))
    proc = subprocess.Popen([sys.executable, str(serve)], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    t_end = time.time() + 10
    while time.time() < t_end:
        if port_open(port):
            return proc
        if proc.poll() is not None:
            raise SystemExit(f"serve.py exited with code {proc.returncode}")
        time.sleep(0.1)
    proc.terminate()
    raise SystemExit(f"serve.py did not listen on :{port} within 10 s")


def stop_server(proc):
    if not proc:
        return
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:
        proc.kill()


async def run(args):
    from playwright.async_api import async_playwright
    base = (LIVE_BASE if args.live else (args.base or DEFAULT_BASE)).rstrip("/") + "/"
    live = not re.match(r"https?://(localhost|127\.0\.0\.1)", base)
    R = Runner(base, live, parse_vp(args.viewport), args.verbose)
    R.full_contrast = args.full_contrast
    only = parse_only(args.only) if args.only else set(CHECKS)
    nums = sorted(n for n in CHECKS if n in only)
    st, _, _ = R.get("")
    print(f"TABOUKHI e2e — {base}  ({'live' if live else 'local'}; viewports {', '.join(R.vp_filter)})")
    results = {}
    if st != 200:
        for n in nums:
            results[n] = (False, f"{base} answered HTTP {st}; nothing to test")
            print(f"[{n:02d}] FAIL  {CHECKS[n]['title']} — {results[n][1]}")
        return results, R
    R.load_media()
    async with async_playwright() as p:
        R.browser = await p.chromium.launch(channel="chrome")
        print(f"Chrome {R.browser.version}")
        try:
            for n in nums:
                meta = CHECKS[n]
                res = Result()
                t0 = time.time()
                try:
                    summary = await asyncio.wait_for(meta["fn"](R, res), timeout=meta["timeout"])
                    if not res.subs:
                        ok, reason = None, "no viewport of this check is in the --viewport filter"
                    else:
                        ok, reason = res.ok, res.reason(summary or "")
                except asyncio.TimeoutError:
                    ok, reason = False, f"timed out after {meta['timeout']} s" + (f"; before that: {res.reason()}" if res.subs and not res.ok else "")
                except Exception as e:
                    tb = traceback.extract_tb(e.__traceback__)
                    where = next((f"line {f.lineno}" for f in reversed(tb) if f.filename.endswith("e2e.py")), "")
                    first = (str(e).strip().splitlines() or [type(e).__name__])[0]
                    ok, reason = False, f"{type(e).__name__} at {where}: {first[:240]}" + (f"; before that: {res.reason()}" if res.subs and not res.ok else "")
                results[n] = (ok, reason)
                tag = "SKIP" if ok is None else "PASS" if ok else "FAIL"
                print(f"[{n:02d}] {tag}  {meta['title']} — {reason}  ({time.time() - t0:.0f}s)", flush=True)
                if R.verbose:
                    for s in res.subs:
                        print(f"      {'ok  ' if s[0] else 'FAIL'} {s[1]}" + (f"  [{s[2]}]" if s[2] else ""))
        finally:
            try:
                await asyncio.wait_for(R.browser.close(), 10)
            except Exception:
                print("  (browser.close() did not return within 10 s; moving on)")
    return results, R


def main():
    ap = argparse.ArgumentParser(description="TABOUKHI website demo acceptance suite (SPEC §9)")
    ap.add_argument("base", nargs="?", help=f"base URL (default {DEFAULT_BASE})")
    ap.add_argument("--live", action="store_true", help=f"test {LIVE_BASE}")
    ap.add_argument("--only", help="check numbers, e.g. 1,2,20-26")
    ap.add_argument("--viewport", help="viewports: " + ", ".join(ALL_VP) + ", phone, tablet, desktop, or a width")
    ap.add_argument("--full-contrast", action="store_true", help="check 20: all 17 loop times at every scroll position (default: 17 at arrival, the 4 brightest elsewhere)")
    ap.add_argument("--list", action="store_true", help="list the checks and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every sub-assertion")
    args = ap.parse_args()
    if args.list:
        for n in sorted(CHECKS):
            print(f"{n:2d}  {CHECKS[n]['title']}")
        return 0
    base = (LIVE_BASE if args.live else (args.base or DEFAULT_BASE))
    proc = None if args.live else start_server(base)
    if proc:
        print(f"(started serve.py on :{urllib.parse.urlparse(base).port}, pid {proc.pid})")
    t0 = time.time()
    try:
        results, R = asyncio.run(run(args))
    finally:
        stop_server(proc)
    passed = sum(1 for ok, _ in results.values() if ok is True)
    failed = sum(1 for ok, _ in results.values() if ok is False)
    skipped = sum(1 for ok, _ in results.values() if ok is None)
    if R.console:
        uniq = list(dict.fromkeys(R.console))
        print(f"\nconsole/page errors seen ({len(uniq)} unique): " + " | ".join(u[:160] for u in uniq[:5]))
    if skipped:
        print(f"({skipped} skipped by the viewport filter)")
    print(f"\n{passed} passed, {failed} failed  ({time.time() - t0:.0f}s)")
    return failed


if __name__ == "__main__":
    sys.exit(main())
