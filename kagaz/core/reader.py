"""Reader: turn raw notice text into structured fields.

Deterministic extraction runs first and always works; the ASI:One LLM then
fills gaps (issuer, plain summary, recipient). Regex results win on hard
facts like amounts and dates, so the LLM cannot invent a number.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from kagaz import llm
from kagaz.core import knowledge

URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?", re.IGNORECASE
)
AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr|rupees)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)|"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:/-|rupees|inr)",
    re.IGNORECASE,
)
DAYS_RE = re.compile(r"within\s+(\d{1,3})\s+(?:clear\s+)?days", re.IGNORECASE)
REF_RE = re.compile(
    r"(?:challan|reference|ref|din|notice|document|case|consumer|account|loan)"
    r"\s*(?:no\.?|number|id|#)?\s*[:\-]?\s*((?=[A-Z/\-]*\d)[A-Z0-9][A-Z0-9/\-]{5,})",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})\b",
                re.IGNORECASE), "d_mon_y"),
]
DEADLINE_HINT = re.compile(
    r"(due|by|before|last date|on or before|deadline|pay by|respond by|within)",
    re.IGNORECASE,
)


@dataclass
class NoticeFields:
    notice_type: str = "unknown"
    title: str = "Official notice"
    issuer: str = ""
    recipient: str = ""
    amount: str = ""
    deadline: str = ""          # ISO date if known
    deadline_estimated: bool = False
    days_to_respond: int = 0
    references: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    summary: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NoticeFields":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _parse_dates(text: str) -> list[tuple[int, date]]:
    found = []
    for pattern, kind in DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if kind == "dmy":
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                elif kind == "ymd":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    mon = MONTHS.get(m.group(2).lower()[:3])
                    if not mon:
                        continue
                    d = date(int(m.group(3)), mon, int(m.group(1)))
                found.append((m.start(), d))
            except ValueError:
                continue
    return found


def _pick_deadline(text: str, days_to_respond: int = 0) -> tuple[str, bool]:
    """Return (deadline_iso, estimated)."""
    dates = _parse_dates(text)
    # A date right after a deadline word is the stated deadline.
    for pos, d in dates:
        window = text[max(0, pos - 40):pos]
        if DEADLINE_HINT.search(window):
            return d.isoformat(), False
    # "within 60 days": count from the latest date in the notice (usually
    # the notice date), or from today if the notice has no date.
    if days_to_respond:
        base = max((d for _, d in dates), default=date.today())
        return (base + timedelta(days=days_to_respond)).isoformat(), True
    return "", False


def _domain_of(url: str) -> str:
    host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/")[0]
    return host.lower().strip(".")


def extract_rule_based(text: str) -> NoticeFields:
    f = NoticeFields(raw_text=text.strip())
    f.notice_type = knowledge.classify(text)
    kb = knowledge.get(f.notice_type)
    f.title = kb["title"]
    f.issuer = kb["issuer"] if f.notice_type != "unknown" else ""

    amounts = []
    for m in AMOUNT_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            amounts.append(float(raw))
        except ValueError:
            pass
    if amounts:
        top = max(amounts)
        f.amount = f"₹{top:,.0f}" if top == int(top) else f"₹{top:,.2f}"

    days = DAYS_RE.search(text)
    if days:
        f.days_to_respond = int(days.group(1))
    f.deadline, f.deadline_estimated = _pick_deadline(text, f.days_to_respond)

    refs = [r.strip(".,") for r in REF_RE.findall(text)]
    f.references = list(dict.fromkeys(r for r in refs if any(c.isdigit() for c in r)))[:4]

    urls = []
    for m in URL_RE.finditer(text):
        u = m.group(0).rstrip(".,)")
        dom = _domain_of(u)
        # skip things like "Rs.500" or "u/s.143" that look domain-ish
        if "." in dom and not dom.replace(".", "").isdigit() and len(dom.split(".")[-1]) >= 2:
            if re.search(r"[a-z]", dom.split(".")[-1]):
                urls.append(u)
    f.urls = list(dict.fromkeys(urls))
    f.phones = list(dict.fromkeys(PHONE_RE.findall(text)))
    return f


LLM_SYSTEM = (
    "You extract facts from Indian official notices and messages (tax, traffic "
    "challan, electricity, bank). Return JSON with keys: issuer (who claims to "
    "send it), recipient (person's name or empty), title (short notice name), "
    "summary (one plain-English sentence on what the notice asks the person to "
    "do). Use only facts present in the text; use empty strings when unsure."
)


def extract(text: str) -> NoticeFields:
    """Rule-based extraction, enriched by ASI:One when available."""
    fields = extract_rule_based(text)
    data = llm.complete_json(LLM_SYSTEM, text[:6000], max_tokens=400)
    if data:
        fields.issuer = str(data.get("issuer") or fields.issuer)[:120]
        fields.recipient = str(data.get("recipient") or "")[:80]
        if fields.notice_type == "unknown" and data.get("title"):
            fields.title = str(data["title"])[:80]
        fields.summary = str(data.get("summary") or "")[:300]
    if not fields.summary:
        fields.summary = knowledge.get(fields.notice_type)["what_it_means"]
    return fields


def days_left(deadline_iso: str, today: date | None = None) -> int | None:
    if not deadline_iso:
        return None
    try:
        d = datetime.fromisoformat(deadline_iso).date()
    except ValueError:
        return None
    return (d - (today or date.today())).days
