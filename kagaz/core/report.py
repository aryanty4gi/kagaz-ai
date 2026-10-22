"""Scam report pack: what to file, and what to forward to family.

After a scam verdict the user gets two ready-to-copy texts:
1. A complaint for cybercrime.gov.in (or to read out on the 1930 helpline).
2. A short warning in English and Hindi to forward on WhatsApp, so the
   same scam doesn't catch parents or relatives.
Built from the extracted fields only, so nothing is invented.
"""

import re
from datetime import date

from kagaz.core.reader import NoticeFields

KIND_HI = {
    "digital_arrest": "नकली पुलिस/CBI 'डिजिटल अरेस्ट'",
    "prize": "नकली इनाम/लॉटरी",
    "otp": "OTP माँगने वाला",
    "link": "नकली लिंक वाला",
    "generic": "धोखाधड़ी वाला",
}


def scam_kind(report: dict) -> str:
    labels = " ".join(s["label"].lower() for s in report.get("signals", []))
    if "digital arrest" in labels:
        return "digital_arrest"
    if "prize" in labels:
        return "prize"
    if "otp" in labels:
        return "otp"
    if "website" in labels or "link" in labels:
        return "link"
    return "generic"


def _claim(fields: NoticeFields) -> str:
    if fields.notice_type != "unknown" and fields.title:
        return f"a {fields.title[0].lower()}{fields.title[1:]}"
    return "an official message"


def complaint_text(fields: NoticeFields, report: dict, today: date | None = None) -> str:
    today = today or date.today()
    reasons = [s["label"] for s in report.get("signals", []) if s.get("weight", 0) > 0][:5]
    lines = [
        "Subject: Report of a fraudulent message (attempted cyber fraud)",
        "",
        f"On or before {today.strftime('%d %b %Y')} I received a message claiming to be "
        f"{_claim(fields)}. I believe it is a fraud attempt and I have not paid or shared any details.",
    ]
    if fields.amount:
        lines.append(f"Amount demanded: {fields.amount}")
    if fields.urls:
        lines.append("Links in the message: " + ", ".join(fields.urls[:5]))
    if fields.phones:
        lines.append("Phone numbers in the message: " + ", ".join(fields.phones[:5]))
    upi_ids = sorted(set(re.findall(r"\b[\w.-]{2,}@[a-z]{2,}\b", fields.raw_text)))
    upi_ids = [u for u in upi_ids if "." not in u.split("@")[1]]  # not email addresses
    if upi_ids:
        lines.append("UPI IDs in the message: " + ", ".join(upi_ids[:5]))
    if reasons:
        lines.append("Why it looks fraudulent: " + "; ".join(reasons) + ".")
    lines += [
        "",
        "Full text of the message:",
        f"\"{fields.raw_text.strip()[:1500]}\"",
        "",
        "Please take action against the numbers/links above.",
    ]
    return "\n".join(lines)


def family_warning(fields: NoticeFields, report: dict) -> str:
    kind = scam_kind(report)
    en = {
        "digital_arrest": "Police, CBI or customs NEVER arrest anyone on a video call and there is "
                          "no 'safe account'. Hang up and call 1930.",
        "prize": "Real prizes never ask you to pay a fee first. Don't pay.",
        "otp": "No bank or office ever asks for your OTP. Never share it.",
        "link": "Don't open the link. Check dues only on the official .gov.in website.",
        "generic": "Don't pay or share any details. Check on the official website.",
    }[kind]
    hi = {
        "digital_arrest": "पुलिस/CBI कभी वीडियो कॉल पर गिरफ्तार नहीं करती, कोई 'सेफ अकाउंट' नहीं होता। "
                          "फ़ोन काटें और 1930 पर कॉल करें।",
        "prize": "असली इनाम के लिए कभी पहले फीस नहीं देनी होती। पैसे न भेजें।",
        "otp": "कोई बैंक या दफ़्तर कभी OTP नहीं माँगता। OTP किसी को न बताएं।",
        "link": "लिंक न खोलें। बकाया सिर्फ़ सरकारी .gov.in वेबसाइट पर ही चेक करें।",
        "generic": "पैसे न भेजें, कोई जानकारी न दें। सरकारी वेबसाइट पर ही चेक करें।",
    }[kind]
    numbers = f" Scam numbers: {', '.join(fields.phones[:3])}." if fields.phones else ""
    return (
        f"⚠️ SCAM ALERT: a fake message pretending to be {_claim(fields)} is going around.{numbers} {en} "
        "Report: cybercrime.gov.in / 1930.\n\n"
        f"⚠️ सावधान: {KIND_HI[kind]} मैसेज आ रहा है। {hi} शिकायत: cybercrime.gov.in / 1930।\n\n"
        "(Checked with Kagaz AI on ASI:One)"
    )
