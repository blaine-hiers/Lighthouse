"""Local server for the Classroom viewer: static files plus one write endpoint.

    python viewer/serve.py [port]        (default 8765)

Serves the repo root like `python -m http.server`, bound to 127.0.0.1 only.
The one thing it adds: PUT to lessons/<slug>/practice/<file>, so the viewer's
Save button can write the learner's code back for Claude to read.

Everything else is refused. The write endpoint is default-deny:
  - the path must match a strict pattern, with no '..', backslash, '%', ':' or
    other odd characters, and must sit under an existing lesson's practice/ folder;
  - the real (symlink-resolved) path must still be inside that folder;
  - the body is capped at MAX_BODY bytes and needs a Content-Length;
  - Host must be this server (blocks DNS rebinding) and Origin must be this
    server's own origin (blocks other websites writing to it).
Standard library only.
"""
import http.server
import os
import re
import sys
import tempfile
from pathlib import Path

MAX_BODY = 256 * 1024
ROOT = Path(__file__).resolve().parents[1]

NAME = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
PUT_PATH = re.compile(rf"^/lessons/({NAME})/practice/({NAME}(?:/{NAME})*)$")
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


def resolve_put_target(root, raw_path):
    """Return the Path to write, or None when the path isn't allowed."""
    path = raw_path  # matched whole: no query, fragment or absolute-form URL is accepted
    if "%" in path or "\\" in path or "\0" in path:
        return None  # encoded variants are never legitimate here; a clean path has none
    m = PUT_PATH.match(path)
    if not m:
        return None
    slug, rest = m.groups()
    segments = [slug, *rest.split("/")]
    if any(s.endswith(".") or WINDOWS_RESERVED.match(s) for s in segments):
        return None  # trailing dots/names Windows would rewrite or treat as devices
    practice = (root / "lessons" / slug / "practice")
    if not practice.is_dir():
        return None
    practice_real = practice.resolve(strict=True)
    if not str(practice_real).startswith(str((root / "lessons").resolve()) + os.sep):
        return None  # practice/ itself is a symlink out of the lessons tree
    target = practice_real.joinpath(*rest.split("/"))
    if not target.parent.is_dir():
        return None  # never create directories
    if target.is_symlink() or (target.exists() and not target.is_file()):
        return None
    try:
        target.parent.resolve(strict=True).relative_to(practice_real)
    except ValueError:
        return None
    return target


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "ClassroomServe/1"

    # ---- helpers ----
    def _host_ok(self):
        return (self.headers.get("Host") or "").lower() in self.server.host_header_ok

    def _fail(self, code, msg=""):
        self.send_response(code)
        if code == 405:
            self.send_header("Allow", "GET, HEAD, PUT")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = (msg or self.responses[code][0]).encode()
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    # ---- reads: static, but only for our own Host ----
    def do_GET(self):
        if not self._host_ok():
            return self._fail(403, "bad Host")
        super().do_GET()

    def do_HEAD(self):
        if not self._host_ok():
            return self._fail(403, "bad Host")
        super().do_HEAD()

    # ---- the one write ----
    def do_PUT(self):
        if not self._host_ok():
            return self._fail(403, "bad Host")
        if (self.headers.get("Origin") or "") not in self.server.origins_ok:
            return self._fail(403, "bad Origin")
        target = resolve_put_target(self.server.root, self.path)
        if target is None:
            return self._fail(404, "not a writable practice file")
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            return self._fail(411, "Content-Length required")
        raw = self.headers.get("Content-Length") or ""
        # ASCII digits only, and short: str.isdigit() also accepts e.g. "²",
        # and int() refuses very long digit strings.
        if not (raw.isascii() and raw.isdigit() and len(raw) <= 9):
            return self._fail(411 if len(raw) <= 9 else 413, "Content-Length required")
        length = int(raw)
        if length > MAX_BODY:
            return self._fail(413, f"body over {MAX_BODY} bytes")
        data = self.rfile.read(length)
        if len(data) != length:  # the client went away mid-body: keep the old file
            return self._fail(400, "body shorter than Content-Length")
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".save-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except PermissionError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            # Windows: another program has the file open.
            return self._fail(409, "the file is open in another program; close it and save again")
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _no(self):
        self._fail(405)

    do_POST = do_DELETE = do_PATCH = do_OPTIONS = _no

    def log_message(self, fmt, *args):
        if os.environ.get("CLASSROOM_SERVE_QUIET") != "1":
            super().log_message(fmt, *args)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def finish_request(self, request, client_address):
        self.RequestHandlerClass(request, client_address, self, directory=str(self.root))

    def __init__(self, port, root=ROOT):
        self.root = Path(root).resolve()
        super().__init__(("127.0.0.1", port), Handler)
        p = self.server_address[1]
        self.host_header_ok = (f"127.0.0.1:{p}", f"localhost:{p}")
        self.origins_ok = tuple(f"http://{h}" for h in self.host_header_ok)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = Server(port)
    print(f"Classroom on http://127.0.0.1:{srv.server_address[1]}/viewer/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
