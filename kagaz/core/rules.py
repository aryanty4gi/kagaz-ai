"""Rules: explain the notice in plain language, grounded in the knowledge base.

The knowledge-base entry is retrieved first and handed to ASI:One as the
only allowed source, so the explanation stays tied to real rules.
"""

import re

from kagaz import llm
from kagaz.core import knowledge
from kagaz.core.reader import NoticeFields, days_left

ALWAYS_ESCALATE = {"it_148", "sarfaesi_13_2", "court_summons"}
LARGE_AMOUNT = 50000


def _amount_value(amount: str) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", amount) or 0)
    except ValueError:
        return 0.0


def explain(fields: NoticeFields, language: str = "en") -> dict:
    kb = knowledge.get(fields.notice_type)
    remaining = days_left(fields.deadline)

    escalate, reason = False, ""
    if fields.notice_type in ALWAYS_ESCALATE:
        escalate, reason = True, kb["escalate_when"]
    elif _amount_value(fields.amount) >= LARGE_AMOUNT:
        escalate, reason = True, f"The amount ({fields.amount}) is large."
    elif fields.notice_type == "unknown":
        escalate, reason = True, "This notice type is not in Kagaz's knowledge base yet."

    lang_name = "Hindi (Devanagari script)" if language == "hi" else "simple English"
    system = (
        "You explain Indian official notices to ordinary citizens. Use ONLY the "
        "rules given in SOURCE. Do not add laws, sections or deadlines that are "
        f"not in SOURCE or NOTICE. Write in {lang_name}, at most 90 words, warm "
        "and calm, no legal jargon. End with the single most important next step."
    )
    source = (
        f"SOURCE\nNotice type: {kb['title']} ({kb['law']})\n"
        f"Meaning: {kb['what_it_means']}\nDeadline rule: {kb['deadline_rule']}\n"
        f"Rights: {'; '.join(kb['rights'])}\n\n"
        f"NOTICE\nIssuer: {fields.issuer}\nAmount: {fields.amount or 'not stated'}\n"
        f"Deadline: {fields.deadline or 'not stated'}\nSummary: {fields.summary}\n"
        f"Text: {fields.raw_text[:2500]}"
    )
    plain = llm.complete(system, source, max_tokens=350)
    if not plain:
        plain = f"{kb['what_it_means']} {kb['deadline_rule']}"
        if language == "hi":
            plain += " (हिंदी अनुवाद के लिए AI सेवा अभी उपलब्ध नहीं है।)"

    return {
        "title": kb["title"],
        "law": kb["law"],
        "what_it_means": kb["what_it_means"],
        "deadline_rule": kb["deadline_rule"],
        "days_left": remaining,
        "steps": kb["steps"],
        "rights": kb["rights"],
        "portals": kb["official_portals"],
        "escalate": escalate,
        "escalate_reason": reason,
        "plain_explanation": plain,
        "language": language,
    }
