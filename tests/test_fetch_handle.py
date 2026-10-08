"""A reused libcurl handle must survive a HEAD followed by more GETs.

This guards one specific fatal crash, and it runs in a subprocess because that
crash cannot be caught: cffi aborts the interpreter.

    Fatal Python error: b_from_handle: ffi.from_handle() detected that the
    address passed points to garbage. If it is really the result of
    ffi.new_handle(), then the Python object has already been garbage collected

── what went wrong ─────────────────────────────────────────────────────────

`Fetcher` keeps one libcurl handle per impersonation target for the life of a
run, so a sweep of twenty districts does one TLS handshake instead of twenty.

curl_cffi's `setopt(WRITEDATA, f)` and `setopt(HEADERDATA, f)` wrap `f` in a
cffi handle and give libcurl the raw pointer — then at the end of every
`perform()` it runs `clean_handles_and_buffers()`, which drops the Python
references keeping those handles alive while libcurl still holds the pointers
and still has the callbacks installed.

So `head()` installed HEADERDATA, and the second ordinary `get()` after it
fired the header callback into freed memory. On the server it took the whole
run with it — Windows reported exit -1073740791, and `openrent` was the only
reader affected because it is the only one that calls `head()`.

The rule that fixes it, and that this test exists to keep: every request sets
every callback target, on every path.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("curl_cffi")

ROOT = Path(__file__).resolve().parents[1]

# A local server, so this is offline and deterministic. It answers HEAD with a
# redirect, which is exactly the shape OpenRent gives a bare listing id.
SCRIPT = textwrap.dedent(
    '''
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/redirect"):
                self.send_response(301)
                self.send_header("Location", "/property-to-rent/london/2-bed-flat-e14/123")
                self.end_headers()
                return
            body = b"<html><body>page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_HEAD = do_GET
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    from worker.sources.fetch import Fetcher

    get = Fetcher()
    # The order matters. One get after a head re-registers the header target by
    # accident and survives; the crash is on the one after that.
    assert get.get(base + "/a").status == 200
    assert get.head(base + "/redirect/1")[0] == 301
    assert get.get(base + "/b").status == 200
    assert get.get(base + "/c").status == 200
    assert get.head(base + "/redirect/2")[1].endswith("/123")
    assert get.get(base + "/d").status == 200
    assert get.get(base + "/e").status == 200

    # And the bodies still arrive: a HEAD leaves NOBODY set on the handle, and
    # forgetting to undo it makes every later page come back empty with no
    # error at all — which is worse than the crash, because it is silent.
    assert "page" in get.get(base + "/f").body

    # One handle, eight requests. Asserted because if the reuse were dropped
    # the crash would be gone too, and this test would pass for the wrong
    # reason — it is the reuse that makes the hazard possible.
    assert get.requests == 8, get.requests
    assert len(get._handles) == 1, get._handles

    get.close()
    print("SURVIVED")
    '''
)


def test_a_head_does_not_poison_the_handle_for_later_gets() -> None:
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    # A fatal cffi error exits non-zero and prints to stderr rather than
    # raising, so both are worth asserting on: the message is the evidence.
    assert "from_handle" not in done.stderr, done.stderr
    assert "Fatal Python error" not in done.stderr, done.stderr
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    assert "SURVIVED" in done.stdout
