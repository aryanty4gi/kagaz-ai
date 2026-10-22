"""Kagaz Coordinator: the agent people talk to on ASI:One.

It plans the work and routes it across the specialist agents:
  Reader -> Verify -> (user confirms on a card) -> Rules -> Action
  -> optional reminders -> optional paid Expert review (Payment Protocol)

Reliability: every specialist call has a timeout; if a specialist is
unreachable the coordinator runs the same logic in-process and tells the
user which step ran in fallback mode, so the conversation never dead-ends.
"""

import asyncio
import base64
import json
import os
import re
from datetime import date, datetime, timezone
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    ResourceContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity
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
from kagaz import payments as gateway
from kagaz.core import action, knowledge, ocr, reader, report as scam_report, rules, verify
from kagaz.core.reader import NoticeFields
from kagaz.messages import (
    ActionRequest, ActionResult, ExpertJobRequest, ExpertJobResult,
    ReadRequest, ReadResult, RulesRequest, RulesResult, VerifyRequest, VerifyResult,
)

SPECIALIST_TIMEOUT = int(os.getenv("KAGAZ_SPECIALIST_TIMEOUT", "30"))
REMINDER_CHECK_SECONDS = float(os.getenv("KAGAZ_REMINDER_INTERVAL", "3600"))
STORAGE_URL = os.getenv("AGENTVERSE_URL", "https://agentverse.ai").rstrip("/") + "/v1/storage"

WELCOME = (
    "👋 I'm **Kagaz**. Send me any official notice or message: a traffic "
    "e-challan, an income tax notice, an electricity disconnection notice, a "
    "bank letter, or a suspicious SMS.\n\n"
    "My agent team will:\n"
    "1. **Check if it's real** (scam detection)\n"
    "2. **Explain it** in plain English or Hindi\n"
    "3. **Draft your reply** and the exact steps\n"
    "4. **Remind you** before the deadline, and get an expert review if needed\n\n"
    "Paste the notice text, or **upload a screenshot or photo** of it. "
    "Add *\"in Hindi\"* for a Hindi explanation."
)
DISCLAIMER = "_General information, not legal advice. Always confirm on the official portal._"

PROSE_ACTIONS = [
    (("i've paid", "i have paid", "check payment", "check_payment"), "check_payment"),
    (("pay rs", "pay and request", "pay_expert", "confirm payment"), "pay_expert"),
    (("report this scam", "report scam", "scam report", "warn my family", "report_scam"),
     "report_scam"),
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


def download_image(item: ResourceContent, identity: Identity) -> tuple[bytes, str]:
    """Fetch an image the user uploaded in ASI:One (Agentverse storage)."""
    res = item.resource[0] if isinstance(item.resource, list) else item.resource
    mime = (res.metadata or {}).get("mime_type", "")
    try:
        from uagents_core.storage import ExternalStorage

        store = ExternalStorage(identity=identity, storage_url=STORAGE_URL)
        data = store.download(str(item.resource_id))
        return base64.b64decode(data["contents"]), data.get("mime_type") or mime
    except Exception as err:
        if not res.uri.startswith(("https://", "http://")):
            raise RuntimeError(f"storage download failed: {err}") from err
    import requests

    resp = requests.get(res.uri, timeout=15)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", mime)


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
    # Coordinator sells expert reviews to the user (Stripe gateway or demo);
    # the marketplace agent pays the experts.
    payments = Protocol(spec=payment_protocol_spec, role="seller")
    expert_proto = Protocol(name="KagazExpertClient", version="2.0.0")

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
                                  config.EXPERT_FEE_AMOUNT, config.EXPERT_FEE_CURRENCY,
                                  live_gateway=gateway.enabled())))

    async def request_expert(ctx: Context, sender: str, state: dict):
        """User clicked Pay: take payment through the gateway, then hire an expert."""
        case_id = state.get("case_id") or uuid4().hex[:10]
        state["case_id"] = case_id
        title = state.get("fields", {}).get("title") or "Official notice"
        if not gateway.enabled():
            tx = gateway.demo_transaction_id()
            await ctx.send(sender, cards.text_message(
                f"💳 Payment of Rs {config.EXPERT_FEE_INR} recorded (`{tx}`, demo mode: "
                "add Stripe keys for real card/UPI payments)."))
            await hire_expert(ctx, sender, state, {"method": "demo", "tx": tx})
            return
        try:
            checkout = await asyncio.to_thread(
                gateway.create_checkout, case_id, sender, config.EXPERT_FEE_INR,
                f"Expert review: {title}")
        except Exception as err:
            ctx.logger.exception("Stripe checkout failed")
            await ctx.send(sender, cards.text_message(
                f"The payment page couldn't be opened ({type(err).__name__}). You have "
                "**not** been charged. Please try again in a minute."))
            return
        ctx.storage.set(f"pay:{case_id}", {"user": sender, "sessions": checkout["session_ids"],
                                           "paid": False})
        state["stage"] = "awaiting_payment"
        save(ctx, sender, state)
        # Payment Protocol request with a Stripe payload ASI:One can render.
        await ctx.send(sender, RequestPayment(
            accepted_funds=[Funds(currency=gateway.CURRENCY.upper(),
                                  amount=f"{float(config.EXPERT_FEE_INR):.2f}",
                                  payment_method="stripe")],
            recipient=agent.address, deadline_seconds=1800, reference=case_id,
            description=f"Kagaz expert review: {title}",
            metadata={"stripe": json.dumps(checkout["stripe"]), "service": "kagaz_expert_review"}))
        link = (f"\n\nIf the payment box doesn't appear, pay here: {checkout['hosted_url']}"
                if checkout["hosted_url"] else "")
        await ctx.send(sender, cards.card_message(
            f"💳 Pay **Rs {config.EXPERT_FEE_INR}** securely by card or UPI (Stripe)." + link,
            "review", cards.payment_check(case_id, config.EXPERT_FEE_INR)))

    async def confirm_paid(ctx: Context, sender: str, state: dict, case_id: str):
        pay = ctx.storage.get(f"pay:{case_id}")
        if not pay or pay["user"] != sender:
            await ctx.send(sender, cards.text_message("I couldn't find that payment."))
            return
        if pay["paid"]:
            return
        sid, intent = await asyncio.to_thread(gateway.paid_session, pay["sessions"])
        if not sid:
            await ctx.send(sender, cards.card_message(
                "I don't see the payment yet. Finish checkout, then tap the button again.",
                "review", cards.payment_check(case_id, config.EXPERT_FEE_INR)))
            return
        pay.update(paid=True, session=sid, intent=intent)
        ctx.storage.set(f"pay:{case_id}", pay)
        await ctx.send(sender, CompletePayment(transaction_id=sid))
        await ctx.send(sender, cards.text_message(
            f"✅ Payment of Rs {config.EXPERT_FEE_INR} received (Stripe `{sid[-10:]}`)."))
        await hire_expert(ctx, sender, state, {"method": "stripe", "tx": sid, "intent": intent})

    async def hire_expert(ctx: Context, sender: str, state: dict, payment: dict):
        case_id = state["case_id"]
        ctx.storage.set(f"case:{case_id}", {"user": sender, "payment": payment})
        fields = state.get("fields", {})
        status = await ctx.send(config.address_for("marketplace"), ExpertJobRequest(
            case_id=case_id, notice_type=fields.get("notice_type", "unknown"),
            fields_json=json.dumps(fields), draft_text=state.get("plan", {}).get("draft", ""),
            budget=config.EXPERT_FEE_AMOUNT))
        if getattr(status, "status", None) and str(status.status).lower().endswith("failed"):
            await refund_user(ctx, case_id, "The expert marketplace is offline right now.")
            return
        state["stage"] = "awaiting_expert"
        save(ctx, sender, state)
        await ctx.send(sender, cards.text_message(
            f"🧑‍⚖️ Sent to the Kagaz expert marketplace (case `{case_id}`). It picks a "
            "verified CA or lawyer for this notice type and pays them per job through "
            "the Payment Protocol. Your review will appear here."))

    async def refund_user(ctx: Context, case_id: str, why: str):
        case = ctx.storage.get(f"case:{case_id}") or {}
        payment = case.get("payment", {})
        refunded = await asyncio.to_thread(gateway.refund, payment.get("intent", ""))
        note = ("Your payment has been **refunded**." if refunded else
                "You have not been charged." if payment.get("method") == "demo" else
                "A refund will be issued.")
        if case.get("user"):
            await ctx.send(case["user"], cards.text_message(f"{why} {note}"))

    # -------------------------------------------------- images and scam reports

    coordinator_identity = Identity.from_seed(config.seed_for("coordinator"), 0)

    async def analyse_image(ctx: Context, sender: str, item: ResourceContent,
                            user_text: str, state: dict):
        data, mime = await asyncio.to_thread(download_image, item, coordinator_identity)
        mime = (mime or "image/png").split(";")[0].strip().lower()
        if mime not in ocr.IMAGE_TYPES and not mime.startswith("image/"):
            await ctx.send(sender, cards.text_message(
                "I can read **photos and screenshots** (PNG/JPG). For a PDF, please "
                "paste its text or send a screenshot of the page."))
            return
        text, method = await asyncio.to_thread(ocr.image_to_text, data, mime)
        if len(text) < ocr.MIN_USEFUL_CHARS:
            await ctx.send(sender, cards.text_message(
                "I couldn't read enough text in that image. Please send a clearer, "
                "straight photo, or paste the text."))
            return
        preview = text if len(text) <= 400 else text[:400] + " …"
        how = "OCR" if method == "ocr" else "ASI:One vision"
        await ctx.send(sender, cards.text_message(
            f"📷 I read this from your image ({how}):\n> " + preview.replace("\n", "\n> ")
            + "\n\nChecking it now…"))
        await analyse_notice(ctx, sender, text, state)

    async def send_report_pack(ctx: Context, sender: str, state: dict):
        nf = NoticeFields.from_dict(state["fields"])
        rep = state.get("report", {})
        complaint = scam_report.complaint_text(nf, rep)
        warning = scam_report.family_warning(nf, rep)
        await ctx.send(sender, cards.text_message(
            "🛡️ **Your scam report pack**\n\n"
            "**1. File a complaint** at https://cybercrime.gov.in (\"Report other cyber crime\"), "
            "or call **1930** and read this out. If you already lost money, call 1930 "
            "within the first hour; banks can freeze the money.\n"
            "```\n" + complaint + "\n```\n"
            "**2. Warn your family.** Forward this on WhatsApp:\n"
            "```\n" + warning + "\n```\n"
            "**3. Block the sender** and report it as spam. On Sanchar Saathi "
            "(https://sancharsaathi.gov.in, Chakshu) you can report fraud calls and SMS.\n\n"
            + DISCLAIMER))

    # -------------------------------------------------- chat protocol

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc),
                                                   acknowledged_msg_id=msg.msg_id))
        if any(isinstance(c, StartSessionContent) for c in msg.content):
            # Tell ASI:One this agent accepts image uploads.
            await ctx.send(sender, ChatMessage(
                timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                content=[MetadataContent(type="metadata", metadata={"attachments": "true"})]))
        text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
        resources = [c for c in msg.content if isinstance(c, ResourceContent)]
        state = load(ctx, sender)
        state["language"] = detect_language(text, state.get("language", "en"))
        save(ctx, sender, state)

        if resources:
            try:
                await analyse_image(ctx, sender, resources[0], text, state)
            except Exception:
                ctx.logger.exception("Image handling failed")
                await ctx.send(sender, cards.text_message(
                    "Sorry, I couldn't open that image. Please try again, or paste the text."))
            return

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
            elif act == "report_scam" and state.get("fields"):
                await send_report_pack(ctx, sender, state)
            elif act == "pay_expert" and state.get("stage") == "confirm_payment":
                await request_expert(ctx, sender, state)
            elif act == "check_payment" and state.get("stage") == "awaiting_payment":
                await confirm_paid(ctx, sender, state, selection.get("case_id")
                                   or state.get("case_id", ""))
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

    # -------------------------------------------------- payments (seller to the user)

    @payments.on_message(CommitPayment)
    async def on_user_commit(ctx: Context, sender: str, msg: CommitPayment):
        """ASI:One's payment card reports a finished Stripe checkout."""
        case_id = msg.reference or ""
        if msg.funds.payment_method != "stripe" or not msg.transaction_id:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                                 reason="Unsupported payment method"))
            return
        await confirm_paid(ctx, sender, load(ctx, sender), case_id)

    @payments.on_message(RejectPayment)
    async def on_user_reject(ctx: Context, sender: str, msg: RejectPayment):
        state = load(ctx, sender)
        state["stage"] = "plan"
        save(ctx, sender, state)
        await ctx.send(sender, cards.text_message("No problem, nothing was charged."))

    @expert_proto.on_message(ExpertJobResult)
    async def on_review(ctx: Context, sender: str, msg: ExpertJobResult):
        if sender != config.address_for("marketplace"):
            return
        case = ctx.storage.get(f"case:{msg.case_id}")
        if not case:
            return
        if not msg.ok:
            await refund_user(ctx, msg.case_id,
                              f"The expert review couldn't be completed ({msg.error}).")
            return
        pay = case.get("payment", {})
        receipt = (f"You paid Rs {config.EXPERT_FEE_INR} ({pay.get('method', 'demo')} "
                   f"`{str(pay.get('tx', 'n/a'))[-12:]}`) · expert paid {msg.payout or 'n/a'} "
                   f"agent-to-agent (`{msg.tx or 'n/a'}`)")
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
