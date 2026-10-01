#!/usr/bin/env python3
"""TABOUKHI website demo: build.

    python3 build.py          # src/ -> every page at the repo root, css/site.css, sw.js, robots.txt

What it writes (SPEC §2):
  index.html, house/, collection/, commission/, contact/, notices/, 404.html, offline.html
  css/site.css             src/css/*.css concatenated in a fixed order
  sw.js                    VERSION = 'tbks-' + sha1(every built file)[:10]
  manifest.webmanifest, robots.txt, .nojekyll, README.md (records the inline-script hash)

It refuses to build (exit 1) when a house rule is broken: a banned word, an "!" in visible
copy, a style="" attribute, a data: URI, a url() inside a custom property, or anything but
exactly three selectors carrying backdrop-filter.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

# At go-live only SITE changes (SPEC §2).
SITE = {"DEMO": True, "BASE_URL": "https://ilyatabrizi.github.io/taboukhi-site/", "BASE_PATH": "/taboukhi-site/"}

# The Home copy position that shipped (SPEC §5 Contrast): "bottom" = bottom-left on the frost,
# "top" = the black band under the capsule. Measured in dev: see README.
COPY_POSITION = "bottom"

INLINE_SCRIPT = ("document.documentElement.className='js';"
                 "setTimeout(function(){if(!window.__tbk)document.documentElement.className='no-js'},4000)")

CSS_ORDER = ["tokens", "base", "glass", "film", "chrome", "sheet", "lens", "controls", "forms", "pages", "fallback"]

PAGES = [
    # name, output path, depth-relative root (None = BASE_PATH absolute), stage
    ("home", "index.html", "./", "sheet"),
    ("house", "house/index.html", "../", "sheet"),
    ("collection", "collection/index.html", "../", "sheet"),
    ("commission", "commission/index.html", "../", "sheet"),
    ("contact", "contact/index.html", "../", "sheet"),
    ("notices", "notices/index.html", "../", "sheet"),
    # Served at any depth (Pages' 404, the worker's offline fallback): absolute BASE_PATH.
    ("404", "404.html", None, "void"),
    ("offline", "offline.html", None, "void"),
]

TABS = [("house", "House"), ("collection", "Collection"), ("commission", "Commission"), ("contact", "Contact")]

GO = ('<svg class="go" viewBox="0 0 10 10" aria-hidden="true" focusable="false">'
      '<path class="st" d="M0 5h7"/><path class="ch" d="M1 1l4 4-4 4"/></svg>')
EXT = ('<svg class="go go--ext" viewBox="0 0 10 10" aria-hidden="true" focusable="false">'
       '<path d="M2.5 7.5l5-5"/><path d="M3.5 2.5h4v4"/></svg>')
# njesenberger's lens pair (Toggle-switches/njesenberger_friendly-otter-40), glyphs removed.
SWITCH = ('<svg class="sw__svg" viewBox="0 0 292 142" aria-hidden="true" focusable="false">'
          '<path class="sw__track" d="M71 142C31.7878 142 0 110.212 0 71C0 31.7878 31.7878 0 71 0C110.212 0 119 30 146 30'
          'C173 30 182 0 221 0C260 0 292 31.7878 292 71C292 110.212 260.212 142 221 142C181.788 142 173 112 146 112'
          'C119 112 110.212 142 71 142Z"/>'
          '<g filter="url(#goo)">'
          '<rect class="sw__pill" rx="29" height="58" width="116" y="42" x="13"/>'
          '<rect class="sw__disc sw__disc--l" rx="57" height="114" width="114" y="14" x="14"/>'
          '<rect class="sw__disc sw__disc--r" rx="57" height="114" width="114" y="14" x="164"/>'
          '</g></svg>')

CREDITS = [  # SPEC §10, word for word
    ("Spacious74", "specular rim light (Cards/Spacious74_moody-owl-18)"),
    ("Mohsinech", "label roll on the primary button (Buttons/Mohsinech_tidy-yak-66)"),
    ("Yaya12085", "email field with an inset button (Inputs/Yaya12085_evil-chipmunk-15)"),
    ("PhanDangKhoa96", "rim light on focus (Inputs/PhanDangKhoa96_swift-warthog-78), with Akshdeep74644 (Inputs/Akshdeep74644_dry-mouse-37)"),
    ("zanina-yassine", "segmented control (Buttons/zanina-yassine_curly-shrimp-52)"),
    ("cbolson", "segmented control hover lean (Toggle-switches/cbolson_slippery-treefrog-30)"),
    ("njesenberger", "liquid switch (Toggle-switches/njesenberger_friendly-otter-40)"),
    ("SelfMadeSystem", "rim ripple (Buttons/SelfMadeSystem_afraid-rabbit-12)"),
    ("elijahgummer", "chevron-to-arrow link (Buttons/elijahgummer_wise-jellyfish-93)"),
]

PAGE_NAMES = {"home": "Home", "house": "The house", "collection": "Collection", "commission": "Commission",
              "contact": "Contact", "notices": "Notices", "404": "Nothing here", "offline": "Offline"}

BANNED = re.compile(r"titanium|luxury|luxurious|exciting|passionate", re.I)


def fail(msg: str) -> None:
    print(f"build.py: {msg}", file=sys.stderr)
    sys.exit(1)


def read_page(name: str) -> tuple[dict, str]:
    text = (SRC / "pages" / f"{name}.html").read_text("utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        fail(f"{name}.html has no front matter")
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, m.group(2)


def placeholders() -> list[tuple[str, str]]:
    """Every data-ph string on every page, in page order (SPEC §8, §11)."""
    out = []
    for name, *_ in PAGES:
        _, body = read_page(name)
        for m in re.finditer(r"<(\w+)[^>]*\sdata-ph(?=[\s>])[^>]*>(.*?)</\1>", body, re.S):
            inner = re.sub(r"<span data-email></span>", "your address", m.group(2))
            txt = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
            txt = re.sub(r"\s+", " ", txt)
            out.append((PAGE_NAMES[name], txt))
    return out


def import_graph(entry: Path) -> list[str]:
    """Every module reachable from main.js, as the URL path the browser will request."""
    seen: list[str] = []
    todo = [entry]
    while todo:
        f = todo.pop(0)
        rel = f.relative_to(ROOT).as_posix()
        if rel in seen:
            continue
        seen.append(rel)
        src = f.read_text("utf-8")
        for m in re.finditer(r"""^\s*(?:import|export)\s[^'"]*?from\s*['"](\.[^'"]+)['"]|^\s*import\s*['"](\.[^'"]+)['"]""", src, re.M):
            spec = m.group(1) or m.group(2)
            todo.append((f.parent / spec).resolve())
    return seen


def collection_block(root: str) -> str:
    """data/collection.json is [] today: the not-ready state, nothing to render. When pieces
    exist they become grouped rows with a 56px still each (SPEC §11)."""
    items = json.loads((ROOT / "data" / "collection.json").read_text("utf-8"))
    if not items:
        return ""
    rows = []
    for it in items:
        rows.append(
            f'<a class="row row--piece" href="{root}collection/{html.escape(it["slug"])}/">'
            f'<img class="piece__thumb" src="{root}{html.escape(it["still"])}" alt="" width="56" height="56" loading="lazy">'
            f'<span>{html.escape(it["name"])}</span><span class="row__v">{GO}</span></a>')
    return '<div class="group blk">' + "".join(rows) + "</div>"


def tabs_html(root: str, current: str) -> str:
    out = []
    for slug, label in TABS:
        cur = ' aria-current="page"' if slug == current else ""
        out.append(f'    <a href="{root}{slug}/" data-to="{slug}/" data-tab="{slug}" draggable="false"{cur}><span>{label}</span></a>')
    return "\n".join(out)


class TextScan(HTMLParser):
    """Collects visible text (templates included: they are copy too) and checked attributes."""
    CHECKED_ATTRS = {"alt", "aria-label", "title", "placeholder", "content"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.attrs: list[str] = []
        self.skip = 0
        self.styles: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k == "style":
                self.styles.append(tag)
            if k in self.CHECKED_ATTRS and v:
                self.attrs.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


def check_html(path: str, doc: str) -> None:
    scan = TextScan()
    scan.feed(doc)
    text = " ".join(scan.text)
    if "!" in text:
        fail(f"{path}: an exclamation mark in visible copy")
    for chunk in [text, *scan.attrs]:
        if BANNED.search(chunk):
            fail(f"{path}: banned word in {chunk[:80]!r}")
    if scan.styles:
        fail(f"{path}: style attribute on {scan.styles}")
    if "data:" in re.sub(r"data-[\w-]+", "", doc):
        fail(f"{path}: data: URI")
    for m in re.finditer(r"taboukhi", text, re.I):
        pass  # house-name casing is enforced by e2e over the rendered page


def check_css(css: str) -> None:
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    if re.search(r"--[\w-]+\s*:[^;{}]*url\(", bare):
        fail("site.css: url() inside a custom property")
    if "data:" in bare:
        fail("site.css: data: URI")
    carriers = set()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare):
        if re.search(r"(?<![\w-])(-webkit-)?backdrop-filter\s*:", m.group(2)):
            for sel in m.group(1).split(","):
                carriers.add(sel.strip())
    if carriers != {".capsule", ".bar", ".sheet"}:
        fail(f"site.css: backdrop-filter must be carried by exactly .capsule, .bar, .sheet — found {sorted(carriers)}")
    if BANNED.search(bare):
        fail("site.css: banned word")


def build() -> None:
    demo = SITE["DEMO"]
    base_path = SITE["BASE_PATH"]

    # ── CSS ────────────────────────────────────────────────────────────────
    parts = []
    for name in CSS_ORDER:
        parts.append((SRC / "css" / f"{name}.css").read_text("utf-8").rstrip() + "\n")
    css = "\n".join(parts)
    check_css(css)
    (ROOT / "css").mkdir(exist_ok=True)
    (ROOT / "css" / "site.css").write_text(css, "utf-8")

    # ── Modules ────────────────────────────────────────────────────────────
    graph = import_graph(ROOT / "js" / "main.js")
    orphans = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "js").glob("*.js"))
    orphans = [p for p in orphans if p not in graph]
    if orphans:
        fail(f"modules outside the import graph: {orphans}")

    shell = (SRC / "shell.html").read_text("utf-8")
    ph = placeholders()
    credits = "\n".join(f'        <li class="credit"><b>{a}:</b> <span>{html.escape(d)}</span></li>' for a, d in CREDITS)
    ph_block = ""
    if demo:
        rows = "\n".join(f'    <p class="row--text">{html.escape(t)}<span class="row__sub">{p}</span></p>' for p, t in ph)
        ph_block = ('<section class="blk" aria-labelledby="nt-ph">\n  <h2 class="group-label" id="nt-ph">Placeholders on this preview</h2>\n'
                    f'  <div class="group">\n{rows}\n  </div>\n</section>')

    built: dict[str, str] = {}
    for name, out, rel_root, stage in PAGES:
        meta, body = read_page(name)
        root = rel_root if rel_root is not None else base_path
        depth_url = SITE["BASE_URL"] + ("" if out == "index.html" else out.replace("index.html", ""))
        body = (body.replace("{{eyebrow}}", meta.get("eyebrow", ""))
                    .replace("{{go}}", GO).replace("{{ext}}", EXT).replace("{{switch}}", SWITCH)
                    .replace("{{credits}}", credits).replace("{{placeholders}}", ph_block)
                    .replace("{{collection}}", collection_block(root))
                    .replace("{{root}}", root))
        if stage == "sheet":
            state = "home" if name == "home" else "panel"
            # In the shell on every page (Home hides them): a soft navigation keeps the shell.
            chip = ('    <button class="ph-chip" type="button" aria-pressed="false" aria-label="Preview, outline placeholders"><span>Preview</span></button>\n'
                    if demo else "")
            cap = '      <p class="ph-cap">Outlined lines are placeholders.</p>\n' if demo else ""
            lens_hidden = "" if name == "home" else " hidden"
            stage_html = (
                f'<section class="sheet glass--thick" data-state="{state}">\n'
                '  <div class="sheet__in">\n'
                '    <button class="sheet__grab" type="button" aria-label="More of this page" aria-expanded="false"></button>\n'
                f'{chip}'
                '    <div class="sheet__scroll">\n'
                f'{cap}'
                f'      <main id="page" tabindex="-1">\n{body.rstrip()}\n      </main>\n'
                '    </div>\n  </div>\n</section>\n'
                '<div class="sheet-ghost" aria-hidden="true"></div>\n'
                f'<div class="lens"{lens_hidden}><button class="lens__handle" type="button" aria-label="Lens" aria-describedby="hint"></button></div>')
        else:
            stage_html = ('<div class="void" aria-hidden="true"></div>\n'
                          f'<main id="page" tabindex="-1" class="lost">\n{body.rstrip()}\n</main>')
        preload = "".join(f'<link rel="modulepreload" href="{root}{m}">\n' for m in graph)
        doc = (shell.replace("{{stage}}", stage_html)
                    .replace("{{tabs}}", tabs_html(root, meta.get("tab", "")))
                    .replace("{{modulepreload}}", preload)
                    .replace("{{inline_script}}", INLINE_SCRIPT)
                    .replace("{{robots}}", '<meta name="robots" content="noindex,nofollow">\n' if demo else "")
                    .replace("{{og_url}}", depth_url)
                    .replace("{{og_image}}", SITE["BASE_URL"] + "assets/og.jpg")
                    .replace("{{title}}", html.escape(meta["title"], quote=True))
                    .replace("{{route}}", meta["route"]).replace("{{station}}", meta["station"])
                    .replace("{{tab}}", meta.get("tab", ""))
                    .replace("{{root}}", root))
        if "{{" in doc:
            fail(f"{out}: unfilled token {re.search(r'{{[^}]*}}', doc).group(0)}")
        check_html(out, doc)
        dest = ROOT / out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(doc, "utf-8")
        built[out] = doc

    # ── Manifest, robots, .nojekyll ─────────────────────────────────────────
    manifest = {
        "name": "TABOUKHI", "short_name": "TABOUKHI",
        "description": "TABOUKHI is the first eyewear brand established by Ilya Taboukhi.",
        "lang": "en", "dir": "ltr", "start_url": "./", "scope": "./",
        "display": "standalone", "orientation": "any",
        "background_color": "#020202", "theme_color": "#020202",
        "icons": [
            {"src": "assets/brand/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "assets/brand/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "assets/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "assets/brand/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }
    (ROOT / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    (ROOT / "robots.txt").write_text("User-agent: *\nDisallow: /\n" if demo else "User-agent: *\nAllow: /\n", "utf-8")
    (ROOT / ".nojekyll").write_text("", "utf-8")

    # ── Service worker ──────────────────────────────────────────────────────
    shell_list = ["./", "./house/", "./collection/", "./commission/", "./contact/", "./notices/", "./offline.html",
                  "./css/site.css", *[f"./{m}" for m in graph],
                  "./assets/fonts/inter.woff2", "./assets/brand/favicon.svg",
                  "./assets/media/film-pt-1080.webp", "./assets/media/film-sq-1920.webp",
                  "./manifest.webmanifest"]
    h = hashlib.sha1()
    for p in sorted(built):
        h.update(p.encode()); h.update(built[p].encode())
    h.update(css.encode())
    for m in graph:
        h.update(m.encode()); h.update((ROOT / m).read_bytes())
    version = "tbks-" + h.hexdigest()[:10]
    sw = (SRC / "sw.js").read_text("utf-8").replace("{{VERSION}}", version).replace(
        "{{SHELL}}", ",\n".join(f"  '{u}'" for u in shell_list))
    (ROOT / "sw.js").write_text(sw, "utf-8")

    # ── README ──────────────────────────────────────────────────────────────
    csp = "sha256-" + base64.b64encode(hashlib.sha256(INLINE_SCRIPT.encode()).digest()).decode()
    readme = (SRC / "README.md").read_text("utf-8")
    readme = (readme.replace("{{CSP_HASH}}", csp).replace("{{VERSION}}", version)
                    .replace("{{COPY_POSITION}}", COPY_POSITION)
                    .replace("{{MODULES}}", "\n".join(f"- `{m}`" for m in graph))
                    .replace("{{PLACEHOLDERS}}", "\n".join(f"- {p}: {t}" for p, t in ph)))
    (ROOT / "README.md").write_text(readme, "utf-8")

    print(f"built {len(built)} pages, css {len(css)//1024} KB, {len(graph)} modules, {len(ph)} placeholders, {version}")
    print(f"inline script CSP hash: '{csp}'")


if __name__ == "__main__":
    build()
