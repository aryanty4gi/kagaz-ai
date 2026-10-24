"""Action Agent: drafts the reply letter, action plan and reminder dates.

Serves the coordinator, and also works alone on ASI:One: paste a notice and
it returns the steps, a ready-to-fill reply letter, official links and
reminder dates.
"""

import asyncio
import json

from uagents import Context, Protocol

from agents import solo
from agents.common import make_agent
from kagaz import llm
from kagaz.core import action, rules
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

    def plan_for(fields: NoticeFields, language: str, user_name: str):
        # Only the steps and links are needed from the rules here, so skip the
        # LLM-written explanation (Hindi still uses it to translate the steps).
        token = llm.OFFLINE.set(language != "hi")
        try:
            explanation = rules.explain(fields, language)
        finally:
            llm.OFFLINE.reset(token)
        return explanation, action.build_plan(fields, explanation, user_name)

    async def on_chat(ctx: Context, sender: str, text: str, language: str) -> str:
        fields = solo.read_fields(text)
        warn = await asyncio.to_thread(solo.scam_warning, fields)
        explanation, plan = await asyncio.to_thread(plan_for, fields, language, "")
        return warn + solo.format_plan(explanation, plan, scam=warn.startswith("🚨"))

    chat = solo.chat_protocol(
        "action",
        "📝 I'm **Kagaz Action**. Paste an official notice (or upload a photo) and I'll give "
        "you the exact steps, a ready-to-fill reply letter, the official links and reminder "
        "dates before the deadline.",
        on_chat)

    agent.include(proto, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
