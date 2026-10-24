"""Rules Agent: explains a notice in plain Hindi or English from grounded rules.

Serves the coordinator, and also works alone on ASI:One: paste a notice
(add "in Hindi" for Hindi) and it explains what it means, your rights,
the deadline rule and what to do.
"""

import asyncio
import json

from uagents import Context, Protocol

from agents import solo
from agents.common import make_agent
from kagaz.core import rules
from kagaz.core.reader import NoticeFields
from kagaz.messages import RulesRequest, RulesResult


def build(mailbox: bool | None = None):
    agent = make_agent(
        "rules",
        "Explains Indian tax, traffic, electricity and bank notices in plain Hindi "
        "or English, grounded in a curated rules knowledge base.",
        mailbox,
    )
    proto = Protocol(name="KagazRules", version="1.0.0")

    @proto.on_message(RulesRequest, replies=RulesResult)
    async def on_rules(ctx: Context, sender: str, msg: RulesRequest):
        try:
            fields = NoticeFields.from_dict(json.loads(msg.fields_json))
            result = await asyncio.to_thread(rules.explain, fields, msg.language)
            await ctx.send(sender, RulesResult(ok=True, explanation_json=json.dumps(result,
                                                                                    ensure_ascii=False)))
        except Exception as err:
            ctx.logger.exception("Rules failed")
            await ctx.send(sender, RulesResult(ok=False, explanation_json="{}", error=str(err)))

    async def on_chat(ctx: Context, sender: str, text: str, language: str) -> str:
        fields = solo.read_fields(text)
        warn = await asyncio.to_thread(solo.scam_warning, fields)
        explanation = await asyncio.to_thread(rules.explain, fields, language)
        return warn + solo.format_explanation(explanation)

    chat = solo.chat_protocol(
        "rules",
        "📘 I'm **Kagaz Rules**. Paste any Indian official notice (traffic challan, income "
        "tax, GST, electricity, bank, court summons, school fee) or upload a photo. I'll "
        "explain what it means, your rights, the deadline and what to do. Add *\"in Hindi\"* "
        "for Hindi.",
        on_chat)

    agent.include(proto, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
