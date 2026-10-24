"""Verify Agent: scam and authenticity checks for notices, SMS and links.

Serves the coordinator over an internal protocol, and is also directly
usable from ASI:One through the Agent Chat Protocol ("is this SMS real?").
"""

import asyncio
import json

from uagents import Context, Protocol

from agents import solo
from agents.common import make_agent
from kagaz.core import reader, verify
from kagaz.core import report as scam_report
from kagaz.core.reader import NoticeFields
from kagaz.messages import VerifyRequest, VerifyResult

VERDICT_TEXT = {
    "SCAM": "🚨 Likely SCAM",
    "SUSPICIOUS": "⚠️ Suspicious",
    "NO_RED_FLAGS": "✅ No scam signals found",
}


def format_report(report: verify.VerifyReport) -> str:
    lines = [f"**{VERDICT_TEXT.get(report.verdict, report.verdict)}** (risk {report.score}/100)", ""]
    reasons = [s for s in report.signals if s["weight"] > 0]
    if reasons:
        lines.append("Why:")
        lines += [f"- {s['label']}" + (f": {s['detail']}" if s["detail"] else "") for s in reasons]
        lines.append("")
    lines.append("What to do:")
    lines += [f"- {a}" for a in report.advice]
    if report.checks_failed:
        lines += ["", f"_Some live checks could not run: {'; '.join(report.checks_failed)}_"]
    return "\n".join(lines)


def build(mailbox: bool | None = None):
    agent = make_agent(
        "verify",
        "Checks whether an Indian notice, SMS or link is genuine or a scam: fake "
        "e-challans, fake income tax refunds, fake electricity disconnection, KYC fraud.",
        mailbox,
    )
    internal = Protocol(name="KagazVerify", version="1.0.0")

    @internal.on_message(VerifyRequest, replies=VerifyResult)
    async def on_verify(ctx: Context, sender: str, msg: VerifyRequest):
        try:
            data = json.loads(msg.fields_json or "{}")
            fields = (NoticeFields.from_dict(data) if data
                      else reader.extract_rule_based(msg.notice_text))
            fields.raw_text = fields.raw_text or msg.notice_text
            report = await asyncio.to_thread(verify.verify, fields)
            await ctx.send(sender, VerifyResult(ok=True, report_json=json.dumps(report.to_dict())))
        except Exception as err:
            ctx.logger.exception("Verify failed")
            await ctx.send(sender, VerifyResult(ok=False, report_json="{}", error=str(err)))

    async def on_chat(ctx: Context, sender: str, text: str, language: str) -> str:
        fields = solo.read_fields(text)
        report = await asyncio.to_thread(verify.verify, fields)
        reply = format_report(report)
        if report.verdict in ("SCAM", "SUSPICIOUS"):
            rep = report.to_dict()
            reply += (
                "\n\n🛡️ **Report it:** file at https://cybercrime.gov.in or call **1930** "
                "(within the first hour if you lost money). Complaint text you can copy:\n"
                "```\n" + scam_report.complaint_text(fields, rep) + "\n```\n"
                "**Warn your family** (forward on WhatsApp):\n"
                "```\n" + scam_report.family_warning(fields, rep) + "\n```")
        return reply

    chat = solo.chat_protocol(
        "verify",
        "🛡️ I'm **Kagaz Verify**. Paste the full SMS, email, WhatsApp message or notice "
        "(including any link), or upload a screenshot, and I'll tell you if it looks "
        "genuine or like a scam.",
        on_chat)

    agent.include(internal, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
