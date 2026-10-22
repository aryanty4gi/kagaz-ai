"""Verify-as-a-service: any agent can reuse Kagaz Verify.

This is a separate, third-party agent (think: a bank's "SMS Shield" bot or a
college helpdesk bot). It does NOT import Kagaz code. It only copies the two
public message models below and sends a VerifyRequest to the Kagaz Verify
agent's address, the same way the Kagaz coordinator does.

    KAGAZ_VERIFY_ADDRESS=agent1q... python examples/verify_client.py "Your SBI KYC ..."
"""

import json
import os
import sys

from uagents import Agent, Context, Model


# Public contract of the Kagaz Verify agent (protocol "KagazVerify" v1.0.0).
# Same names and fields => same schema digest, so the messages match.
class VerifyRequest(Model):
    notice_text: str
    fields_json: str = "{}"


class VerifyResult(Model):
    ok: bool
    report_json: str
    error: str = ""


VERIFY_ADDRESS = os.getenv(
    "KAGAZ_VERIFY_ADDRESS",
    "agent1qdtcq0dwcttl7xnw4rzl0zdjepzlxkc67ts9gzvn3fmxr5fqm3csu52rfqn",
)
MESSAGE = " ".join(sys.argv[1:]) or (
    "Dear customer your SBI account will be blocked today. Update PAN KYC by "
    "calling 9876543210 immediately."
)

shield = Agent(
    name="sms_shield_demo",
    seed=os.getenv("SHIELD_SEED", "sms-shield-demo-seed-change-me"),
    port=int(os.getenv("SHIELD_PORT", "8210")),
    mailbox=os.getenv("SHIELD_MAILBOX", "true").lower() == "true",
)


@shield.on_event("startup")
async def ask_kagaz(ctx: Context):
    ctx.logger.info(f"Asking Kagaz Verify ({VERIFY_ADDRESS[:16]}...) about: {MESSAGE[:60]}")
    await ctx.send(VERIFY_ADDRESS, VerifyRequest(notice_text=MESSAGE))


@shield.on_message(VerifyResult)
async def on_result(ctx: Context, sender: str, msg: VerifyResult):
    if not msg.ok:
        ctx.logger.error(f"Verify failed: {msg.error}")
        return
    report = json.loads(msg.report_json)
    reasons = [s["label"] for s in report["signals"] if s["weight"] > 0]
    ctx.logger.info(f"Kagaz says: {report['verdict']} (risk {report['score']}/100)")
    for r in reasons:
        ctx.logger.info(f"  - {r}")


if __name__ == "__main__":
    shield.run()
