# the2milliondollarhomepage

In 2005 a student sold 1,000,000 pixels for $1 each. It became a legendary
piece of internet history. **This is the 2026 reboot. Two million pixels.** A
blank canvas. An exercise in digital real estate, ego, art, and chaos.

**How it works**

- The grid is a **1415 × 1415 square** — 2,002,225 pixels.
- **$1 buys one pixel.** No block minimum: drag any rectangle you like, from a
  single pixel upwards, and pay for exactly what is inside it.
- You can claim any available area — **and it stays yours.**

**A pixel is sold once.** Buy it and the area is yours permanently. Nobody can
outbid you, buy it out from under you, or take it back. A selection that
overlaps even one owned pixel cannot be bought at any price; the interface says
so while you are still dragging. When the grid is full, it is full.

Live visitor count, running total and a rankings board, all straight from the
database. Payments are real: a claim opens a Dodo Payments checkout, and pixels
only change hands when Dodo's webhook confirms the money landed.

No build step, no npm, no third-party Python packages — standard library only.

```bash
./run.sh          # http://127.0.0.1:8787
```

---

## Ownership is rectangles, not pixels

Because a sold pixel is never resold, two paid claims can never overlap — so a
claim's rectangle is always owned whole, and there is nothing to store per
pixel. **One purchase is one database row**, whether it covers ten pixels or a
quarter of a million. 275,000 sold pixels is nine rows.

An earlier version kept a row per owned pixel, which was necessary when areas
could be taken off their owner a piece at a time. It cost a 40,000-row write
per large purchase, and the WAL checkpoint that followed froze the whole
process for ~20 seconds — CPython's `sqlite3` holds the GIL across
`sqlite3_close`, so nothing else could run, not even the accept loop. Dropping
the outbidding rule removed the need for the table, and with it the problem:
a maximum-size purchase now takes 3 ms and every other request stays at 1–2 ms.

Two smaller guards remain from that episode: one connection is held open for
the life of the process (SQLite full-checkpoints when the *last* connection
closes), and `/healthz` answers without touching the disk or the database, so
"is it the server or the volume?" is one request away.

---

## The interface

The canvas owns the screen. Controls sit in a rail down the left rather than a
bar across the top, which is what lets the square canvas take the full height
of the window — the dimension that limits it. That alone made the canvas about
12% wider and 25% larger in area, with no change to the pixel count.

| | |
|---|---|
| **Left rail** | price per pixel, zoom, grid toggle, live selection readout, Claim button |
| **Status bar** | pixels sold, total raised, cursor position, block size in screen pixels |
| **Top bar** | running totals, grid / rankings, What is this?, your Twitter, Claim |
| **Rankings window** | the ownership board, sortable by pixels / spend / recency, with the numbers and recent buyers beside it |

---

## Naming it and adding your Twitter

All three are environment variables — no code to touch:

```
SITE_NAME=A Million Pixels
SITE_TAGLINE=Own a piece of him.
TWITTER_HANDLE=yourhandle      # just the handle, no @ and no URL
```

`TWITTER_HANDLE` puts a link in the top bar and in the footer, pointing at
`https://x.com/<handle>`. Leave it blank and the link simply isn't rendered.

---

## The canvas

Set in `grid.py`, all overridable from the environment:

| | default | |
|---|---|---|
| `GRID_COLS` × `GRID_ROWS` | 1415 × 1415 | **2,002,225 pixels**, square |
| `TILE_PX` | 1 | one real pixel per unit of sale |
| `PIXEL_CENTS` | 100 | **$1 a pixel** |
| `TILE_FLOOR_CENTS` | *derived* | set directly to break the per-pixel link |
| `MAX_TILES_PER_CLAIM` | 250000 | biggest box one payment may cover |

Sold out at these numbers: **$2,002,225**.

No square holds exactly two million pixels — the root is 1414.21 — so it is
either 1414 (1,999,396, which is 604 short of the name) or 1415, which clears
it. Rounding up keeps "two million pixels" true. `GRID_COLS=1414
GRID_ROWS=1414` if you would rather sit just under.

`TILE_PX` is how many real pixels make one unit of sale. At `1` the price is
literally a dollar a pixel and buyers can take any shape at all. Raise it and
the canvas reverts to the Million Dollar Homepage's model — 10×10 blocks at
$100 — with nothing else changing anywhere, interface wording included:

```bash
GRID_COLS=100 GRID_ROWS=100 TILE_PX=10 ./run.sh
```

Selling by the pixel means up to a million rows, so nothing is materialised
until it is bought and a purchase is capped. Settling the largest allowed
box — 40,000 pixels, 40,000 rows — measures at ~60 ms.

The canvas is square and empty. Sold patches are the only things drawn on it,
so an empty board costs one element and a busy one costs a few dozen.

*"Tile" is the internal name throughout the code for what the site calls a
block — the database and geometry use `tile`, the interface says `block`.*

125 × 160 keeps a tile close to square on a 3:4 portrait. Want a coarse board
of a hundred big pixels instead? `GRID_COLS=10 GRID_ROWS=10 TILE_PX=1`.
Nothing else in the code cares.

### Seeing the pixels

Fitted to a window a block is about seven screen pixels across — too small to
aim at reliably. Two things handle that:

- **Two grids.** At fit, only the every-tenth "major" lines are drawn, like
  graph paper; drawing all 285 lines at that size is just grey mush. The fine
  per-tile lines switch on automatically once a tile is at least 5px wide.
  Every line is dark-over-light so it reads on a bright forehead and on a
  black background alike.
- **Zoom** — `fit / 2× / 4× / 8×`, in the toolbar above the canvas, which also
  reports the current block size in screen pixels. Zooming only changes the
  frame's width in pixels; everything inside is positioned as a percentage of
  it, so selection, pricing and logo placement need no knowledge of zoom at
  all. The view stays anchored on whatever you were last pointing at.

Sold patches carry a hairline outline, so taken and free space are told apart
even where a patch is mostly white.

**The canvas is never materialised.** There is no row per pixel and no row per
tile — the grid is arithmetic, and only tiles somebody has actually bought
become rows. An empty canvas is an empty table. That is what makes two
million affordable; ten million would be no harder.

Changing the grid shape clears tile ownership, because a tile index means
nothing under a different grid. The payment ledger is never touched.

---

## How the money works

- Every free pixel costs $1. A selection is priced at exactly its area.
- A selection containing **any** owned pixel is not for sale, at any price.
  `GET /api/quote` reports `available` so the interface can say so mid-drag.
- Prices and availability are rechecked at settlement, never trusted from
  checkout time.

Prices are recomputed at settlement, never trusted from checkout time. In the
normal case nothing has moved and you get the whole box. If a tile got dearer
while your payment was in flight, your budget buys the cheapest tiles it still
covers, the rest stay with their owner, and the shortfall is logged as
`held_partially` — a refund you owe, visible rather than buried.

A claim holds nothing until the webhook confirms it. Unpaid claims expire
after 30 minutes.

---

## Wiring up payments

In demo mode (no API key) purchases settle instantly, no money moves, and the
page says so in a yellow banner.

### 1. In the Dodo Payments dashboard

- Create **one product**: *Single Payment*, **Pay What You Want** pricing
  enabled, minimum $100. Every purchase checks out against this one product —
  the box's total is sent per checkout as the cart amount. PWYW is one-time
  only; it cannot be a subscription product.
- Add a **webhook endpoint** at `https://YOUR-DOMAIN/api/webhook/dodo`,
  subscribed to at least `payment.succeeded`. Copy the signing secret from the
  endpoint's Overview tab.

### 2. In `.env`

```bash
cp .env.example .env
```

```
DODO_MODE=test                # then `live` when you are ready
DODO_API_KEY=...
DODO_PRODUCT_ID=...           # the PWYW product
DODO_WEBHOOK_KEY=whsec_...
BASE_URL=https://your-domain  # the real public origin, not localhost
```

### 3. Test it end to end

Keep `DODO_MODE=test`, pay with a Dodo test card, and watch the log for
`settled clm_… : held, 80 tile(s)`. Then flip to `DODO_MODE=live`.

Webhooks cannot reach localhost. For local testing use a tunnel
(`cloudflared tunnel --url http://localhost:8787`) and point `BASE_URL` at it.

---

## Layout

```
grid.py                 the canvas: size, tiles, floor price, geometry maths
app.py                  HTTP server, routing, validation, webhook handling
store.py                SQLite: sold blocks, the claim ledger, visitors
dodo.py                 Dodo checkout + Standard Webhooks signature check
config.py               all configuration, read from the environment
public/index.html       the desktop
public/site.css         the dark, canvas-first interface
public/app.js           canvas painting, drag selection, zoom, the dialog
public/thanks.html      post-payment landing, polls until the webhook lands
data/app.db             SQLite, created on first run
```

### Endpoints

| | |
|---|---|
| `GET /` | the page (counts a pageview) |
| `GET /api/state` | grid shape, everything bought, ledger, stats |
| `GET /api/quote?col=&row=&cols=&rows=` | live price for a dragged box |
| `POST /api/claim` | validate a box, open a claim, return a checkout URL |
| `GET /api/claim/<id>` | claim status, polled by the thanks page |
| `POST /api/webhook/dodo` | signed Dodo callback; the only thing that grants pixels |
| `GET /healthz` | liveness, touching neither disk nor database |
| `GET /logo/<claim_id>` | one purchase's logo, cached forever |

Logos are served from `/logo/…` rather than inlined into `/api/state`: on a
canvas this size, base64 in the state payload would be megabytes on every
poll. An empty canvas's state response is under a kilobyte.

## Visitor counting

An `mp_vid` cookie (HttpOnly, one year) is set on the first pageview. The
counter shows unique visitors, total pageviews, and how many people loaded a
page in the last 5 minutes. Only `GET /` counts — the client's 12-second
poll does not inflate it.

## Housekeeping

Wipe demo data before going live:

```bash
rm -f data/app.db data/app.db-wal data/app.db-shm
```

---

## Deploying to Railway

Railway runs the process as-is, so nothing about the app changes: the same
SQLite file, the same single process, exactly what is tested locally.

### 1. Create the service

New Project → Deploy from GitHub → this repo. `railway.json` supplies the
start command (`python3 app.py`) and points the health check at `/healthz`,
which answers without touching the database.

### 2. Add a volume — do this before the first real claim

Storage → Add Volume, mounted at **`/data`**. Without it the database lives on
the container's ephemeral disk and every claim, payment record and visitor
count is wiped on the next redeploy.

Then set `DATA_DIR=/data` so the app writes there.

### 3. Environment variables

| | |
|---|---|
| `DATA_DIR` | `/data` — the mounted volume |
| `BASE_URL` | `https://your-app.up.railway.app` — where Dodo returns payers |
| `SITE_NAME` | `the2milliondollarhomepage` |
| `TWITTER_HANDLE` | `hisahibul` |
| `DODO_API_KEY` / `DODO_PRODUCT_ID` / `DODO_WEBHOOK_KEY` | from the Dodo dashboard |
| `DODO_MODE` | `test`, then `live` |

`PORT` and `HOST` are handled for you: Railway injects `PORT`, and the app
takes that as the signal to bind `0.0.0.0` rather than localhost.

Leave the Dodo keys unset and the site runs in demo mode — claims settle
instantly, no money moves, and the page says so.

### 4. Point Dodo at it

Webhook endpoint `https://your-domain/api/webhook/dodo`, subscribed to
`payment.succeeded`. Copy its signing secret into `DODO_WEBHOOK_KEY`.

### Going live

1. Deploy with `DODO_MODE=test` and buy a patch with a Dodo test card.
2. Watch the logs for `settled clm_… : held`.
3. Wipe the test data (`rm /data/app.db*` via a shell, or redeploy the volume).
4. Set `DODO_MODE=live` and the live keys.

### If something is wrong

`https://your-domain/healthz` answers without the database or the disk. If it
returns `ok` but the site does not load, the problem is the volume or
`DATA_DIR`, not the deploy.

---

## Deploying somewhere serverless instead

The repo also carries what Vercel needs — `api/index.py` (Vercel drives a
`BaseHTTPRequestHandler` per request, and `app.Handler` already is one) and
`vercel.json`. Serverless has no disk, so it additionally needs a Postgres:
set `DATABASE_URL` and `db.py` switches engines, the SQL being written in the
subset SQLite and Postgres share. Uncomment `psycopg2-binary` in
`requirements.txt`.

Railway needs none of that, which is why it is the path above.

## Housekeeping

Wipe demo data before going live:

```bash
rm -f data/app.db data/app.db-wal data/app.db-shm
```

---

## Deploying to Vercel

Vercel is serverless, which changes two things and nothing else:

- **`api/index.py` is the entry point.** Vercel drives a
  `BaseHTTPRequestHandler` per request, and `app.Handler` already is one, so
  the whole router is reused. `vercel.json` rewrites every path to it, keeping
  routing in one place. Nothing calls `serve_forever` there.
- **Postgres replaces SQLite.** Vercel's filesystem is read-only apart from a
  per-invocation `/tmp`, so a SQLite file would lose every claim between
  requests. Set `DATABASE_URL` and `db.py` switches engines; the SQL is written
  once in the subset both understand.

Two smaller consequences of having no shared memory between instances: the
rate limiter and the "sweep at most once a minute" marker both live in the
database rather than in a module-level dict.

### 1. A database

Add **Vercel Postgres** (Neon) from the project's Storage tab, or create a free
Neon project and copy its connection string. Use the **pooled** connection
string — serverless opens a connection per invocation and a direct endpoint
will run out.

### 2. Environment variables

In Vercel → Settings → Environment Variables:

| | |
|---|---|
| `DATABASE_URL` | the pooled Postgres string |
| `BASE_URL` | `https://your-domain.vercel.app` — where Dodo sends payers back |
| `SITE_NAME` | `the2milliondollarhomepage` |
| `TWITTER_HANDLE` | `hisahibul` |
| `DODO_API_KEY` / `DODO_PRODUCT_ID` / `DODO_WEBHOOK_KEY` | from the Dodo dashboard |
| `DODO_MODE` | `test`, then `live` |

Leave the Dodo keys unset and the site runs in demo mode: claims settle
instantly, no money moves, and the page says so.

### 3. Deploy

Import the repo, **Application Preset: Other**, no build command. The schema is
created on the first cold start.

### 4. Point Dodo at it

Webhook endpoint `https://your-domain/api/webhook/dodo`, subscribed to
`payment.succeeded`. Copy its signing secret into `DODO_WEBHOOK_KEY`.

### What to check first

`https://your-domain/healthz` answers without touching the database — if that
works but `/api/state` does not, the problem is `DATABASE_URL`, not the deploy.

### Running it locally is unchanged

No `DATABASE_URL` means SQLite and a normal process:

```bash
./run.sh
```

## Deploying

Any host running Python 3.9+. Put it behind TLS, set `HOST=0.0.0.0`,
`PORT=$PORT` and the real `BASE_URL`. `data/` must be a persistent volume —
it holds every payment record. The server reads `X-Forwarded-For` for rate
limiting, so make sure your proxy sets it.

## Notes

- Uploaded logos are decoded and size-checked server side; SVG is rejected
  outright, and only PNG/JPEG/WebP/GIF are stored.
- Websites are checked for a real http(s) scheme and host, and rendered with
  `rel="noopener noreferrer nofollow"`.
- Rate limiting is one attempt per 3 s and 15 opened claims per hour per
  address. Failed validation costs only the spacing.
- Webhook deliveries are recorded by id, so a redelivery cannot double-settle.
