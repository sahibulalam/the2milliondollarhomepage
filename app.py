#!/usr/bin/env python3
"""MoneyPrinter -- rent an area of the billboard.

Seven areas are for sale on one photo. Each is held by whoever paid the most
for it; anyone can take an area by paying more. Money is real: a claim creates
a Dodo Payments checkout session and the area only changes hands when Dodo's
webhook confirms the payment landed.

    python3 app.py

Dependencies: none. Standard library only.
"""
import base64
import binascii
import json
import mimetypes
import re
import secrets
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import dodo
import grid
import store

MAX_BODY = 2 * 1024 * 1024
ASSETS = ("site.css", "app.js")
MAX_BID_CENTS = 100_000_000          # $1,000,000 -- a typo guard, not a policy
LOGO_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=\s]+)$")
COOKIE_RE = re.compile(r"(?:^|;\s*)mp_vid=([A-Za-z0-9_\-]{6,64})")

# --------------------------------------------------------------- validation

def clean_brand(raw):
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not (2 <= len(s) <= 40):
        raise ValueError("Company name must be 2-40 characters.")
    if any(ord(c) < 32 for c in s):
        raise ValueError("Company name contains control characters.")
    return s


def clean_url(raw):
    t = str(raw or "").strip()
    if not t:
        raise ValueError("Enter your website.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", t):
        t = "https://" + t
    try:
        u = urllib.parse.urlsplit(t)
    except ValueError:
        raise ValueError("That website address is not valid.")
    if u.scheme not in ("http", "https") or "." not in (u.hostname or ""):
        raise ValueError("Website must be a normal http(s) address.")
    if len(t) > 300:
        raise ValueError("That website address is too long.")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, u.query, ""))


def clean_email(raw):
    t = str(raw or "").strip()
    if not t:
        return None
    if len(t) > 160 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", t):
        raise ValueError("That email address is not valid.")
    return t


def clean_logo(raw):
    """Only real raster images, decoded and size-checked before we store them."""
    if not raw:
        return None
    s = str(raw).strip()
    m = LOGO_RE.match(s)
    if not m:
        raise ValueError("Logo must be a PNG, JPEG, WebP or GIF image.")
    payload = re.sub(r"\s", "", m.group(2))
    try:
        blob = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("That logo could not be decoded.")
    if len(blob) > config.MAX_LOGO_BYTES:
        raise ValueError("That logo is too large. Keep it under %d KB."
                         % (config.MAX_LOGO_BYTES // 1000))
    if len(blob) < 32:
        raise ValueError("That logo is empty.")
    return "data:image/%s;base64,%s" % (m.group(1), payload)


def clean_rect(d):
    """A selection: a rectangle of tiles, checked against the real grid."""
    try:
        col = int(d.get("col"))
        row = int(d.get("row"))
        cols = int(d.get("cols"))
        rows = int(d.get("rows"))
    except (TypeError, ValueError):
        raise ValueError("Drag a selection on the picture first.")
    if not grid.in_bounds(col, row, cols, rows):
        raise ValueError("That selection falls outside the canvas.")
    if cols * rows > grid.MAX_TILES_PER_CLAIM:
        raise ValueError(
            "That is %s blocks. One purchase can cover at most %s -- split it up."
            % (format(cols * rows, ","), format(grid.MAX_TILES_PER_CLAIM, ",")))
    return {"col": col, "row": row, "cols": cols, "rows": rows}


def clean_amount(raw, minimum):
    try:
        cents = int(round(float(raw) * 100))
    except (TypeError, ValueError):
        raise ValueError("Enter the amount you are paying.")
    if cents < minimum:
        raise ValueError("This selection needs at least %s." % money(minimum))
    if cents > MAX_BID_CENTS:
        raise ValueError("That is more than this site accepts in one payment.")
    return cents


def money(cents):
    return "$%s" % format(cents / 100.0, ",.2f").replace(".00", "")


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "MoneyPrinter"
    protocol_version = "HTTP/1.1"
    # HTTP/1.1 keeps connections alive, and a client that walks away without
    # closing would otherwise pin a thread here for good. Reap idle sockets.
    timeout = 30
    _raw = b""

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except socket.timeout:
            self.close_connection = True

    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # --- plumbing ---------------------------------------------------------
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8", extra)

    def _fail(self, code, message):
        self._json(code, {"error": message})

    def _read_body(self):
        """Drain the request body exactly once, before any routing.

        On a keep-alive connection an early return that leaves the body in the
        socket desynchronises the stream: the next request line the server
        reads is actually this request's JSON. So this happens up front, for
        every POST, whatever the outcome.
        """
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > MAX_BODY:
            # Still drain, so the connection stays usable, then refuse.
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise ValueError("Request too large.")
        return self.rfile.read(n)

    def _body(self):
        return self._raw

    def _json_body(self):
        try:
            return json.loads(self._raw.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Malformed request.")

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() or self.client_address[0])

    def _vid(self):
        """Stable per-browser id. Returns (vid, set_cookie_header or None)."""
        m = COOKIE_RE.search(self.headers.get("Cookie", "") or "")
        if m:
            return m.group(1), None
        vid = secrets.token_urlsafe(16)
        return vid, ("Set-Cookie",
                     "mp_vid=%s; Path=/; Max-Age=31536000; SameSite=Lax; HttpOnly"
                     % vid)

    # --- routing ----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if path == "/":
                return self.page_index()
            if path == "/thanks":
                return self.serve_page(config.VIEWS / "thanks.html")
            if path.startswith("/logo/"):
                return self.serve_logo(path[len("/logo/"):])
            if path == "/healthz":
                # Touches neither the database nor the disk: a liveness probe,
                # and the control in any "is it the server or the volume?"
                # question.
                return self._send(200, "ok\n", "text/plain; charset=utf-8",
                                  [("Cache-Control", "no-store")])
            if path == "/api/state":
                return self.api_state()
            if path == "/api/quote":
                return self.api_quote(urllib.parse.urlsplit(self.path).query)
            if path.startswith("/api/claim/"):
                return self.api_claim_status(path.rsplit("/", 1)[-1])
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            # Root-relative assets. On Vercel these never reach the app -- the
            # CDN serves them off the filesystem -- but locally they do.
            if path.lstrip("/") in ASSETS:
                return self.serve_static(path.lstrip("/"))
        except BrokenPipeError:
            return
        except Exception as e:                                   # noqa: BLE001
            self.log_message("error on %s: %r", path, e)
            return self._fail(500, "Something broke on our side.")
        self._send(404, "Not found.")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        try:
            self._raw = self._read_body()
        except ValueError as e:
            return self._fail(413, str(e))
        except BrokenPipeError:
            return
        try:
            if path == "/api/claim":
                return self.api_claim()
            if path == "/api/webhook/dodo":
                return self.api_webhook()
        except ValueError as e:
            return self._fail(400, str(e))
        except BrokenPipeError:
            return
        except Exception as e:                                   # noqa: BLE001
            self.log_message("error on %s: %r", path, e)
            return self._fail(500, "Something broke on our side.")
        self._send(404, "Not found.")

    # --- pages ------------------------------------------------------------
    def page_index(self):
        vid, cookie = self._vid()
        con = store.connect()
        try:
            store.touch_visitor(con, vid)
        finally:
            con.close()
        self.serve_page(config.VIEWS / "index.html",
                        extra=[cookie] if cookie else [])

    def serve_page(self, p, extra=None):
        """An HTML page, with its asset links version-stamped.

        /static/app.js becomes /static/app.js?v=<mtime>, so changing a file
        changes its URL and no browser can hold on to the previous one.
        """
        try:
            html = p.read_text()
        except OSError:
            return self._send(404, "Not found.")
        for asset in ("site.css", "app.js"):
            try:
                stamp = "%x" % int((config.PUBLIC / asset).stat().st_mtime)
            except OSError:
                continue
            html = html.replace("/" + asset, "/%s?v=%s" % (asset, stamp))
        head = list(extra or [])
        head.append(("Cache-Control", "no-store"))
        self._send(200, html, "text/html; charset=utf-8", head)

    def serve_file(self, p, cache=True, extra=None, revalidate=False):
        """Static files revalidate rather than sit in the cache for an hour.

        A stale app.js after a deploy is a worse bug than an extra 304, and
        these files are a few KB each.
        """
        try:
            st = p.stat()
            data = p.read_bytes()
        except OSError:
            return self._send(404, "Not found.")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        head = list(extra or [])
        if cache:
            etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
            head.append(("ETag", etag))
            # /hero keeps its URL when the photo is swapped, so it must ask.
            head.append(("Cache-Control",
                         "no-cache" if revalidate else "public, max-age=86400"))
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control",
                                 "no-cache" if revalidate else "public, max-age=86400")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        else:
            head.append(("Cache-Control", "no-store"))
        self._send(200, data, ctype, head)

    def serve_static(self, rel):
        rel = urllib.parse.unquote(rel.split("?")[0])
        if ".." in rel or rel.startswith("/"):
            return self._send(403, "No.")
        target = (config.PUBLIC / rel).resolve()
        if config.PUBLIC.resolve() not in target.parents:
            return self._send(403, "No.")
        self.serve_file(target)

    CLAIM_ID_RE = re.compile(r"^clm_[A-Za-z0-9_\-]{1,40}$")

    def serve_logo(self, claim_id):
        """One claim's logo, as a real image response.

        Logos are served here rather than inlined into /api/state: with a
        hundred pixels the state payload would be megabytes of base64 on every
        poll. A claim's logo never changes, so this caches permanently.
        """
        claim_id = urllib.parse.unquote(claim_id.split("?")[0])
        if not self.CLAIM_ID_RE.match(claim_id):
            return self._send(404, "Not found.")
        con = store.connect()
        try:
            data_url = store.get_logo(con, claim_id)
        finally:
            con.close()
        if not data_url:
            return self._send(404, "Not found.")
        m = LOGO_RE.match(data_url)
        if not m:
            return self._send(404, "Not found.")
        try:
            blob = base64.b64decode(re.sub(r"\s", "", m.group(2)), validate=True)
        except (binascii.Error, ValueError):
            return self._send(500, "Stored logo is unreadable.")
        kind = "jpeg" if m.group(1) == "jpg" else m.group(1)
        self._send(200, blob, "image/" + kind, [
            ("Cache-Control", "public, max-age=31536000, immutable"),
        ])

    # --- api --------------------------------------------------------------
    def api_state(self):
        con = store.connect()
        try:
            store.expire_stale(con)
            payload = {
                "grid": {
                    "cols": grid.COLS, "rows": grid.ROWS,
                    "tile_px": grid.TILE_PX, "tiles": grid.TILES,
                    "pixels": grid.PIXELS,
                    "floor_cents": grid.FLOOR_CENTS,
                    "pixel_cents": grid.FLOOR_CENTS / float(grid.TILE_PX ** 2),
                    "max_tiles": grid.MAX_TILES_PER_CLAIM,
                    "width_px": grid.WIDTH_PX, "height_px": grid.HEIGHT_PX,
                },
                "site": {
                    "name": config.SITE_NAME,
                    "tagline": config.SITE_TAGLINE,
                    "twitter": config.TWITTER_HANDLE,
                },
                "claims": store.board(con),
                "ledger": store.ledger(con),
                "rankings": store.rankings(con),
                "stats": store.stats(con),
                "rules": {"currency": config.CURRENCY},
                "demo": config.DEMO_MODE,
                "now": int(time.time()),
            }
        finally:
            con.close()
        self._json(200, payload, [("Cache-Control", "no-store")])

    def api_quote(self, query):
        """Live price for a dragged selection. Read-only and cheap."""
        q = urllib.parse.parse_qs(query)
        try:
            rect = clean_rect({k: (q.get(k) or [""])[0]
                               for k in ("col", "row", "cols", "rows")})
        except ValueError as e:
            return self._fail(400, str(e))
        con = store.connect()
        try:
            out = store.quote(con, **rect)
        finally:
            con.close()
        self._json(200, out, [("Cache-Control", "no-store")])

    def api_claim_status(self, claim_id):
        con = store.connect()
        try:
            c = store.get_claim(con, claim_id)
        finally:
            con.close()
        if not c:
            return self._fail(404, "No such claim.")
        self._json(200, {
            "id": c["id"], "status": c["status"], "brand": c["brand"],
            "col": c["col"], "row": c["row"],
            "cols": c["cols"], "rows": c["rows"],
            "tiles": c["tile_count"],
            "pixels": c["tile_count"] * grid.TILE_PX * grid.TILE_PX,
            "amount_cents": c["amount_cents"],
        }, [("Cache-Control", "no-store")])

    def api_claim(self):
        d = self._json_body()

        con = store.connect()
        try:
            if store.throttled(con, self._ip()):
                return self._fail(429, "Slow down a moment, then try again.")
            store.expire_stale(con)
            rect = clean_rect(d)
            brand = clean_brand(d.get("brand"))
            url = clean_url(d.get("url"))
            email = clean_email(d.get("email"))
            logo = clean_logo(d.get("logo"))

            # Priced server side. The number the browser showed is a quote,
            # never the authority -- somebody may have bought into the area
            # since the drag.
            q = store.quote(con, **rect)
            if not q["available"]:
                px = q["taken"] * grid.TILE_PX * grid.TILE_PX
                return self._fail(409,
                    "That area overlaps %s pixel%s somebody already owns. "
                    "Sold pixels stay sold -- move or resize your box."
                    % (format(px, ","), "" if px == 1 else "s"))
            amount = clean_amount(d.get("amount"), q["total_cents"])

            claim_id = store.open_claim(con, rect, brand, url, logo, email, amount)
            store.count_claim(con, self._ip())

            if config.DEMO_MODE:
                store.settle_claim(con, claim_id, "demo_" + claim_id)
                return self._json(200, {
                    "claim_id": claim_id, "demo": True,
                    "redirect": "/thanks?claim=" + claim_id,
                })

            label = ("%s pixels (%d block%s of %dx%d)"
                     % (format(q["pixels"], ","), q["tiles"],
                        "" if q["tiles"] == 1 else "s",
                        grid.TILE_PX, grid.TILE_PX))
            try:
                session = dodo.create_checkout(
                    claim_id, "r%dc%d" % (rect["row"], rect["col"]),
                    label, brand, amount, email)
            except dodo.DodoError as e:
                store.fail_claim(con, claim_id)
                self.log_message("dodo checkout failed: %s %s", e, e.body or "")
                return self._fail(502, "The payment provider did not respond. "
                                       "Nothing was charged. Try again.")

            checkout_url = session.get("checkout_url")
            store.attach_session(con, claim_id, session.get("session_id"),
                                 checkout_url, session.get("payment_id"))
            if not checkout_url:
                store.fail_claim(con, claim_id)
                return self._fail(502, "The payment provider returned no "
                                       "checkout page. Nothing was charged.")
            self._json(200, {"claim_id": claim_id, "redirect": checkout_url})
        finally:
            con.close()

    def api_webhook(self):
        raw = self._body()
        try:
            webhook_id = dodo.verify(self.headers, raw)
        except dodo.DodoError as e:
            self.log_message("rejected webhook: %s", e)
            return self._fail(401, "bad signature")

        try:
            event = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError):
            return self._fail(400, "bad json")

        kind = str(event.get("type") or "")
        data = event.get("data") or {}
        meta = data.get("metadata") or {}
        claim_id = str(meta.get("claim_id") or "")
        payment_id = str(data.get("payment_id") or data.get("id") or webhook_id)

        con = store.connect()
        try:
            if not store.webhook_is_new(con, webhook_id):
                return self._json(200, {"ok": True, "note": "duplicate"})
            if not claim_id:
                self.log_message("webhook %s (%s) carried no claim_id", webhook_id, kind)
                return self._json(200, {"ok": True, "note": "no claim_id"})

            if kind == "payment.succeeded":
                ok, why, won = store.settle_claim(con, claim_id, payment_id)
                if why == "lost_race":
                    self.log_message("REFUND DUE on %s: the area was bought by "
                                     "somebody else first", claim_id)
                self.log_message("settled %s: %s, %d tile(s) (%s)",
                                 claim_id, why, won, kind)
                return self._json(200, {"ok": ok, "result": why, "tiles": won})
            if kind in ("payment.failed", "payment.cancelled", "payment.canceled"):
                store.fail_claim(con, claim_id)
                return self._json(200, {"ok": True, "result": "failed"})
            return self._json(200, {"ok": True, "note": "ignored " + kind})
        finally:
            con.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    # `kill -USR1 <pid>` dumps every thread's stack to the log. Cheap to leave
    # in: it costs nothing until the signal arrives, and it is the difference
    # between diagnosing a stall and guessing at one.
    try:
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (ImportError, AttributeError, ValueError):
        pass

    store.init()
    banner = "LIVE (%s)" % config.DODO_MODE if not config.DEMO_MODE else "DEMO"
    print("MoneyPrinter  ->  http://%s:%d   payments: %s"
          % (config.HOST, config.PORT, banner))
    print("  canvas: " + grid.describe())
    if config.DEMO_MODE:
        print("  ! DEMO MODE: claims settle instantly and no money moves.")
        print("    Set DODO_API_KEY, DODO_PRODUCT_ID and DODO_WEBHOOK_KEY to go live.")
    Server((config.HOST, config.PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
