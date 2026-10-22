"""End-to-end test: a simulated ASI:One user walks the whole Kagaz flow.

    KAGAZ_USE_MAILBOX=false KAGAZ_ONLINE_CHECKS=false python -m tests.e2e_local

Scenario A (scam):    fake e-challan SMS -> scam card -> report pack -> "check officially"
Scenario B (genuine): electricity notice -> review card -> proceed -> remind
                      -> expert offer -> pay -> paid expert review arrives
Scenario C (image):   screenshot of a "digital arrest" SMS -> OCR -> scam card
The simulated user clicks card buttons by replying with each CTA's selection
JSON, exactly as ASI:One does for a direct @mention.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("KAGAZ_USE_MAILBOX", "false")
os.environ.setdefault("KAGAZ_ONLINE_CHECKS", "false")

from uagents import Agent, Bureau, Context  # noqa: E402
from uagents_core.contrib.protocols.chat import (  # noqa: E402
    ChatAcknowledgement, ChatMessage, MetadataContent, Resource, ResourceContent, TextContent,
)

from kagaz import config  # noqa: E402
from kagaz.cards import text_message  # noqa: E402
from run_local import build_all  # noqa: E402
from agents import coordinator_agent  # noqa: E402
from tests.test_core import DIGITAL_ARREST, render_png  # noqa: E402

# Stand-in for Agentverse storage: the "uploaded" screenshot.
SCREENSHOT = render_png(DIGITAL_ARREST, dark=True)
coordinator_agent.download_image = lambda item, identity: (SCREENSHOT, "image/png")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {p.name: p.read_text() for p in (ROOT / "samples").glob("*.txt")}
COORD = config.address_for("coordinator")

log: list[dict] = []

user = Agent(name="test_user", seed="kagaz-e2e-test-user", port=8199,
             endpoint=["http://127.0.0.1:8199/submit"])

# What the user does after each card kind / message, per scenario step.
script = [
    ("send", SAMPLES["01_fake_echallan_sms.txt"]),
    ("expect_card", "custom", {"action": "report_scam"}),
    ("expect_text", "Your scam report pack", {"action": "proceed"}),
    ("expect_card", "detail", None),
    ("send", SAMPLES["04_electricity_disconnection.txt"]),
    ("expect_card", "review", {"action": "proceed"}),
    ("expect_card", "detail", {"action": "remind"}),
    ("expect_text", "Done. I'll message you", {"action": "expert"}),
    ("expect_card", "review", {"action": "pay_expert"}),
    ("expect_text", "Sent to the Expert Desk", None),
    ("expect_text", "Expert review", None),
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
    await advance(ctx)


@user.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@user.on_message(ChatMessage)
async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent))
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
    if not ok:
        print(f"!! step {step['i']} expected {expected[:2]}, got kind={kind}")
        return
    step["i"] += 1
    click = expected[2]
    if click:
        ctx.logger.info(f"USER clicks {click}")
        await ctx.send(COORD, text_message(json.dumps(click)))
    await advance(ctx)


async def main():
    agents = build_all() + [user]
    bureau = Bureau(agents=agents, port=8198)
    task = asyncio.create_task(bureau.run_async())
    try:
        await asyncio.wait_for(done.wait(), timeout=float(os.getenv("E2E_TIMEOUT", "180")))
        print(f"\n✅ E2E PASSED: all {len(script)} steps completed")
        code = 0
    except asyncio.TimeoutError:
        print(f"\n❌ E2E FAILED at step {step['i']}: {script[step['i']][:2]}")
        code = 1
    task.cancel()
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
