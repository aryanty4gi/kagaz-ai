"""Marketplace Agent: verified CAs and lawyers, paid per job.

Experts run their own agent on Agentverse (agents/expert_agent.py) and
register here with their ICAI membership or Bar Council enrolment number.
The Kagaz team approves them after checking the number on the official
register. For each paid review the marketplace:

  Coordinator --ExpertJobRequest----> Marketplace   (user already paid Kagaz)
  Marketplace --ExpertReviewRequest-> best matching approved expert
  Expert      --RequestPayment------> Marketplace   (invoice, <= agreed fee)
  Marketplace --CommitPayment-------> Expert        (payout, FET)
  Expert      --CompletePayment-----> Marketplace   (receipt)
  Expert      --ExpertReviewResult--> Marketplace   (the review)
  Marketplace --ExpertJobResult-----> Coordinator   (review + payout receipt)

If no human expert takes the job in time, the built-in AI-assisted Expert
Desk does it, so the user is never left waiting.
"""

import asyncio
import os
import re
import time
from uuid import uuid4

from uagents import Context, Protocol
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
from kagaz import config
from kagaz.core import knowledge
from kagaz.messages import (
    ExpertJobRequest, ExpertJobResult, ExpertReviewRequest, ExpertReviewResult,
    RegisterExpert, RegisterExpertAck,
)

REAL_FET = os.getenv("KAGAZ_REAL_FET", "false").lower() == "true"
ACCEPT_SECONDS = float(os.getenv("KAGAZ_EXPERT_ACCEPT_SECONDS", "120"))
CHECK_SECONDS = float(os.getenv("KAGAZ_MARKETPLACE_CHECK_SECONDS", "20"))

# Credential formats. A well-formed number is only the first check; approval
# needs the Kagaz team to confirm it on the ICAI / Bar Council register.
CREDENTIAL_FORMATS = {
    "CA": (re.compile(r"^\d{6}$"), "ICAI membership number (6 digits)"),
    "Lawyer": (re.compile(r"^[A-Z]{1,4}/\d{1,6}/\d{4}$"),
               "Bar Council enrolment number, e.g. D/1234/2015"),
}


def validate(msg: RegisterExpert, sender: str) -> tuple[str, str]:
    """Return (status, reason) for a registration."""
    if msg.ai_assisted or msg.profession == "AI desk":
        if sender == config.address_for("expert"):
            return "approved", "Built-in AI-assisted desk"
        return "rejected", "Only the built-in Kagaz desk may register as AI-assisted"
    fmt = CREDENTIAL_FORMATS.get(msg.profession)
    if not fmt:
        return "rejected", "Profession must be CA or Lawyer"
    pattern, label = fmt
    if not pattern.match(msg.credential_id.strip().upper()):
        return "rejected", f"Expected a valid {label}"
    try:
        if float(msg.fee) <= 0:
            raise ValueError
    except ValueError:
        return "rejected", "Fee must be a positive number"
    if sender in config.VERIFIED_EXPERTS or config.AUTO_APPROVE_EXPERTS:
        return "approved", "Credential verified"
    return "pending", ("Kagaz will check your number on the official register "
                       "and approve you; you'll get jobs after that")


def pick_expert(experts: dict, notice_type: str, budget: float,
                exclude: set[str] | None = None) -> str:
    """Best approved human expert for this notice type within budget, else the AI desk."""
    exclude = exclude or set()
    humans = [
        (addr, e) for addr, e in experts.items()
        if e["status"] == "approved" and not e["ai_assisted"] and addr not in exclude
        and float(e["fee"]) <= budget
        and (notice_type in e["specialties"] or "all" in e["specialties"])
    ]
    if humans:
        humans.sort(key=lambda x: (x[1].get("active", 0), -x[1].get("done", 0)))
        return humans[0][0]
    desk = config.address_for("expert")
    return desk if desk not in exclude else ""


def reviewer_label(e: dict) -> str:
    if e.get("ai_assisted"):
        return "Kagaz Expert Desk (AI-assisted review, prototype)"
    cred = e["credential_id"]
    masked = cred[:2] + "•" * max(0, len(cred) - 4) + cred[-2:]
    return f"{e['name']}, {e['profession']} (verified, {masked})"


def build(mailbox: bool | None = None):
    agent = make_agent(
        "marketplace",
        "Kagaz expert marketplace: verified CAs and lawyers register their own "
        "agents and are paid per review through the Agent Payment Protocol.",
        mailbox,
    )
    market = Protocol(name="KagazMarketplace", version="1.0.0")
    payments = Protocol(spec=payment_protocol_spec, role="buyer")

    def experts(ctx: Context) -> dict:
        return ctx.storage.get("experts") or {}

    def save_experts(ctx: Context, data: dict):
        ctx.storage.set("experts", data)

    # -------------------------------------------------- registration

    @market.on_message(RegisterExpert, replies=RegisterExpertAck)
    async def on_register(ctx: Context, sender: str, msg: RegisterExpert):
        status, reason = validate(msg, sender)
        if status != "rejected":
            data = experts(ctx)
            old = data.get(sender, {})
            data[sender] = {
                "name": msg.name, "profession": msg.profession,
                "credential_id": msg.credential_id.strip().upper(),
                "specialties": msg.specialties or ["all"], "fee": msg.fee,
                "languages": msg.languages, "ai_assisted": msg.ai_assisted,
                "status": status, "active": old.get("active", 0), "done": old.get("done", 0),
            }
            save_experts(ctx, data)
        ctx.logger.info(f"Expert {msg.name} ({msg.profession}) -> {status}: {reason}")
        await ctx.send(sender, RegisterExpertAck(ok=status != "rejected", status=status,
                                                 reason=reason))

    # -------------------------------------------------- jobs

    async def assign(ctx: Context, case_id: str, job: dict) -> bool:
        data = experts(ctx)
        addr = pick_expert(data, job["notice_type"], float(job["budget"]),
                           set(job.get("tried", [])))
        if not addr:
            return False
        job.update(expert=addr, assigned_at=time.time(), invoiced=False)
        job.setdefault("tried", []).append(addr)
        ctx.storage.set(f"job:{case_id}", job)
        if addr in data:
            data[addr]["active"] = data[addr].get("active", 0) + 1
            save_experts(ctx, data)
        await ctx.send(addr, ExpertReviewRequest(case_id=case_id, fields_json=job["fields_json"],
                                                 draft_text=job["draft"]))
        ctx.logger.info(f"Job {case_id} -> {data.get(addr, {}).get('name', addr[:12])}")
        return True

    @market.on_message(ExpertJobRequest)
    async def on_job(ctx: Context, sender: str, msg: ExpertJobRequest):
        if sender != config.address_for("coordinator"):
            ctx.logger.warning(f"Ignoring job from unknown sender {sender[:16]}")
            return
        job = {"client": sender, "notice_type": msg.notice_type, "fields_json": msg.fields_json,
               "draft": msg.draft_text, "budget": msg.budget, "done": False}
        open_jobs = ctx.storage.get("open_jobs") or []
        ctx.storage.set("open_jobs", open_jobs + [msg.case_id])
        if not await assign(ctx, msg.case_id, job):
            await ctx.send(sender, ExpertJobResult(case_id=msg.case_id, ok=False,
                                                   error="No expert available"))

    @agent.on_interval(period=CHECK_SECONDS)
    async def reassign_stale_jobs(ctx: Context):
        """A human expert who doesn't pick up a job in time loses it to the next one."""
        now = time.time()
        still_open = []
        for case_id in ctx.storage.get("open_jobs") or []:
            job = ctx.storage.get(f"job:{case_id}")
            if not job or job.get("done"):
                continue
            still_open.append(case_id)
            if not job.get("invoiced") and now - job.get("assigned_at", now) > ACCEPT_SECONDS:
                ctx.logger.info(f"Job {case_id} not picked up in time; reassigning")
                if not await assign(ctx, case_id, job):
                    job["done"] = True
                    ctx.storage.set(f"job:{case_id}", job)
                    await ctx.send(job["client"], ExpertJobResult(
                        case_id=case_id, ok=False, error="No expert picked up the job"))
        ctx.storage.set("open_jobs", still_open)

    # -------------------------------------------------- payout (buyer)

    @payments.on_message(RequestPayment)
    async def on_invoice(ctx: Context, sender: str, msg: RequestPayment):
        job = ctx.storage.get(f"job:{msg.reference}")
        if not job or job.get("expert") != sender or job.get("done"):
            await ctx.send(sender, RejectPayment(reason="No matching job for you"))
            return
        expert = experts(ctx).get(sender, {})
        agreed = min(float(job["budget"]), float(expert.get("fee", job["budget"])))
        funds = next((f for f in msg.accepted_funds
                      if f.currency == config.EXPERT_FEE_CURRENCY), None)
        if not funds or float(funds.amount) > agreed + 1e-9:
            await ctx.send(sender, RejectPayment(reason=f"Invoice above the agreed {agreed}"))
            return
        tx_id = f"demo-{uuid4().hex[:12]}"
        if REAL_FET:
            try:
                atto = int(float(funds.amount) * 10**18)
                tx = await asyncio.to_thread(
                    lambda: ctx.ledger.send_tokens(msg.recipient, atto, "atestfet",
                                                   agent.wallet).wait_to_complete())
                tx_id = tx.tx_hash
            except Exception as err:
                ctx.logger.warning(f"FET payout failed: {err}")
                await ctx.send(sender, RejectPayment(reason="Transfer failed"))
                return
        job.update(invoiced=True, tx=tx_id, payout=f"{funds.amount} {funds.currency}")
        ctx.storage.set(f"job:{msg.reference}", job)
        await ctx.send(sender, CommitPayment(
            funds=Funds(amount=funds.amount, currency=funds.currency,
                        payment_method=funds.payment_method),
            recipient=msg.recipient, transaction_id=tx_id, reference=msg.reference,
            description=msg.description, metadata={}))

    @payments.on_message(CompletePayment)
    async def on_receipt(ctx: Context, sender: str, msg: CompletePayment):
        ctx.logger.info(f"Expert confirmed payout {msg.transaction_id}")

    @payments.on_message(CancelPayment)
    async def on_cancel(ctx: Context, sender: str, msg: CancelPayment):
        ctx.logger.warning(f"Expert cancelled payout: {msg.reason}")

    @market.on_message(ExpertReviewResult)
    async def on_review(ctx: Context, sender: str, msg: ExpertReviewResult):
        job = ctx.storage.get(f"job:{msg.case_id}")
        if not job or job.get("expert") != sender or job.get("done"):
            return
        job["done"] = True
        ctx.storage.set(f"job:{msg.case_id}", job)
        data = experts(ctx)
        e = data.get(sender, {"ai_assisted": True})
        if sender in data:
            data[sender]["active"] = max(0, data[sender].get("active", 1) - 1)
            data[sender]["done"] = data[sender].get("done", 0) + (1 if msg.ok else 0)
            save_experts(ctx, data)
        await ctx.send(job["client"], ExpertJobResult(
            case_id=msg.case_id, ok=msg.ok, review_text=msg.review_text,
            reviewer=reviewer_label(e) if sender in data else msg.reviewer,
            payout=job.get("payout", ""), tx=job.get("tx", ""), error=msg.error))

    # -------------------------------------------------- chat (used on its own)

    def expert_list(ctx: Context) -> str:
        data = experts(ctx)
        rows = []
        for e in data.values():
            if e["status"] != "approved":
                continue
            spec = ", ".join(knowledge.get(t)["title"] if t != "all" else "all notices"
                             for t in e["specialties"])
            rows.append(f"- **{reviewer_label(e)}**: {spec}; fee {e['fee']} "
                        f"{config.EXPERT_FEE_CURRENCY}; {e.get('done', 0)} jobs done")
        pending = sum(1 for e in data.values() if e["status"] == "pending")
        text = "**Approved experts right now**\n" + ("\n".join(rows) or
                                                    "- Kagaz Expert Desk (AI-assisted, always on)")
        if pending:
            text += f"\n\n_{pending} more waiting for credential checks._"
        return text

    async def on_chat(ctx: Context, sender: str, text: str, language: str) -> str:
        low = text.lower()
        how = (
            "**How it works**\n"
            f"1. You pay Rs {config.EXPERT_FEE_INR} for a review (through @kagaz, card/UPI).\n"
            "2. I pick the best approved CA or lawyer for that notice type, within budget.\n"
            f"3. The expert's own agent is paid up to {config.EXPERT_FEE_AMOUNT} "
            f"{config.EXPERT_FEE_CURRENCY} per job with the Agent Payment Protocol "
            f"(Kagaz keeps {config.PLATFORM_FEE_PERCENT:g}%).\n"
            "4. If no human takes the job in time, the AI-assisted Expert Desk does it.")
        join = ("**Are you a CA or lawyer?** Run your own Kagaz expert agent and register "
                "with your ICAI membership number or Bar Council enrolment number: "
                "https://github.com/aryanty4gi/kagaz-ai/blob/main/docs/EXPERTS.md")
        if len(text) >= 40 and not any(w in low for w in ("join", "register", "list")):
            fields = solo.read_fields(text)
            data = experts(ctx)
            addr = pick_expert(data, fields.notice_type, float(config.EXPERT_FEE_AMOUNT))
            who = (reviewer_label(data[addr]) if addr in data
                   else "Kagaz Expert Desk (AI-assisted review, prototype)")
            return (f"🧑‍⚖️ **Best expert for this {fields.title}:** {who}\n\n"
                    f"Fee: Rs {config.EXPERT_FEE_INR}. To book the review, send the notice to "
                    "@kagaz and tap **Get expert review**.\n\n" + how)
        return ("🏪 I'm the **Kagaz Expert Marketplace**. Verified CAs and lawyers review "
                "replies to official notices and get paid per job.\n\n"
                + expert_list(ctx) + "\n\n" + how + "\n\n" + join
                + "\n\n_Paste a notice to see which expert would take it._")

    chat = solo.chat_protocol("marketplace", "", on_chat, images=False, min_chars=0)

    agent.include(market, publish_manifest=True)
    agent.include(payments, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
