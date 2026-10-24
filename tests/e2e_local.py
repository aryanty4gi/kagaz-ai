"""End-to-end test: a simulated ASI:One user walks the whole Kagaz flow.

    KAGAZ_USE_MAILBOX=false KAGAZ_ONLINE_CHECKS=false python -m tests.e2e_local

Scenario A (scam):    fake e-challan SMS -> scam card -> report pack -> "check officially"
Scenario B (genuine): electricity notice -> review card -> proceed -> remind
                      -> expert offer -> pay -> paid expert review arrives
                      (a verified human CA agent registers with the marketplace,
                       gets the paid job, and answers it over chat)
Scenario D (reuse):   an outside "SMS Shield" agent calls the Verify agent directly
Scenario C (image):   screenshot of a "digital arrest" SMS -> OCR -> scam card
Scenario E (solo):    a second user chats with each specialist directly (no
                      coordinator) and gets a full answer from each one
The simulated user clicks card buttons by replying with each CTA's selection
JSON, exactly as ASI:One does for a direct @mention.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("KAGAZ_USE_MAILBOX", "false")
os.environ.setdefault("KAGAZ_ONLINE_CHECKS", "false")
os.environ.setdefault("KAGAZ_AUTO_APPROVE_EXPERTS", "true")
for _k in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"):
    os.environ.pop(_k, None)  # gateway runs in demo mode in tests

from uagents import Agent, Bureau, Context  # noqa: E402
from uagents_core.contrib.protocols.chat import (  # noqa: E402
    ChatAcknowledgement, ChatMessage, MetadataContent, Resource, ResourceContent, TextContent,
)

from kagaz import config  # noqa: E402
from kagaz.cards import text_message  # noqa: E402
from run_local import build_all  # noqa: E402
from agents import coordinator_agent, expert_agent  # noqa: E402
from tests.test_core import DIGITAL_ARREST, render_png  # noqa: E402

# E2E_FAKE_STRIPE=1 runs the payment step through the gateway code path with a
# fake Stripe that reports the checkout as paid.
FAKE_STRIPE = os.getenv("E2E_FAKE_STRIPE") == "1"
if FAKE_STRIPE:
    from kagaz import payments as _gw

    _gw.enabled = lambda: True
    _gw.create_checkout = lambda case_id, user, rupees, desc: {
        "stripe": {"ui_mode": "embedded", "client_secret": "cs_secret_x",
                   "checkout_session_id": "cs_test_e2e", "publishable_key": "pk_test"},
        "session_ids": ["cs_test_e2e"], "hosted_url": "https://checkout.stripe.com/c/pay/cs_test_e2e"}
    _gw.paid_session = lambda ids: ("cs_test_e2e", "pi_e2e")

# Stand-in for Agentverse storage: the "uploaded" screenshot.
SCREENSHOT = render_png(DIGITAL_ARREST, dark=True)
coordinator_agent.download_image = lambda item, identity: (SCREENSHOT, "image/png")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {p.name: p.read_text() for p in (ROOT / "samples").glob("*.txt")}
COORD = config.address_for("coordinator")

log: list[dict] = []

# Scenario D: a third-party agent that only knows Verify's public message models.
from uagents import Model  # noqa: E402


class VerifyRequest(Model):
    notice_text: str
    fields_json: str = "{}"


class VerifyResult(Model):
    ok: bool
    report_json: str
    error: str = ""


shield = Agent(name="sms_shield_demo", seed="kagaz-e2e-sms-shield", port=8197,
               endpoint=["http://127.0.0.1:8197/submit"])
shield_result: dict = {}
shield_asked: list = []


@shield.on_interval(period=3.0)
async def shield_asks(ctx: Context):
    if shield_result or shield_asked:
        return
    shield_asked.append(True)
    await ctx.send(config.address_for("verify"), VerifyRequest(
        notice_text="Congratulations! You won KBC lottery of 25 lakh. Pay Rs 15,000 "
                    "processing fee to claim. WhatsApp 9123456789"))


@shield.on_message(VerifyResult)
async def shield_got(ctx: Context, sender: str, msg: VerifyResult):
    shield_result.update(json.loads(msg.report_json))
    print(f"\nSMS SHIELD << Kagaz Verify says {shield_result.get('verdict')} "
          f"(risk {shield_result.get('score')})")

# A human CA running their own Expert agent (see docs/EXPERTS.md).
human_ca = expert_agent.build(False, profile={
    "mode": "human", "name": "Test CA", "profession": "CA", "credential_id": "123456",
    "specialties": ["electricity_disconnection"], "fee": "0.4", "languages": ["en"],
    "ai_assisted": False, "pin": "4321", "seed": "kagaz-e2e-test-human-ca",
})

user = Agent(name="test_user", seed="kagaz-e2e-test-user", port=8199,
             endpoint=["http://127.0.0.1:8199/submit"])

# What the user does after each card kind / message, per scenario step.
script = [
    ("send", SAMPLES["01_fake_echallan_sms.txt"]),
    ("expect_card", "custom", {"action": "report_scam"}),
    # Cards are single-use in ASI:One, so each follow-up carries a fresh card
    # with only the buttons still left (4th item = exact button actions).
    ("expect_text", "Your scam report pack", {"action": "proceed"}, {"proceed", "done"}),
    ("expect_card", "detail", None),
    ("send", SAMPLES["04_electricity_disconnection.txt"]),
    ("expect_card", "review", {"action": "proceed"}),
    ("expect_card", "detail", {"action": "remind"}),
    ("expect_text", "Done. I'll message you", {"action": "expert"}, {"expert", "done"}),
    ("expect_card", "review", {"action": "pay_expert"}),
    *([("expect_card", "review", {"action": "check_payment"}),
       ("expect_text", "Payment of Rs 99 received", None)] if FAKE_STRIPE else
      [("expect_text", "Payment of Rs 99 recorded", None)]),
    ("expect_text", "expert marketplace", None),
    ("expect_text", "Expert review** from Test CA, CA (verified", None, {"done"}),
    ("send_image", "screenshot.png"),
    ("expect_text", "I read this from your image", None),
    ("expect_card", "custom", None),
]
step = {"i": 0}
done = asyncio.Event()


async def advance(ctx: Context):
    while step["i"] < len(script) and script[step["i"]][0] in ("send", "send_image"):
        kind, payload = script[step["i"]][:2]
        ctx.logger.info(f"USER >> {payload[:60]}...")
        if kind == "send_image":
            await ctx.send(COORD, ChatMessage(
                timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                content=[ResourceContent(type="resource", resource_id=uuid4(), resource=Resource(
                    uri="agent-storage://test/screenshot.png",
                    metadata={"mime_type": "image/png", "role": "user-upload"}))]))
        else:
            await ctx.send(COORD, text_message(payload))
        step["i"] += 1
    if step["i"] >= len(script):
        done.set()


@user.on_event("startup")
async def start(ctx: Context):
    await asyncio.sleep(2)
    await ctx.send(human_ca.address, text_message("login 4321"))
    await advance(ctx)


@user.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@user.on_message(ChatMessage)
async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent))
    if sender == human_ca.address:
        # The test user is also the CA, reading jobs on chat and answering them.
        print("\n" + "-" * 70 + f"\nCA's AGENT << {text[:300]}")
        m = re.search(r"case `([0-9a-f]+)`", text)
        if "New paid review" in text and m:
            await ctx.send(human_ca.address, text_message(
                f"review {m.group(1)}: Ready to send. Attach the last two paid bills and "
                "ask for a meter test in writing."))
        return
    if not text and not msg.content:
        return
    card = next((c.metadata for c in msg.content if isinstance(c, MetadataContent)), None)
    kind = card["card_kind"] if card else None
    log.append({"text": text, "card_kind": kind,
                "card": json.loads(card["card_payload"]) if card else None})
    print("\n" + "=" * 70 + f"\nKAGAZ << [{kind or 'text'}]\n{text}")
    if card:
        print("CARD:", json.dumps(json.loads(card["card_payload"]), ensure_ascii=False)[:600])

    if step["i"] >= len(script):
        return
    expected = script[step["i"]]
    ok = (expected[0] == "expect_card" and kind == expected[1]) or \
         (expected[0] == "expect_text" and expected[1] in text)
    if ok and len(expected) > 3:
        payload = json.loads(card["card_payload"]) if card else {}
        actions = {c["selection"]["action"] for c in payload.get("ctas", [])}
        ok = actions == expected[3]
        if not ok:
            print(f"!! step {step['i']} buttons {actions}, expected {expected[3]}")
    if not ok:
        print(f"!! step {step['i']} expected {expected[:2]}, got kind={kind}")
        return
    step["i"] += 1
    click = expected[2]
    if click:
        ctx.logger.info(f"USER clicks {click}")
        await ctx.send(COORD, text_message(json.dumps(click)))
    await advance(ctx)


# Scenario E: each specialist used on its own, like @mentioning it in ASI:One.
ELEC = SAMPLES["04_electricity_disconnection.txt"]
SOLO_CHECKS = [
    ("verify", SAMPLES["01_fake_echallan_sms.txt"], ["Likely SCAM", "cybercrime.gov.in"]),
    ("verify", None, ["I read this from your image", "Likely SCAM"]),
    ("reader", ELEC, ["Here is what this notice says", "₹6,240"]),
    ("rules", ELEC, ["Electricity disconnection notice", "Your rights", "What to do"]),
    ("rules", ELEC + "\nin Hindi", ["आपके अधिकार"]),
    ("action", ELEC, ["Action plan", "Your draft reply", "reminder dates"]),
    ("expert", ELEC, ["Expert review", "Draft reply I prepared", "Review"]),
    ("marketplace", "hi", ["Kagaz Expert Marketplace", "Approved experts", "EXPERTS.md"]),
    ("marketplace", ELEC, ["Best expert for this Electricity disconnection notice", "Test CA"]),
    # Shortcuts through @kagaz: skip the cards and get one agent's answer.
    ("coordinator", "I know it's a scam, just tell me the rules in Hindi: "
     + SAMPLES["01_fake_echallan_sms.txt"], ["Quick check", "scam", "आपके अधिकार"]),
    ("coordinator", "draft in English: " + ELEC, ["Quick check", "What to do", "Your draft reply"]),
    # Casual chat: a greeting, and a general question (offline: falls back to the welcome).
    ("coordinator", "hi", ["Hi! 👋", "Paste the notice"]),
    ("expert", "hello", ["Hi! 👋"]),
    ("expert", "Can my landlord cut my electricity without notice?", ["Expert Desk"]),
]
solo_user = Agent(name="solo_user", seed="kagaz-e2e-solo-user", port=8196,
                  endpoint=["http://127.0.0.1:8196/submit"])
solo_step = {"i": 0, "failed": ""}
solo_done = asyncio.Event()


async def solo_send(ctx: Context):
    if solo_step["i"] >= len(SOLO_CHECKS):
        solo_done.set()
        return
    role, text, _ = SOLO_CHECKS[solo_step["i"]]
    to = config.address_for(role)
    if text is None:
        await ctx.send(to, ChatMessage(
            timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
            content=[ResourceContent(type="resource", resource_id=uuid4(), resource=Resource(
                uri="agent-storage://test/screenshot.png",
                metadata={"mime_type": "image/png", "role": "user-upload"}))]))
    else:
        await ctx.send(to, text_message(text))


@solo_user.on_event("startup")
async def solo_start(ctx: Context):
    await asyncio.sleep(4)  # let the human CA register with the marketplace first
    await solo_send(ctx)


@solo_user.on_message(ChatAcknowledgement)
async def solo_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@solo_user.on_message(ChatMessage)
async def solo_reply(ctx: Context, sender: str, msg: ChatMessage):
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent))
    if not text or solo_step["i"] >= len(SOLO_CHECKS):
        return
    role, _, wants = SOLO_CHECKS[solo_step["i"]]
    if sender != config.address_for(role):
        return
    print("\n" + "~" * 70 + f"\nSOLO {role.upper()} << {text[:500]}")
    missing = [w for w in wants if w not in text]
    if missing:
        solo_step["failed"] = f"{role} reply is missing {missing}"
        solo_done.set()
        return
    solo_step["i"] += 1
    await solo_send(ctx)


async def main():
    agents = build_all() + [human_ca, shield, user, solo_user]
    bureau = Bureau(agents=agents, port=8198)
    task = asyncio.create_task(bureau.run_async())
    try:
        timeout = float(os.getenv("E2E_TIMEOUT", "180"))
        await asyncio.wait_for(asyncio.gather(done.wait(), solo_done.wait()), timeout=timeout)
        if solo_step["failed"]:
            print(f"\n❌ E2E FAILED (solo agents): {solo_step['failed']}")
            code = 1
        elif shield_result.get("verdict") != "SCAM":
            print(f"\n❌ E2E FAILED: outside agent got {shield_result or 'no reply'} from Verify")
            code = 1
        else:
            print(f"\n✅ E2E PASSED: all {len(script)} steps completed, plus Verify reuse "
                  f"and {len(SOLO_CHECKS)} direct chats with single agents")
            code = 0
    except asyncio.TimeoutError:
        if step["i"] < len(script):
            print(f"\n❌ E2E FAILED at step {step['i']}: {script[step['i']][:2]}")
        else:
            print(f"\n❌ E2E FAILED (solo agents) at check {solo_step['i']}: "
                  f"{SOLO_CHECKS[solo_step['i']][0]} did not reply")
        code = 1
    task.cancel()
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
