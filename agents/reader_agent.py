"""Reader Agent: extracts structured fields from raw notice text."""

import asyncio
import json

from uagents import Context, Protocol

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

    agent.include(proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
