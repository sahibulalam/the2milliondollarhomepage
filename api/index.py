"""Vercel entry point.

Vercel's Python runtime imports this module and looks for `handler`, a
BaseHTTPRequestHandler subclass, which it drives once per request. app.Handler
already is one, so the whole router is reused unchanged -- the only difference
between running here and running `python3 app.py` is that nothing calls
serve_forever.

vercel.json rewrites every path to this function, so routing stays in one
place rather than being split between the framework and the app.
"""
import os
import pathlib
import sys

# The repo root holds app.py and its siblings; on Vercel this file is one
# directory down, so make sure the root is importable before anything else.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app      # noqa: E402
import config   # noqa: E402
import store    # noqa: E402

# The schema is created once per cold start rather than on every request:
# CREATE TABLE IF NOT EXISTS is cheap but not free, and this runs on a shared
# database that other instances may be using at the same time.
_ready = False


def _ensure_schema():
    global _ready
    if _ready:
        return
    try:
        store.init()
        _ready = True
    except Exception as exc:                                    # noqa: BLE001
        # A failed migration must not wedge the instance permanently; log it
        # and let the next request try again.
        print("schema init failed: %r" % (exc,), file=sys.stderr)


class handler(app.Handler):
    def handle_one_request(self):
        _ensure_schema()
        return app.Handler.handle_one_request(self)
