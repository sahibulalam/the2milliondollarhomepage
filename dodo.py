"""Dodo Payments: create a checkout session, verify the webhook.

Only two calls are needed, so this is urllib rather than the SDK -- the app has
no third-party dependencies at all.

Pricing model: one "Pay What You Want" single-payment product in the Dodo
dashboard covers every area. The bid is sent per checkout as
product_cart[0].amount, in the currency's smallest unit (cents for USD).
Docs: https://docs.dodopayments.com/developer-resources/dynamic-pricing-checkout
"""
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

import config


class DodoError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _post(path, payload):
    req = urllib.request.Request(
        config.DODO_API_BASE + path,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + config.DODO_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            return json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise DodoError("Dodo returned %d" % e.code, e.code, body)
    except urllib.error.URLError as e:
        raise DodoError("Could not reach Dodo: %s" % e.reason)


def create_checkout(claim_id, rect_ref, what, brand, amount_cents, email=None):
    """Returns the CheckoutSessionResponse dict: session_id, checkout_url, ..."""
    body = {
        "product_cart": [{
            "product_id": config.DODO_PRODUCT_ID,
            "quantity": 1,
            "amount": int(amount_cents),
        }],
        "return_url": "%s/thanks?claim=%s" % (config.BASE_URL, claim_id),
        "metadata": {
            # Comes back verbatim on the payment in the webhook. This is the
            # only link between the money and the area, so keep it exact.
            "claim_id": claim_id,
            "rect": rect_ref,
            "what": what,
            "brand": brand[:120],
        },
        "customization": {"theme": "light", "show_order_details": True},
        "feature_flags": {"allow_discount_code": False},
    }
    if config.CURRENCY:
        body["billing_currency"] = config.CURRENCY
    if email:
        body["customer"] = {"email": email}
    return _post("/checkouts", body)


# --- webhook verification (Standard Webhooks) ------------------------------

def verify(headers, raw_body):
    """Raise DodoError unless the signature checks out. Returns the webhook id.

    Signed content is "{webhook-id}.{webhook-timestamp}.{raw body}", HMAC-SHA256
    with the base64-decoded secret, compared against any v1 entry in the
    space-separated webhook-signature header.
    """
    wid = headers.get("webhook-id")
    wts = headers.get("webhook-timestamp")
    wsig = headers.get("webhook-signature")
    if not (wid and wts and wsig):
        raise DodoError("missing webhook signature headers")
    if not config.DODO_WEBHOOK_KEY:
        raise DodoError("DODO_WEBHOOK_KEY is not configured")

    secret = config.DODO_WEBHOOK_KEY
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    try:
        key = base64.b64decode(secret)
    except Exception:
        key = secret.encode()

    signed = b"%s.%s." % (wid.encode(), wts.encode()) + raw_body
    expect = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    for part in wsig.split(" "):
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(value, expect):
            return wid
    raise DodoError("webhook signature did not match")
