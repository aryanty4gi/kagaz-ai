"""Expert: paid review of a draft reply.

In production this agent fronts a network of verified CAs and lawyers who
pick up paid review jobs. In the prototype an ASI:One "senior reviewer"
produces the review, and every review is labelled as AI-assisted.
"""

from kagaz import llm
from kagaz.core import knowledge
from kagaz.core.reader import NoticeFields

REVIEWER = "Kagaz Expert Desk (AI-assisted review, prototype)"


def review(fields: NoticeFields, draft: str) -> str:
    kb = knowledge.get(fields.notice_type)
    system = (
        "You are a senior Indian chartered accountant and lawyer reviewing a "
        "citizen's draft reply to an official notice. Give: 1) a one-line verdict "
        "(ready to send / needs changes), 2) up to 4 specific improvements, "
        "3) documents to attach, 4) one risk to watch. Be concrete, under 180 "
        "words. Do not invent facts, sections or deadlines."
    )
    prompt = (
        f"Notice type: {kb['title']} ({kb['law']})\nDeadline rule: {kb['deadline_rule']}\n"
        f"Notice text:\n{fields.raw_text[:2500]}\n\nDraft reply:\n{draft[:2500]}"
    )
    result = llm.complete(system, prompt, max_tokens=450)
    if result:
        return result
    docs = {
        "traffic_echallan": "photo/video evidence, RC, proof of your location at that time",
        "it_143_1": "Form 26AS/AIS, Form 16, investment proofs",
        "it_139_9": "corrected ITR, supporting schedules",
        "it_148": "bank statements and records for that assessment year",
        "electricity_disconnection": "last 6 bills, payment receipts, meter photo",
        "sarfaesi_13_2": "loan statement, repayment receipts, sanction letter",
    }.get(fields.notice_type, "a copy of the notice and any receipts")
    return (
        "Verdict: needs changes before sending.\n"
        "1. Fill every [placeholder] with exact facts.\n"
        "2. Quote the notice reference number and date in the subject line.\n"
        "3. Keep the tone factual; attach evidence for each claim.\n"
        f"Documents to attach: {docs}.\n"
        f"Risk: {kb['deadline_rule']}"
    )
