"""Configuration, all of it from the environment.

Nothing here has a live default: the app boots and serves in DEMO mode when
DODO_API_KEY is unset, so the site is usable before the payment account is
wired up. See README.md.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def _env(name, default=""):
    return os.environ.get(name, default).strip()


# --- server ---------------------------------------------------------------
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8787"))
# Public origin, used to build return_url for the checkout. Must be the URL a
# browser can reach; Dodo redirects the payer here after paying.
BASE_URL = _env("BASE_URL", "http://127.0.0.1:%d" % PORT).rstrip("/")

DB_PATH = ROOT / "data" / "app.db"
PUBLIC = ROOT / "public"

# --- dodo payments --------------------------------------------------------
# Test mode talks to test.dodopayments.com, live mode to live.dodopayments.com.
DODO_MODE = (_env("DODO_MODE", "test") or "test").lower()
DODO_API_BASE = (
    "https://live.dodopayments.com"
    if DODO_MODE == "live"
    else "https://test.dodopayments.com"
)
DODO_API_KEY = _env("DODO_API_KEY")
# One "Pay What You Want" single-payment product covers every area: the bid is
# passed per checkout as product_cart[0].amount, in cents.
DODO_PRODUCT_ID = _env("DODO_PRODUCT_ID")
DODO_WEBHOOK_KEY = _env("DODO_WEBHOOK_KEY")

# With no API key the claim flow settles itself instantly and marks the row as
# a demo payment. Never leave this on in production.
DEMO_MODE = not (DODO_API_KEY and DODO_PRODUCT_ID)

# --- auction rules --------------------------------------------------------
CURRENCY = _env("CURRENCY", "USD")
# Sold pixels stay sold: there is no outbidding, so there is nothing to tune.
# A pending claim holds nothing; it just expires if the payer never pays.
CLAIM_TTL_SECONDS = int(_env("CLAIM_TTL_SECONDS", "1800"))   # 30 min
# A pixel is a tenth of the photo across, so logos are small by nature.
MAX_LOGO_BYTES = int(_env("MAX_LOGO_BYTES", "48000"))
ONLINE_WINDOW_SECONDS = 300

# --- presentation ---------------------------------------------------------
SITE_NAME = _env("SITE_NAME", "the2milliondollarhomepage")
SITE_TAGLINE = _env("SITE_TAGLINE", "Two million pixels. A dollar each. Yours for good.")
# Just the handle, no @ and no URL. Left blank, the link is simply not shown.
TWITTER_HANDLE = _env("TWITTER_HANDLE").lstrip("@")
