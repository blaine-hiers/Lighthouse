"""Tests for viewer/serve.py, especially the PUT write endpoint.

    python -m unittest tests/test_serve.py
"""
import http.client
import socket
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ["CLASSROOM_SERVE_QUIET"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewer"))
import serve  # noqa: E402


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "repo"
        (cls.root / "lessons" / "demo" / "practice").mkdir(parents=True)
        (cls.root / "lessons" / "demo" / "practice" / "sub").mkdir()
        (cls.root / "lessons" / "demo" / "lesson.md").write_bytes(b"# Demo\n")
        (cls.root / "lessons" / "demo" / "practice" / "a.py").write_text("old")
        (cls.root / "lessons" / "empty").mkdir()  # lesson with no practice/ folder
        (cls.root / "secret.txt").write_text("secret")
        cls.outside = Path(cls.tmp.name) / "outside"
        cls.outside.mkdir()
        (cls.outside / "target.txt").write_text("untouched")
        cls.symlinks = True
        try:
            os.symlink(cls.outside, cls.root / "lessons" / "demo" / "practice" / "escape", target_is_directory=True)
            os.symlink(cls.outside / "target.txt", cls.root / "lessons" / "demo" / "practice" / "link.py")
        except (OSError, NotImplementedError):
            cls.symlinks = False  # Windows without symlink privilege
        cls.srv = serve.Server(0, cls.root)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=b"", headers=None, origin="default", host=None):
        """Send the path byte-for-byte (no client-side normalising)."""
        h = {} if headers is None else dict(headers)
        if origin == "default":
            origin = f"http://127.0.0.1:{self.port}"
        if origin is not None:
            h["Origin"] = origin
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{self.port}")
        if "Content-Length" not in h and method in ("PUT", "POST"):
            h["Content-Length"] = str(len(body))
        for k, v in h.items():
            c.putheader(k, v)
        c.endheaders(body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def put(self, path, body=b"print(1)\n", **kw):
        return self.req("PUT", path, body, **kw)[0]

    def practice(self, name):
        return self.root / "lessons" / "demo" / "practice" / name

    # ---- allowed ----
    def test_allowed_put_overwrites_and_creates(self):
        self.assertEqual(self.put("/lessons/demo/practice/a.py", b"new"), 204)
        self.assertEqual(self.practice("a.py").read_bytes(), b"new")
        self.assertEqual(self.put("/lessons/demo/practice/b.py", b"x = 1\n"), 204)
        self.assertEqual(self.practice("b.py").read_bytes(), b"x = 1\n")
        self.assertEqual(self.put("/lessons/demo/practice/sub/c.py"), 204)
        self.assertEqual(sorted(p.name for p in self.practice(".").iterdir() if p.name.startswith(".save-")), [])

    def test_localhost_host_and_origin_allowed(self):
        o = f"http://localhost:{self.port}"
        self.assertEqual(self.put("/lessons/demo/practice/l.py", origin=o, host=f"localhost:{self.port}"), 204)

    # ---- traversal ----
    def test_traversal_attempts_rejected(self):
        bad = [
            "/lessons/demo/practice/../lesson.md",
            "/lessons/demo/practice/../../../secret.txt",
            "/lessons/demo/practice/%2e%2e/lesson.md",
            "/lessons/demo/practice/%2E%2E/%2E%2E/secret.txt",
            "/lessons/demo/practice/..%2f..%2fsecret.txt",
            "/lessons/demo/practice/%252e%252e/lesson.md",
            "/lessons/demo/practice/..%5clesson.md",
            "/lessons/demo/practice/..\\lesson.md",
            "/lessons/demo/practice/sub\\..\\..\\lesson.md",
            "/lessons/demo/practice/C:/evil.py",
            "/lessons/demo/practice/a.py:stream",
            "/lessons/demo/practice//etc/passwd",
            "/lessons/demo/practice/%00.py",
            "/lessons/demo/practice/a.py%00.txt",
            "/lessons/%2e%2e/practice/x.py",
            "/lessons/../practice/x.py",
            "/lessons/demo/practice/.",
            "/lessons/demo/practice/a.py.",
            "/lessons/demo/practice/CON",
            "/lessons/demo/practice/nul.txt",
            "/etc/passwd",
            "C:\\Windows\\win.ini",
            "http://evil.example/lessons/demo/practice/x.py",
            "//evil.example/lessons/demo/practice/x.py",
            "/lessons/demo/practice/x.py?../../secret.txt",
            "/lessons/demo/practice/x.py#frag",
        ]
        for p in bad:
            with self.subTest(path=p):
                self.assertIn(self.put(p), (400, 403, 404), p)
        self.assertEqual((self.root / "secret.txt").read_text(), "secret")
        self.assertEqual((self.root / "lessons" / "demo" / "lesson.md").read_text(), "# Demo\n")
        self.assertFalse((self.root / "x.py").exists())

    def test_symlinks_out_of_tree_rejected(self):
        if not self.symlinks:
            self.skipTest("symlinks unavailable")
        self.assertIn(self.put("/lessons/demo/practice/escape/new.txt"), (403, 404))
        self.assertIn(self.put("/lessons/demo/practice/escape/target.txt"), (403, 404))
        self.assertIn(self.put("/lessons/demo/practice/link.py"), (403, 404))
        self.assertEqual((self.outside / "target.txt").read_text(), "untouched")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["target.txt"])

    # ---- paths outside the practice subtree ----
    def test_wrong_paths_rejected(self):
        for p in [
            "/lessons/demo/lesson.md",
            "/lessons/demo/practice",
            "/lessons/demo/practice/",
            "/lessons/demo/practice/newdir/x.py",  # directories are never created
            "/lessons/nope/practice/x.py",         # lesson must already exist
            "/lessons/empty/practice/x.py",        # practice/ must already exist
            "/viewer/core.js",
            "/secret.txt",
            "/",
            "/lessons/.current",
        ]:
            with self.subTest(path=p):
                self.assertIn(self.put(p), (404,), p)
        self.assertFalse((self.root / "lessons" / "empty" / "practice").exists())

    # ---- methods ----
    def test_wrong_methods_rejected(self):
        for m in ("POST", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=m):
                self.assertEqual(self.req(m, "/lessons/demo/practice/a.py", b"x")[0], 405)
        self.assertTrue(self.practice("a.py").exists())

    # ---- size cap ----
    def test_size_cap(self):
        ok = b"x" * serve.MAX_BODY
        self.assertEqual(self.put("/lessons/demo/practice/big.py", ok), 204)
        self.assertEqual(self.practice("big.py").stat().st_size, serve.MAX_BODY)
        self.assertEqual(self.put("/lessons/demo/practice/big2.py", ok + b"x"), 413)
        self.assertFalse(self.practice("big2.py").exists())
        # a lying/huge Content-Length is refused without reading it
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("PUT", "/lessons/demo/practice/huge.py", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{self.port}")
        c.putheader("Origin", f"http://127.0.0.1:{self.port}")
        c.putheader("Content-Length", str(10 * 1024 * 1024 * 1024))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()
        self.assertFalse(self.practice("huge.py").exists())

    def test_missing_or_bad_length_rejected(self):
        for cl in ("abc", "-1", "²"):  # "²" passes str.isdigit() but isn't a number
            self.assertEqual(self.req("PUT", "/lessons/demo/practice/n.py", b"", {"Content-Length": cl})[0], 411)
        self.assertEqual(self.req("PUT", "/lessons/demo/practice/n.py", b"", {"Content-Length": "9" * 5000})[0], 413)
        self.assertFalse(self.practice("n.py").exists())

    def test_short_body_keeps_the_old_file(self):
        self.assertEqual(self.put("/lessons/demo/practice/keep.py", b"original\n"), 204)
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"PUT /lessons/demo/practice/keep.py HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                   f"Origin: http://127.0.0.1:{self.port}\r\nContent-Length: 100\r\n\r\nhalf").encode())
        s.shutdown(socket.SHUT_WR)
        reply = s.recv(200)
        s.close()
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])
        self.assertEqual(self.practice("keep.py").read_bytes(), b"original\n")

    def test_file_locked_by_another_program_gets_an_answer(self):
        real = serve.os.replace
        def locked(*a, **k):
            raise PermissionError(13, "The process cannot access the file")
        serve.os.replace = locked
        try:
            status, body = self.req("PUT", "/lessons/demo/practice/locked.py", b"x")
        finally:
            serve.os.replace = real
        self.assertEqual(status, 409)
        self.assertIn(b"open in another program", body)
        self.assertEqual([p.name for p in self.practice(".").iterdir() if p.name.startswith(".save-")], [])

    # ---- Origin / Host ----
    def test_bad_origin_rejected(self):
        for o in ("http://evil.example", "null", "", f"https://127.0.0.1:{self.port}",
                  f"http://127.0.0.1:{self.port + 1}", f"http://127.0.0.1:{self.port}.evil.example"):
            with self.subTest(origin=o):
                self.assertEqual(self.put("/lessons/demo/practice/o.py", origin=o), 403)
        self.assertEqual(self.put("/lessons/demo/practice/o.py", origin=None), 403)  # no Origin at all
        self.assertFalse(self.practice("o.py").exists())

    def test_bad_host_rejected(self):
        # DNS rebinding: the attacker's page is "same origin" with its own Host name.
        self.assertEqual(self.put("/lessons/demo/practice/h.py", host="evil.example",
                                  origin="http://evil.example"), 403)
        self.assertEqual(self.req("GET", "/secret.txt", host="evil.example")[0], 403)
        self.assertFalse(self.practice("h.py").exists())

    # ---- static serving still works ----
    def test_static_files(self):
        status, body = self.req("GET", "/lessons/demo/lesson.md", origin=None)
        self.assertEqual((status, body), (200, b"# Demo\n"))
        self.assertEqual(self.req("GET", "/nope", origin=None)[0], 404)

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
