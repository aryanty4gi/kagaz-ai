"""Kagaz Coordinator: the agent people talk to on ASI:One.

It plans the work and routes it across the specialist agents:
  Reader -> Verify -> (user confirms on a card) -> Rules -> Action
  -> optional reminders -> optional paid Expert review (Payment Protocol)

Reliability: every specialist call has a timeout; if a specialist is
unreachable the coordinator runs the same logic in-process and tells the
user which step ran in fallback mode, so the conversation never dead-ends.
"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timezone
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
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

from agents.common import make_agent
from kagaz import cards, config, llm
from kagaz.core import action, knowledge, reader, rules, verify
from kagaz.core.reader import NoticeFields
from kagaz.messages import (
    ActionRequest, ActionResult, ExpertReviewRequest, ExpertReviewResult,
    ReadRequest, ReadResult, RulesRequest, RulesResult, VerifyRequest, VerifyResult,
)

SPECIALIST_TIMEOUT = int(os.getenv("KAGAZ_SPECIALIST_TIMEOUT", "30"))
REMINDER_CHECK_SECONDS = float(os.getenv("KAGAZ_REMINDER_INTERVAL", "3600"))
REAL_FET = os.getenv("KAGAZ_REAL_FET", "false").lower() == "true"
MAX_EXPERT_FEE = float(os.getenv("KAGAZ_MAX_EXPERT_FEE", "1.0"))

WELCOME = (
    "👋 I'm **Kagaz**. Send me any official notice or message: a traffic "
    "e-challan, an income tax notice, an electricity disconnection notice, a "
    "bank letter, or a suspicious SMS.\n\n"
    "My agent team will:\n"
    "1. **Check if it's real** (scam detection)\n"
    "2. **Explain it** in plain English or Hindi\n"
    "3. **Draft your reply** and the exact steps\n"
    "4. **Remind you** before the deadline, and get an expert review if needed\n\n"
    "Paste the notice text now. Add *\"in Hindi\"* for a Hindi explanation."
)
DISCLAIMER = "_General information, not legal advice. Always confirm on the official portal._"

PROSE_ACTIONS = [
    (("pay rs", "pay and request", "pay_expert", "confirm payment"), "pay_expert"),
    (("expert",), "expert"),
    (("reminder", "remind"), "remind"),
    (("looks right", "proceed", "prepare my reply", "official way", "explain"), "proceed"),
    (("details are wrong", "wrong", "correct the details", "edit"), "edit"),
    (("save and continue", "edit_submit"), "edit_submit"),
    (("not now", "cancel"), "cancel"),
    (("i'm done", "im done", "done", "thank"), "done"),
]
FORM_KEYS = ["notice_type", "amount", "deadline", "reference", "user_name"]


# ---------------------------------------------------------------- parsing

def parse_selection(text: str) -> dict:
    """Card selections arrive as JSON (direct @mention) or prose (planner)."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("action"):
            return data
    except (ValueError, TypeError):
        pass
    # Planner-narrated clicks are short; anything long or with a link is a
    # new notice (a scam SMS saying "refund will be cancelled" is not a click).
    if len(text) > 160 or re.search(r"https?://|www\.|\.[a-z]{2,4}/", text, re.IGNORECASE):
        return {}
    low = text.lower()
    selection: dict = {}
    for phrases, act in PROSE_ACTIONS:
        if any(p in low for p in phrases):
            selection["action"] = act
            break
    # Form fields are only read as "key: value" / "key value" pairs using the
    # exact field names, so "Set deadline reminders" is not a deadline.
    form = {}
    for key in FORM_KEYS:
        m = re.search(rf"\b{key}\b\s*[:=]?\s*([^,;\n]+)", text, re.IGNORECASE)
        if m:
            value = m.group(1).split(". ")[0].strip().strip("'\".")
            if value:
                form[key] = value
    if len(form) >= 2 or (form and selection.get("action") == "edit_submit"):
        selection.update(form)
        selection["action"] = "edit_submit"
    return selection


def detect_language(text: str, current: str = "en") -> str:
    if re.search(r"[ऀ-ॿ]", text) or re.search(r"\bhindi\b", text, re.IGNORECASE):
        return "hi"
    if re.search(r"\benglish\b", text, re.IGNORECASE):
        return "en"
    return current


def strip_language_request(text: str) -> str:
    return re.sub(r"\b(?:explain\s+)?(?:in|into)\s+(?:hindi|english)\b", "", text,
                  flags=re.IGNORECASE).strip()


# ---------------------------------------------------------------- agent

def build(mailbox: bool | None = None):
    agent = make_agent(
        "coordinator",
        "Kagaz AI: send any Indian official notice or SMS (traffic e-challan, income "
        "tax, electricity, bank). A team of agents checks if it is a scam, explains "
        "it in Hindi or English, drafts your reply and reminds you of the deadline.",
        mailbox,
    )
    chat = Protocol(spec=chat_protocol_spec)
    payments = Protocol(spec=payment_protocol_spec, role="buyer")
    expert_proto = Protocol(name="KagazExpertClient", version="1.0.0")

    # -------------------------------------------------- specialist calls

    async def ask(ctx: Context, role: str, request, response_type, fallback):
        """Call a specialist; on failure, compute locally and record it."""
        try:
            resp, status = await ctx.send_and_receive(
                config.address_for(role), request, response_type, timeout=SPECIALIST_TIMEOUT)
        except Exception as err:  # resolver or transport errors
            resp, status = None, err
        if resp is not None and getattr(resp, "ok", False):
            return resp, None
        detail = getattr(resp, "error", "") or getattr(status, "detail", str(status))
        ctx.logger.warning(f"{role} agent unavailable ({detail}); using fallback")

        def run_offline():
            # The specialist was slow, usually because the LLM is. Use the
            # built-in templates instead of waiting on the LLM a second time.
            token = llm.OFFLINE.set(True)
            try:
                return fallback()
            finally:
                llm.OFFLINE.reset(token)

        return await asyncio.to_thread(run_offline), role

    def state_key(sender: str) -> str:
        return f"user:{sender}"

    def load(ctx: Context, sender: str) -> dict:
        return ctx.storage.get(state_key(sender)) or {}

    def save(ctx: Context, sender: str, state: dict):
        ctx.storage.set(state_key(sender), state)

    def fallback_note(degraded: list[str]) -> str:
        if not degraded:
            return ""
        return (f"\n\n_Note: the {', '.join(degraded)} agent(s) didn't respond in time, "
                "so I ran that step myself._")

    # -------------------------------------------------- steps

    async def analyse_notice(ctx: Context, sender: str, text: str, state: dict):
        text = strip_language_request(text)
        degraded = []
        read, failed = await ask(
            ctx, "reader", ReadRequest(notice_text=text), ReadResult,
            lambda: ReadResult(ok=True, fields_json=json.dumps(reader.extract(text).to_dict())))
        if failed:
            degraded.append(failed)
        fields = json.loads(read.fields_json)

        checked, failed = await ask(
            ctx, "verify", VerifyRequest(notice_text=text, fields_json=read.fields_json),
            VerifyResult,
            lambda: VerifyResult(ok=True, report_json=json.dumps(
                verify.verify(NoticeFields.from_dict(fields)).to_dict())))
        if failed:
            degraded.append(failed)
        report = json.loads(checked.report_json)

        state.update(stage="review", fields=fields, report=report,
                     case_id=uuid4().hex[:10], degraded=degraded)
        save(ctx, sender, state)

        if report["verdict"] == "SCAM":
            await ctx.send(sender, cards.card_message(
                "🚨 **Stop. This looks like a scam.** Don't click the link, pay, or share "
                "any OTP." + fallback_note(degraded),
                "custom", cards.scam_alert(fields, report)))
        else:
            intro = {
                "SUSPICIOUS": "⚠️ I found some warning signs, so treat this carefully.",
                "NO_RED_FLAGS": "✅ I didn't find scam signals in this notice.",
            }.get(report["verdict"], "")
            await ctx.send(sender, cards.card_message(
                f"{intro} Please check what I read before I explain it and draft your reply."
                + fallback_note(degraded),
                "review", cards.review_extracted(fields, report)))

    async def explain_and_act(ctx: Context, sender: str, state: dict):
        fields_json = json.dumps(state["fields"])
        language = state.get("language", "en")
        degraded = []
        nf = NoticeFields.from_dict(state["fields"])

        ruled, failed = await ask(
            ctx, "rules", RulesRequest(fields_json=fields_json, language=language), RulesResult,
            lambda: RulesResult(ok=True, explanation_json=json.dumps(
                rules.explain(nf, language), ensure_ascii=False)))
        if failed:
            degraded.append(failed)
        explanation = json.loads(ruled.explanation_json)

        acted, failed = await ask(
            ctx, "action",
            ActionRequest(fields_json=fields_json, explanation_json=ruled.explanation_json,
                          user_name=state.get("user_name", ""), language=language),
            ActionResult,
            lambda: ActionResult(ok=True, plan_json=json.dumps(
                action.build_plan(nf, explanation, state.get("user_name", "")),
                ensure_ascii=False)))
        if failed:
            degraded.append(failed)
        plan = json.loads(acted.plan_json)

        scam = state.get("report", {}).get("verdict") == "SCAM"
        state.update(stage="plan", explanation=explanation, plan=plan)
        save(ctx, sender, state)

        parts = []
        if scam:
            parts.append(f"The message you got is most likely fake. Here is how a genuine "
                         f"**{explanation['title']}** works, so you can check safely:")
        else:
            law = f" ({explanation['law']})" if explanation.get("law") else ""
            parts.append(f"**{explanation['title']}**{law}")
        parts.append(explanation["plain_explanation"])
        parts.append("**What to do**\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)))
        if not scam:
            parts.append("**Your draft reply** (fill in the [brackets]):\n```\n"
                         + plan["draft"].strip() + "\n```")
        if plan.get("portals"):
            parts.append("**Official links:** " + ", ".join(plan["portals"]))
        parts.append(DISCLAIMER + fallback_note(degraded))

        await ctx.send(sender, cards.card_message(
            "\n\n".join(parts), "detail",
            cards.next_steps(explanation, plan, config.EXPERT_FEE_INR, scam)))

    async def set_reminders(ctx: Context, sender: str, state: dict):
        plan = state.get("plan", {})
        dates = plan.get("reminders", [])
        if not dates:
            await ctx.send(sender, cards.text_message(
                "This notice has no clear deadline, so there's nothing to schedule. "
                "Tell me the deadline (DD/MM/YYYY) and I'll set reminders."))
            return
        reminders = ctx.storage.get("reminders") or []
        title = state.get("explanation", {}).get("title", "your notice")
        for d in dates:
            reminders.append({"to": sender, "date": d, "title": title,
                              "deadline": plan.get("deadline", ""), "sent": False})
        ctx.storage.set("reminders", reminders)
        await ctx.send(sender, cards.text_message(
            f"⏰ Done. I'll message you here on {', '.join(dates)} about **{title}** "
            f"(deadline {plan.get('deadline')})."))

    async def offer_expert(ctx: Context, sender: str, state: dict):
        state["stage"] = "confirm_payment"
        save(ctx, sender, state)
        await ctx.send(sender, cards.card_message(
            "An expert can check your draft before you send it.",
            "review",
            cards.confirm_payment(state.get("fields", {}), config.EXPERT_FEE_INR,
                                  config.EXPERT_FEE_AMOUNT, config.EXPERT_FEE_CURRENCY)))

    async def request_expert(ctx: Context, sender: str, state: dict):
        case_id = state.get("case_id") or uuid4().hex[:10]
        ctx.storage.set(f"case:{case_id}", {"user": sender, "paid": False})
        draft = state.get("plan", {}).get("draft", "")
        status = await ctx.send(config.address_for("expert"), ExpertReviewRequest(
            case_id=case_id, fields_json=json.dumps(state.get("fields", {})), draft_text=draft))
        if getattr(status, "status", None) and str(status.status).lower().endswith("failed"):
            await ctx.send(sender, cards.text_message(
                "The Expert Desk is offline right now, so you have **not** been charged. "
                "Please try again in a few minutes."))
            return
        state["stage"] = "awaiting_expert"
        save(ctx, sender, state)
        await ctx.send(sender, cards.text_message(
            f"💳 Sent to the Expert Desk (case `{case_id}`). My agent is settling the fee "
            "with the Expert agent through the Payment Protocol; the review will appear "
            "here in a moment."))

    # -------------------------------------------------- chat protocol

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc),
                                                   acknowledged_msg_id=msg.msg_id))
        text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
        state = load(ctx, sender)
        state["language"] = detect_language(text, state.get("language", "en"))
        save(ctx, sender, state)

        selection = parse_selection(text) if state.get("stage") else {}
        act = selection.get("action")
        try:
            if act == "proceed" and state.get("fields"):
                await explain_and_act(ctx, sender, state)
            elif act == "edit" and state.get("fields"):
                await ctx.send(sender, cards.card_message(
                    "Tell me the correct details.", "form", cards.edit_form(state["fields"])))
            elif act == "edit_submit" and state.get("fields"):
                apply_edits(state, selection)
                save(ctx, sender, state)
                await explain_and_act(ctx, sender, state)
            elif act == "remind" and state.get("plan"):
                await set_reminders(ctx, sender, state)
            elif act == "expert" and state.get("fields"):
                await offer_expert(ctx, sender, state)
            elif act == "pay_expert" and state.get("stage") == "confirm_payment":
                await request_expert(ctx, sender, state)
            elif act in {"cancel", "done"} and state.get("stage"):
                save(ctx, sender, {"language": state.get("language", "en")})
                await ctx.send(sender, cards.text_message(
                    "All set. Send me another notice anytime. 🙏", end_session=True))
            elif len(text) >= 40:
                await analyse_notice(ctx, sender, text, state)
            elif state.get("plan") and state["language"] != "en" and re.search(r"hindi", text, re.I):
                await explain_and_act(ctx, sender, state)
            else:
                await ctx.send(sender, cards.text_message(WELCOME))
        except Exception:
            ctx.logger.exception("Coordinator failed")
            await ctx.send(sender, cards.text_message(
                "Sorry, something went wrong on my side. Please paste the notice again."))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    # -------------------------------------------------- payments (buyer)

    @payments.on_message(RequestPayment)
    async def on_invoice(ctx: Context, sender: str, msg: RequestPayment):
        case = ctx.storage.get(f"case:{msg.reference}")
        if sender != config.address_for("expert") or not case:
            await ctx.send(sender, RejectPayment(reason="No matching review request"))
            return
        funds = next((f for f in msg.accepted_funds
                      if f.currency == config.EXPERT_FEE_CURRENCY), None)
        if not funds or float(funds.amount) > MAX_EXPERT_FEE:
            await ctx.send(sender, RejectPayment(reason="Fee above agreed budget"))
            await ctx.send(case["user"], cards.text_message(
                "The expert's fee didn't match the agreed price, so I declined it. "
                "You have not been charged."))
            return
        tx_id = f"demo-{uuid4().hex[:12]}"
        if REAL_FET:
            try:
                amount_atto = int(float(funds.amount) * 10**18)
                tx = await asyncio.to_thread(
                    lambda: ctx.ledger.send_tokens(msg.recipient, amount_atto, "atestfet",
                                                   agent.wallet).wait_to_complete())
                tx_id = tx.tx_hash
            except Exception as err:
                ctx.logger.warning(f"FET transfer failed: {err}")
                await ctx.send(sender, RejectPayment(reason="Transfer failed"))
                await ctx.send(case["user"], cards.text_message(
                    "The payment didn't go through, so you have not been charged."))
                return
        case["tx"] = tx_id
        ctx.storage.set(f"case:{msg.reference}", case)
        await ctx.send(sender, CommitPayment(
            funds=Funds(amount=funds.amount, currency=funds.currency,
                        payment_method=funds.payment_method),
            recipient=msg.recipient, transaction_id=tx_id, reference=msg.reference,
            description=msg.description, metadata={}))

    @payments.on_message(CompletePayment)
    async def on_paid(ctx: Context, sender: str, msg: CompletePayment):
        ctx.logger.info(f"Expert confirmed payment {msg.transaction_id}")

    @payments.on_message(CancelPayment)
    async def on_cancelled(ctx: Context, sender: str, msg: CancelPayment):
        ctx.logger.warning(f"Expert cancelled payment: {msg.reason}")

    @expert_proto.on_message(ExpertReviewResult)
    async def on_review(ctx: Context, sender: str, msg: ExpertReviewResult):
        case = ctx.storage.get(f"case:{msg.case_id}")
        if not case:
            return
        if not msg.ok:
            await ctx.send(case["user"], cards.text_message(
                "The expert couldn't complete the review. A refund would be issued in production."))
            return
        receipt = (f"Paid {config.EXPERT_FEE_AMOUNT} {config.EXPERT_FEE_CURRENCY} "
                   f"(Rs {config.EXPERT_FEE_INR}) · transaction `{case.get('tx', 'n/a')}`")
        await ctx.send(case["user"], cards.text_message(
            f"🧑‍⚖️ **Expert review** from {msg.reviewer}\n\n{msg.review_text}\n\n{receipt}"))

    # -------------------------------------------------- proactive reminders

    @agent.on_interval(period=REMINDER_CHECK_SECONDS)
    async def send_due_reminders(ctx: Context):
        reminders = ctx.storage.get("reminders") or []
        today = date.today().isoformat()
        changed = False
        for r in reminders:
            if not r["sent"] and r["date"] <= today:
                left = reader.days_left(r["deadline"])
                when = "today" if left == 0 else f"in {left} days"
                await ctx.send(r["to"], cards.text_message(
                    f"⏰ Reminder: the deadline for **{r['title']}** is {when} "
                    f"({r['deadline']}). Reply here if you need help."))
                r["sent"] = True
                changed = True
        if changed:
            ctx.storage.set("reminders", [r for r in reminders if not r["sent"]])

    agent.include(chat, publish_manifest=True)
    agent.include(payments, publish_manifest=True)
    agent.include(expert_proto, publish_manifest=True)
    return agent


def apply_edits(state: dict, selection: dict):
    fields = state["fields"]
    ntype = selection.get("notice_type")
    if ntype in knowledge.NOTICE_TYPES or ntype == "unknown":
        kb = knowledge.get(ntype)
        fields.update(notice_type=ntype, title=kb["title"],
                      issuer=fields.get("issuer") or kb["issuer"])
    if selection.get("amount"):
        digits = re.sub(r"[^\d.]", "", str(selection["amount"]))
        if digits:
            fields["amount"] = f"₹{float(digits):,.0f}"
    if selection.get("deadline"):
        parsed = reader._parse_dates(str(selection["deadline"]))
        if parsed:
            fields["deadline"] = parsed[0][1].isoformat()
            fields["deadline_estimated"] = False
    if selection.get("reference"):
        fields["references"] = [str(selection["reference"])] + fields.get("references", [])[:3]
    if selection.get("user_name"):
        state["user_name"] = str(selection["user_name"])[:80]


if __name__ == "__main__":
    build().run()
