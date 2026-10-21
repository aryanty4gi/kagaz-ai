"""Expert Agent: paid draft reviews, settled with the Agent Payment Protocol.

Flow (agent-to-agent):
  Coordinator --ExpertReviewRequest--> Expert
  Expert      --RequestPayment-------> Coordinator   (invoice)
  Coordinator --CommitPayment--------> Expert        (payment + transaction id)
  Expert      --CompletePayment------> Coordinator   (receipt)
  Expert      --ExpertReviewResult---> Coordinator   (the review)

Prototype note: payments use demo transaction ids unless KAGAZ_REAL_FET=true,
in which case the expert checks the FET transfer on the ledger.
"""

import asyncio
import json
import os

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    CancelPayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents.common import make_agent
from kagaz import config
from kagaz.core import expert
from kagaz.core.reader import NoticeFields
from kagaz.messages import ExpertReviewRequest, ExpertReviewResult

REAL_FET = os.getenv("KAGAZ_REAL_FET", "false").lower() == "true"


def build(mailbox: bool | None = None):
    agent = make_agent(
        "expert",
        "Paid expert review of replies to Indian official notices. Accepts payment "
        "through the Agent Payment Protocol.",
        mailbox,
    )
    review_proto = Protocol(name="KagazExpertReview", version="1.0.0")
    payment = Protocol(spec=payment_protocol_spec, role="seller")

    @review_proto.on_message(ExpertReviewRequest)
    async def on_review_request(ctx: Context, sender: str, msg: ExpertReviewRequest):
        ctx.storage.set(f"job:{msg.case_id}", {
            "client": sender, "fields_json": msg.fields_json, "draft": msg.draft_text,
            "paid": False,
        })
        recipient = str(agent.wallet.address()) if REAL_FET else agent.address
        await ctx.send(sender, RequestPayment(
            accepted_funds=[Funds(amount=config.EXPERT_FEE_AMOUNT,
                                  currency=config.EXPERT_FEE_CURRENCY,
                                  payment_method="fet_direct")],
            recipient=recipient,
            deadline_seconds=300,
            reference=msg.case_id,
            description="Kagaz expert review of a notice reply",
            metadata={},
        ))
        ctx.logger.info(f"Invoice sent for case {msg.case_id}")

    async def _payment_ok(ctx: Context, msg: CommitPayment) -> bool:
        if not msg.transaction_id:
            return False
        if msg.funds.amount != config.EXPERT_FEE_AMOUNT or msg.funds.currency != config.EXPERT_FEE_CURRENCY:
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

    agent.include(review_proto, publish_manifest=True)
    agent.include(payment, publish_manifest=True)
    return agent


if __name__ == "__main__":
    build().run()
