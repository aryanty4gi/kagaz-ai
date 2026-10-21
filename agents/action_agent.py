"""Action Agent: drafts the reply letter, action plan and reminder dates."""

import asyncio
import json

from uagents import Context, Protocol

from agents.common import make_agent
from kagaz.core import action
from kagaz.core.reader import NoticeFields
from kagaz.messages import ActionRequest, ActionResult


def build(mailbox: bool | None = None):
    agent = make_agent(
        "action",
        "Turns an Indian official notice into a ready-to-send reply letter, a "
        "step-by-step action plan and deadline reminders.",
        mailbox,
    )
    proto = Protocol(name="KagazAction", version="1.0.0")

    @proto.on_message(ActionRequest, replies=ActionResult)
    async def on_action(ctx: Context, sender: str, msg: ActionRequest):
        try:
            fields = NoticeFields.from_dict(json.loads(msg.fields_json))
            explanation = json.loads(msg.explanation_json)
            plan = await asyncio.to_thread(action.build_plan, fields, explanation, msg.user_name)
            await ctx.send(sender, ActionResult(ok=True, plan_json=json.dumps(plan, ensure_ascii=False)))
        except Exception as err:
            ctx.logger.exception("Action failed")
            await ctx.send(sender, ActionResult(ok=False, plan_json="{}", error=str(err)))

    agent.include(proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
