"""Expert Agent: paid reviews of draft replies, settled with the Agent Payment Protocol.

One file, two kinds of expert:

* Built-in desk (default): Kagaz's own AI-assisted Expert Desk, the fallback
  that always takes a job when no human expert is free.
* Your own agent (KAGAZ_EXPERT_MODE=human): a CA or lawyer runs this file
  with their own seed and credentials. It registers with the Kagaz
  Marketplace, takes paid jobs, and forwards each one to the expert on
  ASI:One chat; the expert replies with the review. See docs/EXPERTS.md.

Flow per job:
  Marketplace --ExpertReviewRequest--> Expert
  Expert      --RequestPayment-------> Marketplace   (invoice for the agreed fee)
  Marketplace --CommitPayment--------> Expert        (payout + transaction id)
  Expert      --CompletePayment------> Marketplace   (receipt)
  Expert      --ExpertReviewResult---> Marketplace   (the review)
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents import solo
from agents.common import make_agent
from kagaz import config, llm, security
from kagaz.cards import text_message
from kagaz.core import action, expert, knowledge, rules, talk
from kagaz.core.reader import NoticeFields
from kagaz.messages import (
    ExpertReviewRequest, ExpertReviewResult, RegisterExpert, RegisterExpertAck,
)

DESK_WELCOME = (
    "🧑‍⚖️ I'm the **Kagaz Expert Desk**. Paste an official notice and I'll draft a reply "
    "and review it like a senior CA/lawyer would: verdict, improvements, documents to "
    "attach and risks. Already have a reply? Paste the notice, then a line `Draft:` "
    "followed by your reply, and I'll review yours."
)

REAL_FET = os.getenv("KAGAZ_REAL_FET", "false").lower() == "true"


def profile_from_env() -> dict:
    mode = os.getenv("KAGAZ_EXPERT_MODE", "ai").lower()
    if mode != "human":
        return {"mode": "ai", "name": "Kagaz Expert Desk", "profession": "AI desk",
                "credential_id": "KAGAZ-AI", "specialties": ["all"],
                "fee": config.EXPERT_FEE_AMOUNT, "languages": ["en", "hi"], "ai_assisted": True}
    return {
        "mode": "human",
        "name": os.getenv("KAGAZ_EXPERT_NAME", ""),
        "profession": os.getenv("KAGAZ_EXPERT_PROFESSION", "CA"),
        "credential_id": os.getenv("KAGAZ_EXPERT_CREDENTIAL", ""),
        "specialties": [s.strip() for s in os.getenv("KAGAZ_EXPERT_SPECIALTIES", "all").split(",")
                        if s.strip()],
        "fee": os.getenv("KAGAZ_EXPERT_FEE_FET", config.EXPERT_FEE_AMOUNT),
        "languages": [s.strip() for s in os.getenv("KAGAZ_EXPERT_LANGUAGES", "en").split(",")],
        "ai_assisted": False,
        "pin": os.getenv("KAGAZ_EXPERT_PIN", ""),
    }


def build(mailbox: bool | None = None, profile: dict | None = None):
    profile = profile or profile_from_env()
    human = profile["mode"] == "human"
    name = None
    if human:
        slug = re.sub(r"[^a-z0-9]+", "_", profile["name"].lower()).strip("_") or "expert"
        name = os.getenv("KAGAZ_EXPERT_AGENT_NAME", f"kagaz_expert_{slug}")
    agent = make_agent(
        "expert",
        (f"{profile['name']} ({profile['profession']}) reviews replies to Indian official "
         "notices for Kagaz AI, paid per job through the Agent Payment Protocol.")
        if human else
        "Paid expert review of replies to Indian official notices. Accepts payment "
        "through the Agent Payment Protocol.",
        mailbox,
        name=name,
        seed=profile.get("seed"),
    )
    review_proto = Protocol(name="KagazExpertReview", version="1.0.0")
    payment = Protocol(spec=payment_protocol_spec, role="seller")
    chat = Protocol(spec=chat_protocol_spec)
    limiter = security.RateLimiter()
    pins = security.PinGuard()

    # -------------------------------------------------- registration

    @agent.on_event("startup")
    async def register(ctx: Context):
        await asyncio.sleep(1)
        await ctx.send(config.address_for("marketplace"), RegisterExpert(
            name=profile["name"], profession=profile["profession"],
            credential_id=profile["credential_id"], specialties=profile["specialties"],
            fee=str(profile["fee"]), languages=profile["languages"],
            ai_assisted=profile["ai_assisted"]))

    @review_proto.on_message(RegisterExpertAck)
    async def on_ack(ctx: Context, sender: str, msg: RegisterExpertAck):
        ctx.storage.set("registration", {"status": msg.status, "reason": msg.reason})
        ctx.logger.info(f"Marketplace registration: {msg.status}. {msg.reason}")
        owner = ctx.storage.get("owner")
        if owner:
            await ctx.send(owner, text_message(f"Kagaz registration: **{msg.status}**. {msg.reason}"))

    # -------------------------------------------------- jobs + payment

    @review_proto.on_message(ExpertReviewRequest)
    async def on_review_request(ctx: Context, sender: str, msg: ExpertReviewRequest):
        if sender != config.address_for("marketplace"):
            ctx.logger.warning("Ignoring a job that did not come from the Kagaz marketplace")
            return
        ctx.storage.set(f"job:{msg.case_id}", {
            "client": sender, "fields_json": msg.fields_json, "draft": msg.draft_text,
            "paid": False,
        })
        recipient = str(agent.wallet.address()) if REAL_FET else agent.address
        await ctx.send(sender, RequestPayment(
            accepted_funds=[Funds(amount=str(profile["fee"]),
                                  currency=config.EXPERT_FEE_CURRENCY,
                                  payment_method="fet_direct")],
            recipient=recipient,
            deadline_seconds=300,
            reference=msg.case_id,
            description=f"Review by {profile['name']}",
            metadata={},
        ))
        ctx.logger.info(f"Invoice sent for case {msg.case_id}")

    async def _payment_ok(ctx: Context, msg: CommitPayment) -> bool:
        if not msg.transaction_id:
            return False
        if float(msg.funds.amount) < float(profile["fee"]) - 1e-9 \
                or msg.funds.currency != config.EXPERT_FEE_CURRENCY:
            return False
        if not REAL_FET:
            return True  # prototype: trust demo transaction ids
        try:
            tx = await asyncio.to_thread(ctx.ledger.query_tx, msg.transaction_id)
            return tx.is_successful()
        except Exception as err:
            ctx.logger.warning(f"Could not verify transaction: {err}")
            return False

    @payment.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
        case_id = msg.reference or ""
        job = ctx.storage.get(f"job:{case_id}")
        if not job or job["client"] != sender:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                                 reason="Unknown review job"))
            return
        if not await _payment_ok(ctx, msg):
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                                 reason="Payment could not be verified"))
            return
        job["paid"] = True
        ctx.storage.set(f"job:{case_id}", job)
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))

        if human:
            await notify_owner(ctx, case_id, job)
            return
        fields = NoticeFields.from_dict(json.loads(job["fields_json"]))
        try:
            text = await asyncio.to_thread(expert.review, fields, job["draft"])
            await ctx.send(sender, ExpertReviewResult(case_id=case_id, ok=True,
                                                      review_text=text, reviewer=expert.REVIEWER))
        except Exception as err:
            ctx.logger.exception("Review failed")
            await ctx.send(sender, ExpertReviewResult(case_id=case_id, ok=False,
                                                      review_text="", error=str(err)))

    @payment.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
        ctx.logger.info(f"Payment rejected: {msg.reason}")

    # -------------------------------------------------- human expert on ASI:One

    async def notify_owner(ctx: Context, case_id: str, job: dict):
        pending = ctx.storage.get("pending") or []
        ctx.storage.set("pending", pending + [case_id])
        owner = ctx.storage.get("owner")
        if not owner:
            ctx.logger.warning(f"Paid job {case_id} waiting: no expert logged in on chat yet")
            return
        fields = json.loads(job["fields_json"])
        kb = knowledge.get(fields.get("notice_type", "unknown"))
        await ctx.send(owner, text_message(
            f"💼 **New paid review** (case `{case_id}`, {profile['fee']} "
            f"{config.EXPERT_FEE_CURRENCY} received)\n\n**Notice:** {kb['title']}\n"
            f"```\n{fields.get('raw_text', '')[:1500]}\n```\n**Client's draft reply:**\n"
            f"```\n{job['draft'][:1500]}\n```\nReply with: `review {case_id}: <your review>`"))

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc),
                                                   acknowledged_msg_id=msg.msg_id))
        if not limiter.allow(sender):
            if limiter.should_warn(sender):
                await ctx.send(sender, text_message(security.SLOW_DOWN))
            return
        text = security.clip(solo.chat_text(msg))
        if not human:
            # Used on its own from ASI:One: review the person's notice and draft.
            await ctx.send(sender, text_message(await desk_review(text)))
            return
        owner = ctx.storage.get("owner")
        m = re.match(r"login\s+(\S+)", text, re.IGNORECASE)
        if m:
            if pins.locked(sender):
                await ctx.send(sender, text_message("Too many wrong PINs. Try again in 15 minutes."))
                return
            if pins.check(sender, m.group(1), profile.get("pin", "")):
                ctx.storage.set("owner", sender)
                reg = ctx.storage.get("registration") or {}
                await ctx.send(sender, text_message(
                    f"✅ Logged in as {profile['name']}. Marketplace status: "
                    f"**{reg.get('status', 'waiting')}**. New paid jobs will appear here."))
                for case_id in ctx.storage.get("pending") or []:
                    job = ctx.storage.get(f"job:{case_id}")
                    if job:
                        await notify_owner_again(ctx, sender, case_id, job)
            else:
                await ctx.send(sender, text_message("Wrong PIN."))
            return
        if sender != owner:
            await ctx.send(sender, text_message(
                f"This is {profile['name']}'s expert agent for Kagaz AI. "
                "To get a notice reviewed, talk to @kagaz."))
            return
        m = re.match(r"review\s+([0-9a-f]{6,})\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if m:
            case_id, review = m.group(1), m.group(2).strip()
            job = ctx.storage.get(f"job:{case_id}")
            if not job or not job.get("paid"):
                await ctx.send(sender, text_message(f"No paid job `{case_id}`."))
                return
            await ctx.send(job["client"], ExpertReviewResult(
                case_id=case_id, ok=True, review_text=review,
                reviewer=f"{profile['name']}, {profile['profession']}"))
            ctx.storage.set("pending", [c for c in ctx.storage.get("pending") or [] if c != case_id])
            await ctx.send(sender, text_message(f"📨 Review for `{case_id}` sent to the client."))
            return
        pending = ctx.storage.get("pending") or []
        await ctx.send(sender, text_message(
            f"Pending jobs: {', '.join(f'`{c}`' for c in pending) or 'none'}.\n"
            "Answer one with `review <case_id>: <your review>`."))

    async def desk_review(text: str) -> str:
        language = "hi" if re.search(r"[ऀ-ॿ]", text) else "en"
        if talk.is_greeting(text):
            return talk.greet("expert", DESK_WELCOME, language)
        if talk.is_question(text):
            reply = await asyncio.to_thread(talk.answer, text, "expert", language)
            return (reply + solo.FOOTER) if reply else DESK_WELCOME
        if len(text) < 40:
            return DESK_WELCOME
        parts = re.split(r"\bdraft\s*:", text, maxsplit=1, flags=re.IGNORECASE)
        notice = parts[0].strip()
        draft = parts[1].strip() if len(parts) > 1 else ""
        fields = solo.read_fields(notice)
        warn = await asyncio.to_thread(solo.scam_warning, fields)
        made = ""
        if not draft:
            # No draft given: have the Action logic write one, then review it.
            token = llm.OFFLINE.set(True)
            try:
                exp = rules.explain(fields, "en")
            finally:
                llm.OFFLINE.reset(token)
            draft = (await asyncio.to_thread(action.build_plan, fields, exp, ""))["draft"]
            made = ("**Draft reply I prepared** (fill in the [brackets]):\n```\n"
                    + draft.strip() + "\n```\n\n")
        review = await asyncio.to_thread(expert.review, fields, draft)
        return (warn + f"🧑‍⚖️ **Expert review: {fields.title}**\n\n" + made
                + "**Review**\n" + review
                + f"\n\n_{expert.REVIEWER}. For a review by a verified human CA or lawyer "
                f"(Rs {config.EXPERT_FEE_INR}, paid through the Payment Protocol), use @kagaz. "
                "Not legal advice._" + solo.FOOTER)

    async def notify_owner_again(ctx: Context, owner: str, case_id: str, job: dict):
        await ctx.send(owner, text_message(
            f"⏳ Waiting for your review: case `{case_id}`. "
            f"Reply with `review {case_id}: <your review>`."))

    @chat.on_message(ChatAcknowledgement)
    async def on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    agent.include(review_proto, publish_manifest=True)
    agent.include(payment, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
