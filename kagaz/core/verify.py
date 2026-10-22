"""Verify: is this notice genuine, suspicious, or a scam?

Signals come from three places:
1. Rule checks on links, senders and wording (deterministic, explainable).
2. A live RDAP lookup of how old each unofficial domain is: scam sites are
   usually days or weeks old. Failures are reported, never fatal.
3. An optional ASI:One second opinion that can only add red flags.
"""

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from kagaz import config, llm
from kagaz.core import knowledge
from kagaz.core.reader import NoticeFields, _domain_of

SHORTENERS = {"bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "is.gd", "t.co",
              "shorturl.at", "goo.gl", "ow.ly", "tiny.cc", "rebrand.ly"}
RISKY_TLDS = {"xyz", "top", "shop", "online", "site", "icu", "click", "info",
              "live", "vip", "buzz", "cc", "pw", "fun", "store", "link", "net"}
IMPERSONATED = ["parivahan", "echallan", "challan", "incometax", "itr", "refund",
                "gov", "sbi", "hdfc", "icici", "axis", "pnb", "electricity", "bijli",
                "kyc", "rto", "traffic", "uidai", "aadhaar", "npci", "bescom", "tata-power"]
SECRET_ASK = re.compile(
    r"\b(otp|cvv|pin|password|card number|card details|net ?banking|login details)\b",
    re.IGNORECASE)
APK = re.compile(r"\.apk\b|download (?:the )?(?:app|apk)", re.IGNORECASE)
UPI = re.compile(r"\bupi\b|@ok(?:sbi|axis|icici|hdfc)|@ybl|@paytm|scan (?:the )?qr",
                 re.IGNORECASE)
URGENCY = re.compile(
    r"immediately|within 24 hours|within 2 hours|today itself|last warning|"
    r"will be (?:blocked|suspended|disconnected) (?:today|tonight)|arrest|"
    r"legal action will be taken|final notice",
    re.IGNORECASE)
# Scams that arrive with no link at all: fake police/CBI calls, lotteries, fake KYC.
DIGITAL_ARREST = re.compile(
    r"digital arrest|\b(?:cbi|ncb|ed|customs|cyber cell|narcotics)\b.{0,60}"
    r"(?:officer|parcel|case|warrant|arrest)|parcel.{0,40}(?:drugs|illegal)|"
    r"safe account|rbi (?:safe|secure|verification) account|money laundering case",
    re.IGNORECASE)
PRIZE_FEE = re.compile(
    r"(?:won|winner|lottery|lucky draw|prize|kbc|cashback of).{0,80}"
    r"(?:fee|charges|tax|pay|deposit|transfer)|processing fee|registration fee to claim",
    re.IGNORECASE)
ACCOUNT_THREAT = re.compile(
    r"(?:account|sim|card|connection|pan|aadhaar).{0,40}"
    r"(?:will be|is being|has been) (?:blocked|suspended|deactivated|disconnected|kaat)|"
    r"update (?:your )?(?:pan|kyc|aadhaar)",
    re.IGNORECASE)
HINGLISH_URGENCY = re.compile(
    r"turant|aaj raat|aaj hi|kaat diya jayega|band ho jayega|block ho jayega|"
    r"jaldi (?:karein|kare)|warna",
    re.IGNORECASE)
SHARE_SECRET = re.compile(
    r"(?:share|tell|send|give|forward|bata\w*)\b.{0,25}\b(?:otp|pin|cvv|password)|"
    r"(?:otp|pin|cvv)\b.{0,25}\b(?:share|bata)\w* (?:karein|kare|with)",
    re.IGNORECASE)
# Genuine bank OTP alerts say "do not share"; they are not asking for the OTP.
OTP_ALERT = re.compile(r"(?:do not|don't|never) share|is your otp|otp for (?:your )?transaction",
                       re.IGNORECASE)
REFUND_BAIT = re.compile(r"refund (?:of|is|has been)? ?(?:approved|sanctioned|pending)",
                         re.IGNORECASE)


@dataclass
class Signal:
    label: str
    weight: int
    detail: str = ""


@dataclass
class VerifyReport:
    verdict: str = "UNVERIFIED"   # SCAM | SUSPICIOUS | NO_RED_FLAGS
    score: int = 0                # 0-100, higher = riskier
    signals: list[dict] = field(default_factory=list)
    domains: list[dict] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def is_official(domain: str) -> bool:
    domain = domain.lower()
    if domain in knowledge.OFFICIAL_EXACT:
        return True
    return any(domain.endswith(s) for s in knowledge.OFFICIAL_SUFFIXES)


def registrable(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "org", "net", "gov", "ac", "nic"} and parts[-1] == "in":
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_age_days(domain: str, timeout: float = 4.0) -> tuple[int | None, str]:
    """Live RDAP lookup. Returns (age_in_days, error_message)."""
    if not config.ONLINE_CHECKS:
        return None, "online checks disabled"
    url = f"https://rdap.org/domain/{registrable(domain)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/rdap+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for event in data.get("events", []):
            if event.get("eventAction") == "registration":
                created = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - created).days, ""
        return None, "registration date not published"
    except Exception as err:  # offline, 404 for unregistered domains, timeouts
        return None, f"domain lookup failed ({type(err).__name__})"


def _rule_signals(fields: NoticeFields, text: str) -> tuple[list[Signal], list[dict], list[str]]:
    # Photos and screenshots wrap lines anywhere ("digital\narrest"), so match on
    # single-spaced text.
    text = re.sub(r"\s+", " ", text)
    signals: list[Signal] = []
    domains: list[dict] = []
    failed: list[str] = []
    official_link_seen = False

    for url in fields.urls:
        dom = _domain_of(url)
        info = {"domain": dom, "official": is_official(dom), "age_days": None}
        if info["official"]:
            official_link_seen = True
            domains.append(info)
            continue
        brand = next((b for b in IMPERSONATED if b in dom), None)
        if brand:
            signals.append(Signal(
                "Look-alike website",
                45,
                f"{dom} uses '{brand}' but is not an official .gov.in/.nic.in/.bank.in site"))
        if dom in SHORTENERS or registrable(dom) in SHORTENERS:
            signals.append(Signal("Shortened link hides the real site", 30, dom))
        tld = dom.rsplit(".", 1)[-1]
        if tld in RISKY_TLDS:
            signals.append(Signal("Unusual domain ending", 15, f".{tld}"))
        if url.lower().startswith("http://"):
            signals.append(Signal("Link is not secure (http)", 5, dom))
        age, err = domain_age_days(dom)
        info["age_days"] = age
        if err:
            failed.append(f"{dom}: {err}")
        elif age is not None and age < 180:
            signals.append(Signal("Website created very recently", 25, f"{dom} is {age} days old"))
        domains.append(info)

    if fields.urls and not official_link_seen and not signals:
        signals.append(Signal("Link is not on an official government domain", 15,
                              ", ".join(d["domain"] for d in domains)))
    if APK.search(text):
        signals.append(Signal("Asks you to install an app/APK", 40,
                              "Government departments never send APK files"))
    if SECRET_ASK.search(text) and not (OTP_ALERT.search(text) and not fields.urls
                                        and not fields.phones):
        signals.append(Signal("Asks for OTP, PIN, CVV or passwords", 40,
                              "No genuine department asks for these"))
    if UPI.search(text):
        signals.append(Signal("Asks for payment by UPI ID or QR", 20,
                              "Official fines are paid on the department's own portal"))
    if fields.phones and re.search(r"call|whatsapp|contact|sampark", text, re.IGNORECASE):
        signals.append(Signal("Personal mobile number to contact", 15, ", ".join(fields.phones)))
    if SHARE_SECRET.search(text) and not re.search(r"(?:do not|don't|never|mat)\W+share",
                                                    text, re.IGNORECASE):
        signals.append(Signal("Tells you to share the OTP/PIN with someone", 30,
                              "Banks never ask anyone to collect your OTP"))
    if DIGITAL_ARREST.search(text):
        signals.append(Signal("Fake police/CBI 'digital arrest' threat", 60,
                              "Police and CBI never arrest or settle cases over calls or chat, "
                              "and there is no 'safe account'"))
    if PRIZE_FEE.search(text):
        signals.append(Signal("Prize that needs a fee first", 50,
                              "Real prizes never ask you to pay to claim them"))
    if ACCOUNT_THREAT.search(text):
        signals.append(Signal("Threat to block your account or service", 20,
                              ACCOUNT_THREAT.search(text).group(0)))
    urgency = URGENCY.search(text) or HINGLISH_URGENCY.search(text)
    if urgency:
        signals.append(Signal("Pressure tactics", 10, urgency.group(0)))
    if fields.phones and not fields.urls and urgency and re.search(
            r"call|whatsapp|contact|sampark|phone", text, re.IGNORECASE):
        signals.append(Signal("Asks you to call a mobile number urgently", 15,
                              "Departments give official helplines, not personal numbers"))
    if REFUND_BAIT.search(text) and fields.urls:
        signals.append(Signal("Refund bait with a link", 20,
                              "Income tax refunds are credited automatically to your bank"))
    if official_link_seen and not signals:
        signals.append(Signal("All links point to official domains", -20, ""))
    return signals, domains, failed


LLM_SYSTEM = (
    "You are a fraud analyst for Indian SMS, email and letter scams (fake "
    "e-challans, fake income tax refunds, fake electricity disconnection, fake "
    "KYC, digital arrest, lottery, job and investment scams). List only concrete red flags actually present in the text. Return "
    'JSON: {"red_flags": ["..."], "looks_like_scam": true|false}. '
    "Return an empty list if nothing is suspicious."
)


def verify(fields: NoticeFields, use_llm: bool = True) -> VerifyReport:
    text = fields.raw_text
    signals, domains, failed = _rule_signals(fields, text)

    if use_llm:
        data = llm.complete_json(LLM_SYSTEM, text[:4000], max_tokens=300)
        if data and data.get("looks_like_scam") and data.get("red_flags"):
            flags = [str(f)[:120] for f in data["red_flags"][:3]]
            signals.append(Signal("AI fraud analyst flagged it", 25, "; ".join(flags)))

    score = max(0, min(100, sum(s.weight for s in signals)))
    report = VerifyReport(score=score, domains=domains, checks_failed=failed)
    report.signals = [asdict(s) for s in sorted(signals, key=lambda s: -s.weight)]

    kb = knowledge.get(fields.notice_type)
    portal = kb["official_portals"][0] if kb["official_portals"] else "the official website"
    if score >= 60:
        report.verdict = "SCAM"
        report.advice = [
            ("Do not click the link, pay, or share any details." if fields.urls else
             "Do not pay, call back, or share any details."),
            f"Check for real dues yourself on {portal}.",
            "Report the message at cybercrime.gov.in or call 1930.",
            knowledge.CYBER_HELP,
        ]
    elif score >= 25:
        report.verdict = "SUSPICIOUS"
        report.advice = [
            "Do not use links in the message.",
            f"Open {portal} yourself and check whether this notice exists.",
        ]
    else:
        report.verdict = "NO_RED_FLAGS"
        report.advice = [f"No scam signals found. Still confirm it on {portal} before paying."]
    return report
