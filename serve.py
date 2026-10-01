#!/usr/bin/env python3
"""Local preview. Static files, correct MIME types, byte ranges.

Like GitHub Pages: a missing path answers 404 with 404.html, and the site is also reachable
under its Pages sub-path (/taboukhi-site/...), so the BASE_PATH-absolute 404 and offline
pages render styled locally too.

    python3 serve.py           # http://localhost:8261, nothing cached
    python3 serve.py --pages   # send GitHub Pages' own Cache-Control (max-age=600)
"""
import functools
import http.server
import os
import pathlib
import re
import socketserver
import sys

PORT = int(os.environ.get("PORT", 8261))
ROOT = pathlib.Path(__file__).resolve().parent
PAGES = "--pages" in sys.argv


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript",
        ".woff2": "font/woff2",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    _left = None
    BASE = "/taboukhi-site"

    def translate_path(self, path):
        if path == self.BASE or path.startswith(self.BASE + "/"):
            path = path[len(self.BASE):] or "/"
        return super().translate_path(path)

    def _send_404_page(self):
        page = ROOT / "404.html"
        if not page.is_file():
            self.send_error(404)
            return None
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        import io
        return io.BytesIO(body)

    def end_headers(self):
        self.send_header("Cache-Control", "max-age=600" if PAGES else "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        """Serve a single byte range when asked — harmless here, and correct."""
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            return self._send_404_page()
        m = re.match(r"bytes=(\d*)-(\d*)$", (rng or "").strip())
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        if m.group(1):
            start = int(m.group(1))
            end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
        else:
            start, end = max(0, size - int(m.group(2) or 0)), size - 1
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        try:
            if self._left is None:
                return super().copyfile(source, outputfile)
            left, self._left = self._left, None
            while left > 0:
                chunk = source.read(min(1 << 16, left))
                if not chunk:
                    break
                outputfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        line = fmt % args
        if not any(f" {c} " in line for c in ("200", "206", "304")):
            super().log_message(fmt, *args)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("", PORT), functools.partial(Handler, directory=str(ROOT))) as srv:
        print(f"TABOUKHI → http://localhost:{PORT}" + ("  (GitHub Pages cache headers)" if PAGES else ""))
        srv.serve_forever()
