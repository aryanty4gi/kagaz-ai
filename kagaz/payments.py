"""Payment gateway: the user pays for an expert review.

Two layers, both on the Fetch.ai Agent Payment Protocol:
- User -> Kagaz (this file): card/UPI through Stripe Checkout. The coordinator
  is the *seller*: it sends RequestPayment with a Stripe embedded-checkout
  payload that ASI:One can render, plus a hosted checkout link as a backup,
  and checks with Stripe that the session is paid before doing the work.
- Kagaz -> Expert (marketplace agent): agent-to-agent payout per job.

Without Stripe keys the gateway runs in clearly labelled demo mode, so the
demo never breaks. Keys go in Render's environment, never in the repo.
"""

import logging
import os
import time
from uuid import uuid4

log = logging.getLogger("kagaz.payments")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
CURRENCY = os.getenv("STRIPE_CURRENCY", "inr")
SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://agentverse.ai/payment-success")


def enabled() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)


def amount_minor(rupees: str) -> int:
    """Rs 99 -> 9900 paise."""
    return int(round(float(rupees) * 100))


def _stripe():
    import stripe

    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def _expires_at() -> int:
    # Stripe needs expiry 30 min to 24 h ahead.
    return int(time.time()) + 1800


def create_checkout(case_id: str, user: str, rupees: str, description: str) -> dict:
    """Create an embedded session (for ASI:One's payment card) and a hosted one
    (a plain link that works in any browser). Returns the buyer-safe payload."""
    stripe = _stripe()
    common = dict(
        mode="payment",
        expires_at=_expires_at(),
        line_items=[{
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": "Kagaz expert review", "description": description[:300]},
                "unit_amount": amount_minor(rupees),
            },
            "quantity": 1,
        }],
        metadata={"case_id": case_id, "user_address": user, "service": "kagaz_expert_review"},
    )
    embedded = stripe.checkout.Session.create(
        ui_mode="embedded",
        redirect_on_completion="if_required",
        return_url=f"{SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}&case={case_id}",
        **common,
    )
    hosted_url = ""
    hosted_id = ""
    try:
        hosted = stripe.checkout.Session.create(
            success_url=f"{SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}&case={case_id}",
            **common,
        )
        hosted_url, hosted_id = hosted.url, hosted.id
    except Exception as err:  # the embedded session alone is enough
        log.warning("Hosted checkout not created: %s", err)
    return {
        "stripe": {  # never include the secret key here: the buyer sees this
            "ui_mode": "embedded",
            "publishable_key": STRIPE_PUBLISHABLE_KEY,
            "client_secret": embedded.client_secret,
            "checkout_session_id": embedded.id,
            "id": embedded.id,
            "currency": CURRENCY,
            "amount_cents": amount_minor(rupees),
        },
        "session_ids": [s for s in (embedded.id, hosted_id) if s],
        "hosted_url": hosted_url,
    }


def paid_session(session_ids: list[str]) -> tuple[str, str]:
    """Return (session_id, payment_intent) of the first paid session, else ("", "")."""
    stripe = _stripe()
    for sid in session_ids:
        try:
            s = stripe.checkout.Session.retrieve(sid)
        except Exception as err:
            log.warning("Stripe lookup failed for %s: %s", sid, err)
            continue
        if getattr(s, "payment_status", None) == "paid":
            return sid, str(getattr(s, "payment_intent", "") or "")
    return "", ""


def refund(payment_intent: str) -> bool:
    if not (enabled() and payment_intent):
        return False
    try:
        _stripe().Refund.create(payment_intent=payment_intent)
        return True
    except Exception as err:
        log.warning("Refund failed: %s", err)
        return False


def demo_transaction_id() -> str:
    return f"demo-{uuid4().hex[:12]}"
