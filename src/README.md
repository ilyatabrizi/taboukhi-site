# TABOUKHI — website demo

TABOUKHI is the first eyewear brand established by Ilya Taboukhi. This repository is the
demo of its website: one film of the engraved temple arm, one sheet of glass in front of
it, two floating capsules. The full build spec is `SPEC.md` (kept out of the repo).

## Build and preview

```sh
python3 build.py            # src/ -> index.html, house/ … notices/, 404.html, offline.html, css/site.css, sw.js
python3 serve.py            # http://localhost:8261 (byte ranges, Pages-style 404, /taboukhi-site/ alias)
python3 serve.py --pages    # the same with GitHub Pages' max-age=600
```

Nothing is deployed from here by the build. `build.py` refuses to build if a house rule is
broken: a banned word, an exclamation mark in copy, a `style=""` attribute, a `data:` URI, a
`url()` inside a custom property, or anything but exactly three selectors (`.capsule`,
`.bar`, `.sheet`) carrying `backdrop-filter`.

## CSP (for taboukhi.com)

One inline script, identical on every page. Its hash for `script-src`:

```
'{{CSP_HASH}}'
```

No `style=""` in the HTML (JS writes through the CSSOM), no `data:` URIs, no CDN, no web
fonts from anywhere else.

## What shipped

- Service worker cache: `{{VERSION}}` (prefix `tbks-`; only these caches are ever deleted).
- Home copy position (SPEC §5 Contrast): **{{COPY_POSITION}}** — bottom-left on the thick frost.
- Modules (each has a matching unversioned `<link rel="modulepreload">`):
{{MODULES}}

## Performance tiers

`?tier=1|1b|2|3` forces a tier for testing (and disables the probe).
1 full design · 1b regular glass on the sheet · 2 Home frost without backdrop-filter
(pre-blurred ambient film + sharp film clipped to the lens) · 3 paint only.
WebKit before 18 (only `-webkit-backdrop-filter` supported: Safari 17, and Chrome, Instagram and home-screen apps on iOS 17) is sent to tier 2 automatically, because it ignores a mask on a backdrop-filtered element.

## Placeholders on this preview (every `data-ph` line)

{{PLACEHOLDERS}}

## Credits

Film, wordmark and symbol: TABOUKHI. Inter by Rasmus Andersson (SIL OFL). Interface
elements adapted from Uiverse.io (MIT) — authors and source files are named above each
block in `src/css/`, and on the Notices page.
