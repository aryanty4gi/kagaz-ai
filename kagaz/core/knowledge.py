"""Curated knowledge base of common Indian notices.

Each entry is the grounded source the Rules agent retrieves from, so the
LLM explains real rules instead of inventing them. Kept deliberately small
and conservative for the prototype; it grows notice type by notice type.
This is general information, not legal advice.
"""

NOTICE_TYPES: dict[str, dict] = {
    "traffic_echallan": {
        "title": "Traffic e-challan",
        "issuer": "State traffic police / transport department (Parivahan)",
        "law": "Motor Vehicles Act, 1988",
        "keywords": ["challan", "e-challan", "echallan", "traffic", "violation",
                     "vehicle", "parivahan", "speeding", "signal", "rto", "helmet"],
        "what_it_means": (
            "Your vehicle was recorded for a traffic violation and a fine was issued."
        ),
        "deadline_rule": (
            "Pay or contest promptly. Unpaid challans can be sent to a court, and "
            "pending challans can block vehicle services such as transfer or renewal."
        ),
        "official_portals": ["https://echallan.parivahan.gov.in",
                             "https://vcourts.gov.in"],
        "steps": [
            "Open echallan.parivahan.gov.in yourself (never through an SMS link).",
            "Search with your challan number, vehicle number or DL number.",
            "If the challan is genuine and correct, pay on the official portal.",
            "If you dispute it, contest it through the virtual court (vcourts.gov.in) "
            "or the traffic police office named on the challan.",
        ],
        "rights": [
            "You can see the evidence (photo/video) for camera-issued challans on the portal.",
            "You can contest a wrong challan instead of paying it.",
        ],
        "escalate_when": "The challan is wrong and has already gone to court.",
        "official_domains": ["parivahan.gov.in", "vcourts.gov.in"],
    },
    "it_143_1": {
        "title": "Income Tax intimation u/s 143(1)",
        "issuer": "Income Tax Department (CPC, Bengaluru)",
        "law": "Income-tax Act, 1961, section 143(1)",
        "keywords": ["143(1)", "intimation", "cpc", "income tax", "itr", "processed",
                     "demand", "refund", "assessment year", "ay "],
        "what_it_means": (
            "Your income tax return was processed. The intimation compares your "
            "figures with the department's. It may show no change, a refund, or a "
            "tax demand if something did not match."
        ),
        "deadline_rule": (
            "If it shows a demand, respond on the e-filing portal: agree and pay, "
            "or disagree with reasons. A demand notice usually gives 30 days."
        ),
        "official_portals": ["https://www.incometax.gov.in"],
        "steps": [
            "Log in at incometax.gov.in yourself and open Pending Actions.",
            "Compare the intimation with your return and your AIS/Form 26AS.",
            "Under 'Response to Outstanding Demand', agree (and pay) or disagree "
            "with reasons and documents.",
            "If you made a mistake, consider a rectification request u/s 154.",
        ],
        "rights": [
            "You can disagree with a demand and give reasons.",
            "You can request rectification of an apparent mistake (section 154).",
        ],
        "escalate_when": "The demand is large or you do not understand the mismatch.",
        "official_domains": ["incometax.gov.in", "gov.in"],
    },
    "it_139_9": {
        "title": "Defective return notice u/s 139(9)",
        "issuer": "Income Tax Department (CPC)",
        "law": "Income-tax Act, 1961, section 139(9)",
        "keywords": ["139(9)", "defective", "defect", "income tax", "itr"],
        "what_it_means": (
            "The department found a defect in your return (for example a wrong ITR "
            "form or missing schedule) and is asking you to fix it."
        ),
        "deadline_rule": (
            "Usually 15 days from the notice. If you do not respond, the return "
            "can be treated as invalid, as if it was never filed."
        ),
        "official_portals": ["https://www.incometax.gov.in"],
        "steps": [
            "Log in at incometax.gov.in and open the e-Proceedings section.",
            "Read the defect described in the notice.",
            "Respond by agreeing and filing a corrected return, or disagreeing with reasons.",
        ],
        "rights": ["You can ask for more time with reasons before the deadline."],
        "escalate_when": "You do not understand the defect or the return is complex.",
        "official_domains": ["incometax.gov.in", "gov.in"],
    },
    "it_148": {
        "title": "Reassessment notice u/s 148",
        "issuer": "Income Tax Department (Assessing Officer)",
        "law": "Income-tax Act, 1961, sections 148 and 148A",
        "keywords": ["reassessment", "escaped assessment",
                     "income escaping", "reopen"],
        "what_it_means": (
            "The department believes some income escaped assessment in an earlier "
            "year and is reopening that year."
        ),
        "deadline_rule": (
            "File the return or reply within the period stated in the notice. "
            "This is a serious notice; do not ignore it."
        ),
        "official_portals": ["https://www.incometax.gov.in"],
        "steps": [
            "Check the notice on incometax.gov.in under e-Proceedings to confirm it is genuine.",
            "Note the exact deadline in the notice.",
            "Collect bank statements and documents for that year.",
            "Get a chartered accountant to review before replying.",
        ],
        "rights": [
            "You are entitled to the reasons and an opportunity to be heard "
            "(the section 148A process).",
        ],
        "escalate_when": "Always. Reassessment should be handled with a CA.",
        "official_domains": ["incometax.gov.in", "gov.in"],
    },
    "electricity_disconnection": {
        "title": "Electricity disconnection notice",
        "issuer": "Electricity distribution company (DISCOM)",
        "law": "Electricity Act, 2003, section 56",
        "keywords": ["electricity", "disconnection", "disconnect", "power supply",
                     "discom", "bijli", "meter", "consumer number", "units",
                     "बिजली", "कनेक्शन", "मीटर"],
        "what_it_means": (
            "Your electricity provider says a bill is unpaid and supply may be cut."
        ),
        "deadline_rule": (
            "Section 56 requires at least 15 clear days' written notice before "
            "disconnection for non-payment."
        ),
        "official_portals": ["Your DISCOM's official website or app"],
        "steps": [
            "Check the dues on your DISCOM's official website or app, not via SMS links.",
            "If the bill is correct, pay through the official website or app.",
            "If you dispute it, you can pay under protest and file a complaint.",
            "Unresolved complaints go to the Consumer Grievance Redressal Forum (CGRF), "
            "then the Electricity Ombudsman.",
        ],
        "rights": [
            "15 clear days' written notice before disconnection for non-payment.",
            "Right to complain to the CGRF and then the Ombudsman.",
        ],
        "escalate_when": "Supply was cut without proper notice or the bill is clearly wrong.",
        "official_domains": [],
    },
    "sarfaesi_13_2": {
        "title": "Bank demand notice u/s 13(2) SARFAESI",
        "issuer": "Bank or financial institution",
        "law": "SARFAESI Act, 2002, sections 13(2) and 13(3A)",
        "keywords": ["sarfaesi", "13(2)", "npa", "non-performing", "secured asset",
                     "loan", "demand notice", "borrower", "possession"],
        "what_it_means": (
            "Your loan has been classified as a non-performing asset. The bank is "
            "demanding the full dues and may take possession of the secured property."
        ),
        "deadline_rule": (
            "You have 60 days from the notice to pay the dues. Within that time you "
            "can send a written representation or objection; under section 13(3A) "
            "the bank must reply to it within 15 days."
        ),
        "official_portals": ["Your bank's official branch or website"],
        "steps": [
            "Confirm the notice with your bank branch directly.",
            "Check the outstanding amount and your repayment history.",
            "Send a written representation or objection within 60 days if you disagree.",
            "Talk to a lawyer before the 60 days end.",
        ],
        "rights": [
            "Right to object in writing; the bank must reply within 15 days.",
        ],
        "escalate_when": "Always. Property is at risk.",
        "official_domains": [],
    },
    "gst_notice": {
        "title": "GST notice",
        "issuer": "GST department (Central or State tax officer)",
        "law": "CGST Act, 2017 (scrutiny u/s 61, demand u/s 73/74)",
        "keywords": ["gst", "gstin", "asmt", "drc-01", "drc 01", "cgst", "sgst",
                     "input tax credit", "show cause", "gstr", "tax period"],
        "what_it_means": (
            "The GST officer found a mismatch or a short payment in your returns and is "
            "asking you to explain it, or proposing tax, interest and penalty."
        ),
        "deadline_rule": (
            "The notice states the reply period, usually 30 days. Scrutiny notices "
            "(ASMT-10) are answered in ASMT-11; show cause notices (DRC-01) in DRC-06. "
            "No reply can lead to an order against you."
        ),
        "official_portals": ["https://www.gst.gov.in", "https://www.cbic.gov.in"],
        "steps": [
            "Log in at gst.gov.in yourself: Services > User Services > View Notices and Orders.",
            "If it is a central tax letter, verify its DIN on cbic.gov.in.",
            "Match the figures with your GSTR-1, GSTR-3B and GSTR-2B for that period.",
            "Reply on the portal in the form named in the notice, with documents, before the date.",
            "If you agree, pay through the portal (DRC-03) and mention it in the reply.",
        ],
        "rights": [
            "Right to a personal hearing before an adverse order.",
            "Right to appeal an order within the time allowed (section 107).",
        ],
        "escalate_when": "A demand with penalty, or you cannot explain the mismatch.",
        "official_domains": ["gst.gov.in", "cbic.gov.in"],
    },
    "court_summons": {
        "title": "Court summons",
        "issuer": "Court (civil or criminal)",
        "law": "Code of Civil Procedure, 1908 (Order V) / Bharatiya Nagarik Suraksha Sanhita, 2023",
        "keywords": ["summons", "court", "magistrate", "cnr", "case no", "plaintiff",
                     "defendant", "hearing", "appear", "advocate", "judge", "complainant"],
        "what_it_means": (
            "A court is asking you to appear (or answer) in a case on a fixed date. "
            "It does not mean you are guilty or must pay anything."
        ),
        "deadline_rule": (
            "Appear in person or through a lawyer on the date written on the summons. "
            "Ignoring a civil summons can lead to a decision without you; ignoring a "
            "criminal summons can lead to a warrant."
        ),
        "official_portals": ["https://ecourts.gov.in", "https://services.ecourts.gov.in"],
        "steps": [
            "Check the case on services.ecourts.gov.in with the CNR or case number.",
            "Note the court, the case number and the hearing date.",
            "Talk to a lawyer before the date; free legal aid is available through "
            "your District Legal Services Authority (nalsa.gov.in).",
            "Appear on the date, in person or through your lawyer.",
            "Never pay anyone who offers to 'cancel' or 'settle' a summons by phone.",
        ],
        "rights": [
            "Right to be represented by a lawyer, and to free legal aid if eligible.",
            "Right to see the complaint or plaint filed against you.",
        ],
        "escalate_when": "Always. Talk to a lawyer before the hearing date.",
        "official_domains": ["ecourts.gov.in", "nalsa.gov.in"],
    },
    "school_fee": {
        "title": "School fee notice",
        "issuer": "School",
        "law": "State school fee regulation rules (vary by state); RTE Act, 2009",
        "keywords": ["school", "tuition", "fee", "student", "admission", "term",
                     "late fee", "parent", "academic year", "class"],
        "what_it_means": (
            "Your child's school says a fee is due, often with a late fee if paid after a date."
        ),
        "deadline_rule": (
            "Pay by the date on the notice to avoid a late fee. If you dispute the amount, "
            "raise it in writing with the school before the date."
        ),
        "official_portals": ["Your school's official fee portal or office"],
        "steps": [
            "Confirm the amount with the school office or its official fee portal.",
            "Ask for the approved fee structure and an itemised bill if the amount looks high.",
            "Pay only to the school's own portal or bank account, and keep the receipt.",
            "If the hike looks above the allowed limit, complain to your state's fee "
            "regulatory committee or the district education office.",
        ],
        "rights": [
            "Right to an itemised fee structure and a receipt for every payment.",
            "EWS/DG seats under section 12 of the RTE Act are free.",
        ],
        "escalate_when": "A large, unexplained fee hike or threats to stop the child's studies.",
        "official_domains": [],
    },
}

GENERIC = {
    "title": "Official notice",
    "issuer": "Unknown",
    "law": "",
    "what_it_means": "A notice that is not yet in Kagaz's knowledge base.",
    "deadline_rule": "Look for a date or 'within N days' in the notice and treat it as the deadline.",
    "official_portals": [],
    "steps": [
        "Confirm the notice with the issuing office through its official website or phone number.",
        "Do not pay or share details through links in the message.",
    ],
    "rights": [],
    "escalate_when": "You cannot confirm what the notice is about.",
    "official_domains": [],
}

# Domains that genuine government or bank messages use.
OFFICIAL_SUFFIXES = (".gov.in", ".nic.in", ".bank.in")
OFFICIAL_EXACT = {
    "incometax.gov.in", "echallan.parivahan.gov.in", "parivahan.gov.in",
    "vcourts.gov.in", "cybercrime.gov.in", "sbi.co.in", "onlinesbi.sbi",
    "gst.gov.in", "services.gst.gov.in", "cbic.gov.in", "ecourts.gov.in",
    "services.ecourts.gov.in",
}

CYBER_HELP = (
    "If you already paid or shared OTP/card details: call 1930 (National Cyber "
    "Crime Helpline) immediately and report at cybercrime.gov.in."
)


# Section references are strong signals; matched as patterns so that an
# amount like "Rs 1,480" is never mistaken for section 148.
_SECTION_PATTERNS = {
    "it_143_1": r"143\s*\(\s*1\s*\)",
    "it_139_9": r"139\s*\(\s*9\s*\)",
    "it_148": r"(?:section|sec\.?|u/s|under)\s*148a?\b|\b148a\b",
    "sarfaesi_13_2": r"13\s*\(\s*2\s*\)|sarfaesi",
    "gst_notice": r"\bgstin\b|asmt-?\s?1[01]\b|drc-?\s?0?[16]\b|cgst act",
    "court_summons": r"\bsummons\b|\bcnr\b",
    "school_fee": r"school fees?|tuition fees?|fee (?:due|reminder)",
}


def classify(text: str) -> str:
    """Pick the notice type whose keywords best match the text."""
    import re

    low = text.lower()
    best, best_score = "unknown", 0
    for key, entry in NOTICE_TYPES.items():
        score = sum(1 for kw in entry["keywords"] if kw in low)
        pattern = _SECTION_PATTERNS.get(key)
        if pattern and re.search(pattern, low):
            score += 4
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= 2 else "unknown"


def get(notice_type: str) -> dict:
    return NOTICE_TYPES.get(notice_type, GENERIC)
