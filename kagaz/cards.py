"""ASI:One Interactive Card builders and chat helpers.

Cards follow the ASI:One card protocol v1: a TextContent narration plus one
MetadataContent block carrying card_kind and a JSON card_payload.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    TextContent,
)

from kagaz.core.reader import days_left

VERDICT_BADGE = {
    "SCAM": ("Likely SCAM", "warning"),
    "SUSPICIOUS": ("Suspicious", "warning"),
    "NO_RED_FLAGS": ("No scam signals", "success"),
    "UNVERIFIED": ("Not verified", "info"),
}


def text_message(text: str, end_session: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


def card_message(narration: str, card_kind: str, payload: dict,
                 terminal: bool = False) -> ChatMessage:
    metadata = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": card_kind,
        "card_payload": json.dumps(payload, ensure_ascii=False),
        "preferred_drawer_width_px": "560",
    }
    if terminal:
        metadata["is_terminal"] = "true"
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[
            TextContent(type="text", text=narration),
            MetadataContent(type="metadata", metadata=metadata),
        ],
    )


def _deadline_text(fields: dict) -> str:
    if not fields.get("deadline"):
        return "Not stated"
    left = days_left(fields["deadline"])
    label = datetime.fromisoformat(fields["deadline"]).strftime("%d %b %Y")
    if fields.get("deadline_estimated"):
        label += " (estimated)"
    if left is None:
        return label
    if left < 0:
        return f"{label} - passed {abs(left)} days ago"
    return f"{label} - {left} days left"


def review_extracted(fields: dict, report: dict) -> dict:
    label, _ = VERDICT_BADGE.get(report["verdict"], VERDICT_BADGE["UNVERIFIED"])
    rows = [
        {"label": "Notice", "value": fields.get("title") or "Official notice"},
        {"label": "From", "value": fields.get("issuer") or "Not stated"},
        {"label": "Amount", "value": fields.get("amount") or "None stated"},
        {"label": "Deadline", "value": _deadline_text(fields)},
    ]
    if fields.get("references"):
        rows.append({"label": "Reference", "value": fields["references"][0]})
    rows.append({"label": "Authenticity", "value": f"{label} (risk {report['score']}/100)"})
    return {
        "title": "Here is what I read",
        "summary_rows": rows,
        "approve_cta": {"label": "Looks right - explain and prepare my reply",
                        "selection": {"action": "proceed"}, "primary": True},
        "reject_cta": {"label": "Some details are wrong",
                       "selection": {"action": "edit"}},
    }


def scam_alert(fields: dict, report: dict) -> dict:
    signal_nodes = [
        {"type": "text", "value": f"• {s['label']}" + (f": {s['detail']}" if s["detail"] else ""),
         "style": "body"}
        for s in report["signals"] if s["weight"] > 0
    ][:6]
    advice_nodes = [{"type": "text", "value": f"{i}. {a}", "style": "body"}
                    for i, a in enumerate(report["advice"], 1)]
    return {
        "root": {
            "type": "section",
            "title": "Stop - this looks like a scam",
            "subtitle": f"Claims to be: {fields.get('title') or 'an official notice'}",
            "children": [
                {"type": "group", "direction": "row", "gap": 8, "children": [
                    {"type": "badge", "label": f"Risk {report['score']}/100", "variant": "warning"},
                    {"type": "badge", "label": "Do not click or pay", "variant": "warning"},
                ]},
                {"type": "heading", "value": "Why Kagaz flagged it", "level": 3},
                *signal_nodes,
                {"type": "divider"},
                {"type": "heading", "value": "What to do now", "level": 3},
                *advice_nodes,
                {"type": "button", "label": "Report this scam + warn my family",
                 "primary": True, "action": {"selection": {"action": "report_scam"}}},
                {"type": "button", "label": "Show me how to check this the official way",
                 "action": {"selection": {"action": "proceed"}}},
            ],
        }
    }


def edit_form(fields: dict) -> dict:
    from kagaz.core.knowledge import NOTICE_TYPES

    options = [{"value": k, "label": v["title"]} for k, v in NOTICE_TYPES.items()]
    options.append({"value": "unknown", "label": "Something else"})
    return {
        "title": "Correct the details",
        "fields": [
            {"name": "notice_type", "kind": "select", "label": "Type of notice",
             "options": options, "required": True},
            {"name": "amount", "kind": "text", "label": "Amount (e.g. 6240)"},
            {"name": "deadline", "kind": "text", "label": "Deadline (DD/MM/YYYY)"},
            {"name": "reference", "kind": "text", "label": "Reference / challan / consumer no."},
            {"name": "user_name", "kind": "text", "label": "Your name (for the letter)"},
        ],
        "submit_cta": {"label": "Save and continue", "selection": {"action": "edit_submit"}},
    }


HI = {
    "Your next steps": "आपके अगले कदम",
    "What it is": "यह क्या है",
    "Deadline": "आख़िरी तारीख़",
    "days left": "दिन बाकी",
    "Reminders I can set": "रिमाइंडर की तारीख़ें",
    "Reminders set": "रिमाइंडर सेट हैं",
    "Expert advised": "विशेषज्ञ की सलाह ज़रूरी",
    "Set deadline reminders": "रिमाइंडर सेट करें (Set deadline reminders)",
    "Get expert review": "विशेषज्ञ से जँचवाएं (Get expert review)",
    "I'm done": "हो गया (I'm done)",
    "What next?": "आगे क्या करें?",
}


def _t(text: str, language: str) -> str:
    return HI.get(text, text) if language == "hi" else text


def next_steps(explanation: dict, plan: dict, fee_inr: str, scam: bool,
               used: list | None = None, language: str = "en") -> dict:
    """Next-steps card. ASI:One lets a card be clicked only once, so after each
    action the coordinator sends this card again without the used buttons."""
    used = set(used or [])
    rows = [{"label": _t("What it is", language), "value": explanation["title"]}]
    if plan.get("deadline"):
        left = days_left(plan["deadline"])
        rows.append({"label": _t("Deadline", language),
                     "value": f"{plan['deadline']}"
                     + (f" ({left} {_t('days left', language)})" if left is not None else "")})
    if plan.get("reminders"):
        label = "Reminders set" if "remind" in used else "Reminders I can set"
        rows.append({"label": _t(label, language), "value": ", ".join(plan["reminders"])})
    if explanation.get("escalate") and "expert" not in used:
        rows.append({"label": _t("Expert advised", language),
                     "value": explanation["escalate_reason"]})
    ctas = []
    if plan.get("reminders") and "remind" not in used:
        ctas.append({"label": _t("Set deadline reminders", language),
                     "selection": {"action": "remind"},
                     "primary": not explanation.get("escalate")})
    if not scam and "expert" not in used:
        ctas.append({"label": f"{_t('Get expert review', language)} (Rs {fee_inr})",
                     "selection": {"action": "expert"},
                     "primary": bool(explanation.get("escalate"))})
    ctas.append({"label": _t("I'm done", language), "selection": {"action": "done"}})
    if not any(c.get("primary") for c in ctas):
        ctas[0]["primary"] = True
    title = "Your next steps" if not used else "What next?"
    return {"title": _t(title, language), "summary_rows": rows, "ctas": ctas}


def after_report(fields: dict) -> dict:
    """Follow-up card after the scam report pack (the scam card is now used up)."""
    return {
        "title": "What next?",
        "summary_rows": [
            {"label": "Claimed to be", "value": fields.get("title") or "An official notice"},
            {"label": "Report pack", "value": "Sent above - copy and file it"},
        ],
        "ctas": [
            {"label": "Show me how to check this the official way",
             "selection": {"action": "proceed"}, "primary": True},
            {"label": "I'm done", "selection": {"action": "done"}},
        ],
    }


def confirm_payment(fields: dict, fee_inr: str, fee_amount: str, currency: str,
                    live_gateway: bool = False) -> dict:
    return {
        "title": "Confirm expert review",
        "summary_rows": [
            {"label": "Notice", "value": fields.get("title") or "Official notice"},
            {"label": "Reviewer", "value": "A verified CA or lawyer from the Kagaz marketplace"},
            {"label": "Fee", "value": f"Rs {fee_inr}"},
            {"label": "Pay with", "value": "Card or UPI (Stripe)" if live_gateway
             else "Demo payment (no money moves)"},
            {"label": "Expert is paid", "value": f"Up to {fee_amount} {currency}, agent-to-agent, per job"},
            {"label": "You get", "value": "Draft check, missing documents, risks to watch"},
        ],
        "approve_cta": {"label": f"Pay Rs {fee_inr} and request review",
                        "selection": {"action": "pay_expert"}, "primary": True},
        "reject_cta": {"label": "Not now", "selection": {"action": "cancel"}},
    }


def payment_check(case_id: str, fee_inr: str) -> dict:
    return {
        "title": f"Pay Rs {fee_inr}",
        "summary_rows": [
            {"label": "Case", "value": case_id},
            {"label": "Secure checkout", "value": "Stripe (card / UPI)"},
        ],
        "approve_cta": {"label": "I've paid - check now",
                        "selection": {"action": "check_payment", "case_id": case_id},
                        "primary": True},
        "reject_cta": {"label": "Cancel", "selection": {"action": "cancel"}},
    }
