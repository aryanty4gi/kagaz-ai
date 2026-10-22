"""Action: turn the explanation into something the person can actually do.

Produces a ready-to-send draft letter, an ordered action plan and reminder
dates. Template letters guarantee an output even without the LLM; ASI:One
tailors the draft to the facts of the notice when available.
"""

from datetime import date, datetime, timedelta

from kagaz import llm
from kagaz.core.reader import NoticeFields

TEMPLATES = {
    "traffic_echallan": (
        "To,\nThe Traffic Police Officer / Concerned Authority,\n{issuer}\n\n"
        "Subject: Request to review e-challan {ref}\n\n"
        "Sir/Madam,\n\nI have received e-challan {ref}{amount_part}. I request a review "
        "of this challan because [state your reason: e.g. the vehicle was not at the "
        "location / the number plate was misread / the violation did not occur].\n\n"
        "I request you to share the evidence for this challan and withdraw it if it was "
        "issued in error. I am also willing to present my case before the virtual court.\n\n"
        "Yours faithfully,\n{name}\nVehicle no.: [your vehicle number]\nDate: {today}"
    ),
    "it_143_1": (
        "Response to intimation u/s 143(1)\nReference: {ref}\n\n"
        "I have reviewed the intimation{amount_part}. I [agree / disagree] with the "
        "adjustment for the following reasons:\n1. [Reason, e.g. TDS shown in Form 26AS "
        "was not considered]\n2. [Supporting document attached]\n\n"
        "I request that the demand be corrected accordingly. Supporting documents are "
        "attached.\n\n{name}\nPAN: [your PAN]\nDate: {today}"
    ),
    "it_139_9": (
        "Response to notice u/s 139(9) (defective return)\nReference: {ref}\n\n"
        "I have noted the defect pointed out in the notice. I am [filing a corrected "
        "return / submitting the missing information] as follows: [details].\n\n"
        "{name}\nPAN: [your PAN]\nDate: {today}"
    ),
    "it_148": (
        "Reply to notice u/s 148\nReference: {ref}\n\n"
        "I acknowledge receipt of the notice. I request a copy of the reasons recorded "
        "and the material relied upon, so that I can respond fully. I will file my "
        "response within the time allowed.\n\n{name}\nPAN: [your PAN]\nDate: {today}"
    ),
    "electricity_disconnection": (
        "To,\nThe Assistant Engineer / Billing Section,\n{issuer}\n\n"
        "Subject: Dispute of bill and request to stop disconnection - {ref}\n\n"
        "Sir/Madam,\n\nI have received a disconnection notice{amount_part}. I dispute "
        "this bill because [reason: e.g. reading is higher than usual / meter fault / "
        "payment already made on (date)].\n\nI request a meter check and a corrected "
        "bill. As provided under section 56 of the Electricity Act, 2003, I am [paying "
        "the undisputed amount / paying under protest] and request that supply not be "
        "disconnected while my complaint is pending.\n\nYours faithfully,\n{name}\n"
        "Consumer no.: [your consumer number]\nDate: {today}"
    ),
    "sarfaesi_13_2": (
        "To,\nThe Authorised Officer,\n{issuer}\n\n"
        "Subject: Representation u/s 13(3A) of the SARFAESI Act against notice {ref}\n\n"
        "Sir/Madam,\n\nWith reference to your demand notice{amount_part}, I submit the "
        "following representation/objection: [state objections: e.g. amount "
        "calculation, payments not credited, classification as NPA].\n\nKindly "
        "consider this representation and reply as required under section 13(3A).\n\n"
        "Yours faithfully,\n{name}\nLoan account no.: [your account number]\nDate: {today}"
    ),
    "gst_notice": (
        "To,\nThe Proper Officer,\n{issuer}\n\n"
        "Subject: Reply to notice {ref}\n\n"
        "Sir/Madam,\n\nWith reference to the above notice{amount_part}, I submit my reply:\n"
        "1. [Explain each point raised, e.g. the ITC difference is due to invoices "
        "reported late by the supplier]\n2. [Reconciliation of GSTR-1/3B/2B attached]\n\n"
        "I request that the proceedings be dropped. I also request a personal hearing "
        "before any order is passed.\n\nYours faithfully,\n{name}\nGSTIN: [your GSTIN]\n"
        "Date: {today}"
    ),
    "court_summons": (
        "Note for your lawyer\n\nCase / CNR: {ref}\nCourt: [court name from the summons]\n"
        "Hearing date: [date on the summons]\n\nI have received a summons in this case"
        "{amount_part}. Background: [what the dispute is about, in your words].\n"
        "Documents I have: [agreements, receipts, messages].\n\n"
        "Please advise whether I should appear in person and what I need to file.\n\n"
        "{name}\nDate: {today}"
    ),
    "school_fee": (
        "To,\nThe Principal,\n{issuer}\n\n"
        "Subject: Request for fee details - {ref}\n\n"
        "Sir/Madam,\n\nI have received the fee notice{amount_part}. Kindly share the "
        "approved fee structure for this academic year and an itemised break-up of the "
        "amount, so that I can pay it on the school's official portal. [If you need "
        "time: I request an extension until (date) without late fee because (reason).]"
        "\n\nYours faithfully,\n{name}\nParent of: [child's name, class]\nDate: {today}"
    ),
    "unknown": (
        "To,\n{issuer}\n\nSubject: Request for clarification on notice {ref}\n\n"
        "Sir/Madam,\n\nI have received a notice{amount_part}. Please confirm that this "
        "notice was issued by your office and explain the basis for it. Kindly share "
        "the official channel through which I can respond or pay.\n\n"
        "Yours faithfully,\n{name}\nDate: {today}"
    ),
}


def _fill_template(fields: NoticeFields, user_name: str) -> str:
    template = TEMPLATES.get(fields.notice_type, TEMPLATES["unknown"])
    return template.format(
        issuer=fields.issuer or "[issuing office]",
        ref=fields.references[0] if fields.references else "[reference number]",
        amount_part=f" for {fields.amount}" if fields.amount else "",
        name=user_name or fields.recipient or "[Your name]",
        today=date.today().strftime("%d %b %Y"),
    )


def reminder_dates(deadline_iso: str, today: date | None = None) -> list[str]:
    today = today or date.today()
    if not deadline_iso:
        return []
    try:
        deadline = datetime.fromisoformat(deadline_iso).date()
    except ValueError:
        return []
    picks = {deadline - timedelta(days=7), deadline - timedelta(days=2), deadline}
    return sorted(d.isoformat() for d in picks if d >= today)


def build_plan(fields: NoticeFields, explanation: dict, user_name: str = "") -> dict:
    template_draft = _fill_template(fields, user_name)
    system = (
        "You draft short, formal replies to Indian official notices for ordinary "
        "citizens. Improve the TEMPLATE using facts from NOTICE only. Keep every "
        "[placeholder] the person must fill in. Never invent reference numbers, "
        "amounts, dates or legal sections. Max 220 words. Plain text only."
    )
    prompt = f"TEMPLATE\n{template_draft}\n\nNOTICE\n{fields.raw_text[:2500]}"
    draft = llm.complete(system, prompt, max_tokens=500) or template_draft

    steps = list(explanation.get("steps", []))
    if explanation.get("escalate"):
        steps.append("भेजने से पहले ड्राफ्ट किसी विशेषज्ञ से जँचवा लें।"
                     if explanation.get("language") == "hi"
                     else "Get the draft reviewed by an expert before sending it.")

    return {
        "draft": draft,
        "steps": steps,
        "portals": explanation.get("portals", []),
        "reminders": reminder_dates(fields.deadline),
        "deadline": fields.deadline,
        "escalate": explanation.get("escalate", False),
    }
