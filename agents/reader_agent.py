"""Reader Agent: extracts structured fields from raw notice text.

Serves the coordinator, and also works alone on ASI:One: paste a notice or
upload a photo and it lists the type, sender, amount, deadline, reference
numbers, links and phone numbers.
"""

import asyncio
import json

from uagents import Context, Protocol

from agents import solo
from agents.common import make_agent
from kagaz.core import reader
from kagaz.messages import ReadRequest, ReadResult


def build(mailbox: bool | None = None):
    agent = make_agent(
        "reader",
        "Reads Indian official notices and SMS and extracts type, issuer, amount, "
        "deadline, reference numbers and links.",
        mailbox,
    )
    proto = Protocol(name="KagazReader", version="1.0.0")

    @proto.on_message(ReadRequest, replies=ReadResult)
    async def on_read(ctx: Context, sender: str, msg: ReadRequest):
        try:
            fields = await asyncio.to_thread(reader.extract, msg.notice_text)
            await ctx.send(sender, ReadResult(ok=True, fields_json=json.dumps(fields.to_dict())))
        except Exception as err:
            ctx.logger.exception("Reader failed")
            await ctx.send(sender, ReadResult(ok=False, fields_json="{}", error=str(err)))

    async def on_chat(ctx: Context, sender: str, text: str, language: str) -> str:
        fields = await asyncio.to_thread(reader.extract, text)
        warn = await asyncio.to_thread(solo.scam_warning, fields)
        return warn + solo.format_fields(fields)

    chat = solo.chat_protocol(
        "reader",
        "📄 I'm **Kagaz Reader**. Paste any official notice or SMS, or upload a photo or "
        "screenshot of it. I'll pull out the type of notice, who sent it, the amount, the "
        "deadline, reference numbers, links and phone numbers.",
        on_chat)

    agent.include(proto, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
