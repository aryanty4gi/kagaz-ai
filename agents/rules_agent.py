"""Rules Agent: explains a notice in plain Hindi or English from grounded rules."""

import asyncio
import json

from uagents import Context, Protocol

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

    agent.include(proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
