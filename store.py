"""SQLite persistence.

Ownership is a list of rectangles, not a list of pixels.

Because a sold pixel is never resold, two paid claims can never overlap -- so
a claim's rectangle is always owned whole, and there is nothing to record per
pixel. One purchase is one row, whether it covers ten pixels or forty thousand.

The earlier design kept a row per owned pixel. It was needed when areas could
be taken off their owner a pixel at a time, and it made a large purchase write
tens of thousands of rows; the WAL checkpoint that followed froze the whole
process (CPython's sqlite3 holds the GIL across sqlite3_close). Dropping the
outbidding rule dropped the need for it.

  claim -- one purchase: a rectangle, a brand, artwork, an amount, a status.
           The complete ledger. Rows are never deleted; `status` says whether
           a row owns its rectangle (paid) or not (pending/failed/refund_due).
"""
import json
import secrets
import time

import config
import db as _db
import grid

SCHEMA = """
CREATE TABLE IF NOT EXISTS claim (
  id           TEXT PRIMARY KEY,
  col          INTEGER NOT NULL,
  row          INTEGER NOT NULL,
  cols         INTEGER NOT NULL,
  rows         INTEGER NOT NULL,
  tile_count   INTEGER NOT NULL,          -- units of sale in the rectangle
  brand        TEXT NOT NULL,
  url          TEXT,
  logo         TEXT,
  email        TEXT,
  amount_cents INTEGER NOT NULL,
  status       TEXT NOT NULL,             -- pending|paid|failed|expired|refund_due
  session_id   TEXT,
  payment_id   TEXT,
  checkout_url TEXT,
  created_at   INTEGER NOT NULL,
  settled_at   INTEGER
);
CREATE INDEX IF NOT EXISTS claim_paid   ON claim(status, settled_at);
CREATE INDEX IF NOT EXISTS claim_pos    ON claim(status, col, row);
CREATE INDEX IF NOT EXISTS claim_brand  ON claim(brand);

CREATE TABLE IF NOT EXISTS visitor (
  vid        TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  hits       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS visitor_last ON visitor(last_seen);

CREATE TABLE IF NOT EXISTS webhook_seen (
  webhook_id TEXT PRIMARY KEY,
  at         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

-- Rate limiting lives here rather than in memory: serverless instances share
-- no state, so a per-process dict would let anyone around the limit simply by
-- landing on a cold instance.
CREATE TABLE IF NOT EXISTS attempt (
  ip     TEXT NOT NULL,
  at     INTEGER NOT NULL,
  opened INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS attempt_ip ON attempt(ip, at);
"""

# Held open for the life of the process so SQLite's connection count never
# reaches zero -- it runs a full WAL checkpoint when the last one closes, and
# CPython holds the GIL across that. Irrelevant under Postgres.
_keeper = None


def connect():
    return _db.connect()


def init():
    global _keeper
    con = connect()
    if not _db.IS_POSTGRES:
        mode = con.pragma("journal_mode=WAL")
        if mode != "wal":
            print("warning: journal_mode is %r, not wal" % mode)
    with con:
        con.executescript(SCHEMA)
        shape = json.dumps({"cols": grid.COLS, "rows": grid.ROWS,
                            "tile_px": grid.TILE_PX})
        was = con.execute("SELECT v FROM meta WHERE k='grid'").fetchone()
        if was and was["v"] != shape:
            print("grid reshaped %s -> %s; existing claims keep their "
                  "coordinates" % (was["v"], shape))
        con.execute("INSERT INTO meta (k,v) VALUES ('grid',?)"
                    " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (shape,))
    con.close()
    if _keeper is None and not _db.IS_POSTGRES:
        _keeper = connect()


# --------------------------------------------------------------- overlap --

# Two rectangles miss each other when one is entirely left of, right of, above
# or below the other. Everything else is an overlap.
_OVERLAP = ("status='paid' AND col < ? AND ? < col + cols"
            " AND row < ? AND ? < row + rows")


def _bounds(col, row, cols, rows):
    # Order matches the placeholders in _OVERLAP.
    return (col + cols, col, row + rows, row)


def overlapping(con, col, row, cols, rows, limit=8):
    """Paid claims that intersect this rectangle."""
    return con.execute(
        "SELECT id, brand, col, row, cols, rows FROM claim WHERE "
        + _OVERLAP + " LIMIT ?",
        _bounds(col, row, cols, rows) + (limit,)).fetchall()


def overlap_area(con, col, row, cols, rows):
    """How many units of sale inside this rectangle are already owned."""
    total = 0
    for r in con.execute("SELECT col, row, cols, rows FROM claim WHERE " + _OVERLAP,
                         _bounds(col, row, cols, rows)):
        w = min(col + cols, r["col"] + r["cols"]) - max(col, r["col"])
        h = min(row + rows, r["row"] + r["rows"]) - max(row, r["row"])
        total += max(0, w) * max(0, h)
    return total


def quote(con, col, row, cols, rows):
    """Price a selection, and say whether it can be bought at all.

    A rectangle is only for sale if every pixel in it is unsold. Overlapping
    even one owned pixel blocks the whole thing, which is why `taken` is
    reported: the interface says so while you are still dragging, rather than
    failing at the checkout.
    """
    n = cols * rows
    taken = overlap_area(con, col, row, cols, rows)
    return {
        "col": col, "row": row, "cols": cols, "rows": rows,
        "tiles": n, "free": n - taken, "taken": taken,
        "available": taken == 0,
        "pixels": n * grid.TILE_PX * grid.TILE_PX,
        "total_cents": n * grid.FLOOR_CENTS,
    }


# ----------------------------------------------------------------- reads --

def board(con):
    """Every rectangle that is owned. One row each, no geometry to expand."""
    rows = con.execute(
        "SELECT id, brand, url, (logo IS NOT NULL) AS has_logo,"
        "       col, row, cols, rows, tile_count, amount_cents, settled_at"
        " FROM claim WHERE status='paid' ORDER BY settled_at"
    ).fetchall()
    return [{
        "id": r["id"], "brand": r["brand"], "url": r["url"],
        "logo": ("/logo/" + r["id"]) if r["has_logo"] else None,
        "col": r["col"], "row": r["row"], "cols": r["cols"], "rows": r["rows"],
        "owned": r["tile_count"], "whole": True,
        "since": r["settled_at"],
        "tile_cents": (r["amount_cents"] // r["tile_count"]) if r["tile_count"] else 0,
    } for r in rows]


def ledger(con, limit=60):
    rows = con.execute(
        "SELECT id, col, row, cols, rows, tile_count, brand, url,"
        "       (logo IS NOT NULL) AS has_logo, amount_cents, settled_at, status"
        " FROM claim WHERE status IN ('paid','refund_due')"
        " ORDER BY settled_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["logo"] = ("/logo/" + d["id"]) if d.pop("has_logo") else None
        d["current"] = d["status"] == "paid"
        d["still_owned"] = d["tile_count"] if d["current"] else 0
        out.append(d)
    return out


def rankings(con, limit=100):
    """Advertisers by how much of the page they hold."""
    px = grid.TILE_PX * grid.TILE_PX
    rows = con.execute(
        "SELECT brand,"
        "       SUM(tile_count) AS tiles, COUNT(*) AS buys,"
        "       SUM(amount_cents) AS cents, MIN(settled_at) AS since,"
        "       MAX(settled_at) AS last_won"
        " FROM claim WHERE status='paid' GROUP BY brand").fetchall()
    face = {}
    for r in con.execute("SELECT brand, id, url, (logo IS NOT NULL) AS has_logo"
                         " FROM claim WHERE status='paid' ORDER BY settled_at"):
        face[r["brand"]] = {"url": r["url"],
                            "logo": ("/logo/" + r["id"]) if r["has_logo"] else None}
    out = []
    for r in rows:
        pixels = r["tiles"] * px
        out.append({
            "brand": r["brand"], "blocks": r["tiles"], "pixels": pixels,
            "invested_cents": r["cents"], "buys": r["buys"],
            "avg_px_cents": (r["cents"] / float(pixels)) if pixels else 0,
            "since": r["since"], "last_won": r["last_won"],
            "url": face.get(r["brand"], {}).get("url"),
            "logo": face.get(r["brand"], {}).get("logo"),
        })
    out.sort(key=lambda x: (-x["pixels"], -x["invested_cents"], x["brand"]))
    return out[:limit]


def stats(con):
    now = int(time.time())
    paid = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(amount_cents),0) s,"
        "       COALESCE(SUM(tile_count),0) t"
        " FROM claim WHERE status='paid'").fetchone()
    v = con.execute(
        "SELECT COUNT(*) uniq, COALESCE(SUM(hits),0) hits, MIN(first_seen) since"
        " FROM visitor").fetchone()
    online = con.execute("SELECT COUNT(*) n FROM visitor WHERE last_seen > ?",
                         (now - config.ONLINE_WINDOW_SECONDS,)).fetchone()["n"]
    top = con.execute("SELECT brand, amount_cents, tile_count FROM claim"
                      " WHERE status='paid' ORDER BY amount_cents DESC"
                      " LIMIT 1").fetchone()
    px = grid.TILE_PX * grid.TILE_PX
    return {
        "visitors": v["uniq"], "pageviews": v["hits"],
        "online": online, "since": v["since"] or now,
        "sales": paid["n"], "total_cents": paid["s"],
        "tiles_sold": paid["t"], "tiles": grid.TILES,
        "pixels_sold": paid["t"] * px, "pixels": grid.PIXELS,
        "top_tile_cents": ((top["amount_cents"] // top["tile_count"])
                           if top and top["tile_count"] else 0),
        "top_tile_brand": top["brand"] if top else None,
    }


def get_logo(con, claim_id):
    r = con.execute("SELECT logo FROM claim WHERE id=?", (claim_id,)).fetchone()
    return r["logo"] if r else None


def get_claim(con, claim_id):
    r = con.execute("SELECT * FROM claim WHERE id=?", (claim_id,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- writes --

def touch_visitor(con, vid):
    now = int(time.time())
    with con:
        cur = con.execute(
            "UPDATE visitor SET last_seen=?, hits=hits+1 WHERE vid=?", (now, vid))
        if cur.rowcount:
            return False
        con.execute("INSERT OR IGNORE INTO visitor (vid,first_seen,last_seen,hits)"
                    " VALUES (?,?,?,1)", (vid, now, now))
    return True


def open_claim(con, rect, brand, url, logo, email, amount_cents):
    cid = "clm_" + secrets.token_urlsafe(12)
    with con:
        con.execute(
            "INSERT INTO claim (id,col,row,cols,rows,tile_count,brand,url,logo,"
            " email,amount_cents,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)",
            (cid, rect["col"], rect["row"], rect["cols"], rect["rows"],
             rect["cols"] * rect["rows"], brand, url, logo, email,
             amount_cents, int(time.time())))
    return cid


def attach_session(con, claim_id, session_id, checkout_url, payment_id=None):
    with con:
        con.execute(
            "UPDATE claim SET session_id=?, checkout_url=?,"
            " payment_id=COALESCE(?,payment_id) WHERE id=?",
            (session_id, checkout_url, payment_id, claim_id))


def fail_claim(con, claim_id, status="failed"):
    with con:
        con.execute(
            "UPDATE claim SET status=?, settled_at=? WHERE id=? AND status='pending'",
            (status, int(time.time()), claim_id))


def settle_claim(con, claim_id, payment_id):
    """Money landed: hand over the rectangle, if it is all still free.

    Sold is sold, so this never takes anything off anybody. The only way a paid
    claim misses out is a race -- two people paying for overlapping areas at
    once. The first settlement wins; the second is marked refund_due and shows
    on the ledger as such, because that is money the operator owes back rather
    than something to bury.
    """
    now = int(time.time())
    with con:
        c = con.execute("SELECT * FROM claim WHERE id=?", (claim_id,)).fetchone()
        if not c:
            return False, "unknown_claim", 0
        if c["status"] == "paid":
            return True, "already_settled", c["tile_count"]

        clash = overlapping(con, c["col"], c["row"], c["cols"], c["rows"], limit=1)
        if clash:
            con.execute("UPDATE claim SET status='refund_due', settled_at=?,"
                        " payment_id=? WHERE id=?", (now, payment_id, claim_id))
            return True, "lost_race", 0

        con.execute("UPDATE claim SET status='paid', settled_at=?, payment_id=?"
                    " WHERE id=?", (now, payment_id, claim_id))
    return True, "held", c["tile_count"]


def _meta_int(con, key, default=0):
    r = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    try:
        return int(r["v"]) if r else default
    except (TypeError, ValueError):
        return default


def expire_stale(con, every=60):
    """Retire unpaid claims that ran out of time, and prune old rate rows.

    Called from read paths, so it must not turn every page load into a write:
    it runs at most once a minute -- tracked in the database, because
    serverless instances do not share memory -- and only writes when there is
    something to do.
    """
    now = int(time.time())
    if now - _meta_int(con, "last_sweep") < every:
        return
    with con:
        con.execute("INSERT INTO meta (k,v) VALUES ('last_sweep',?)"
                    " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(now),))
    cutoff = now - config.CLAIM_TTL_SECONDS
    if con.execute("SELECT 1 FROM claim WHERE status='pending'"
                   " AND created_at < ? LIMIT 1", (cutoff,)).fetchone():
        with con:
            con.execute("UPDATE claim SET status='expired', settled_at=?"
                        " WHERE status='pending' AND created_at < ?",
                        (now, cutoff))
    with con:
        con.execute("DELETE FROM attempt WHERE at < ?", (now - 3600,))


# ------------------------------------------------------------ rate limits --

SPACING_SECONDS = 3
CLAIMS_PER_HOUR = 15


def throttled(con, ip):
    """True if this address is going too fast.

    No more than one attempt every few seconds, and a cap on claims actually
    opened per hour. Attempts that fail validation cost only the spacing --
    fumbling the form should not lock you out of buying.
    """
    now = int(time.time())
    r = con.execute(
        "SELECT MAX(at) AS last, COALESCE(SUM(opened),0) AS opened"
        " FROM attempt WHERE ip=? AND at > ?", (ip, now - 3600)).fetchone()
    if r and r["last"] and now - int(r["last"]) < SPACING_SECONDS:
        return True
    if r and int(r["opened"] or 0) >= CLAIMS_PER_HOUR:
        return True
    with con:
        con.execute("INSERT INTO attempt (ip, at, opened) VALUES (?,?,0)",
                    (ip, now))
    return False


def count_claim(con, ip):
    """Record that a claim was actually opened, against the hourly budget."""
    with con:
        con.execute("INSERT INTO attempt (ip, at, opened) VALUES (?,?,1)",
                    (ip, int(time.time())))


def webhook_is_new(con, webhook_id):
    """Standard Webhooks redelivers; settle each delivery id exactly once.

    INSERT OR IGNORE rather than catching an integrity error, so this needs no
    engine-specific exception type -- rowcount tells us whether it was new.
    """
    with con:
        cur = con.execute(
            "INSERT OR IGNORE INTO webhook_seen (webhook_id, at) VALUES (?,?)",
            (webhook_id, int(time.time())))
    return cur.rowcount > 0
