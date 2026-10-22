"""Unit tests for the Kagaz core logic (no network, no LLM).

    KAGAZ_ONLINE_CHECKS=false python -m pytest -q
"""

import os
from datetime import date
from pathlib import Path

os.environ["KAGAZ_ONLINE_CHECKS"] = "false"
os.environ["ASI1_API_KEY"] = ""

from kagaz.core import action, knowledge, reader, rules, verify  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def load(name: str) -> str:
    return (SAMPLES / name).read_text()


def check(name: str):
    fields = reader.extract(load(name))
    return fields, verify.verify(fields, use_llm=False)


def test_fake_echallan_is_scam():
    fields, report = check("01_fake_echallan_sms.txt")
    assert fields.notice_type == "traffic_echallan"
    assert report.verdict == "SCAM"
    labels = {s["label"] for s in report.signals}
    assert "Look-alike website" in labels
    assert "Asks you to install an app/APK" in labels


def test_fake_refund_is_scam():
    _, report = check("02_fake_tax_refund.txt")
    assert report.verdict == "SCAM"
    assert any("OTP" in s["label"] for s in report.signals)


def test_genuine_tax_notice_has_no_red_flags():
    fields, report = check("03_income_tax_143_1.txt")
    assert fields.notice_type == "it_143_1"
    assert fields.amount == "₹18,450"
    assert fields.deadline == "2026-11-20"
    assert report.verdict == "NO_RED_FLAGS"


def test_electricity_notice_fields():
    fields, report = check("04_electricity_disconnection.txt")
    assert fields.notice_type == "electricity_disconnection"
    assert fields.amount == "₹6,240"
    assert fields.deadline == "2026-11-05"
    assert "4123987650" in fields.references
    assert report.verdict == "NO_RED_FLAGS"


def test_sarfaesi_deadline_is_estimated_and_escalated():
    fields = reader.extract(load("05_sarfaesi_13_2.txt"))
    assert fields.notice_type == "sarfaesi_13_2"
    assert fields.deadline == "2026-12-14"  # 15/10/2026 + 60 days
    assert fields.deadline_estimated
    assert "HL/MRT/2021/004512" in fields.references
    assert rules.explain(fields)["escalate"] is True


def test_amount_is_not_mistaken_for_section_148():
    assert knowledge.classify("Pay Rs 1,480 electricity bill dues, consumer number 12") \
        == "electricity_disconnection"
    assert knowledge.classify("Notice under section 148 for reassessment") == "it_148"


def test_official_domains():
    assert verify.is_official("echallan.parivahan.gov.in")
    assert verify.is_official("www.incometax.gov.in")
    assert not verify.is_official("echallan-parivahan.top")
    assert not verify.is_official("incometax-refund.online")


def test_reminders_skip_past_dates():
    assert action.reminder_dates("2026-11-20", today=date(2026, 11, 15)) == \
        ["2026-11-18", "2026-11-20"]


def test_draft_keeps_placeholders_and_facts():
    fields = reader.extract(load("04_electricity_disconnection.txt"))
    plan = action.build_plan(fields, rules.explain(fields))
    assert "₹6,240" in plan["draft"]
    assert "[reason" in plan["draft"]


def _verdict(text: str) -> str:
    return verify.verify(reader.extract(text), use_llm=False).verdict


def test_linkless_scams_are_caught():
    assert _verdict("This is CBI officer. A parcel in your name has drugs. You are under "
                    "digital arrest. Transfer Rs 50,000 to RBI safe account today.") == "SCAM"
    assert _verdict("Congratulations! You won KBC lottery of 25 lakh. Pay Rs 15,000 "
                    "processing fee to claim. WhatsApp 9123456789") == "SCAM"
    assert _verdict("Dear customer your SBI account will be blocked today. Update PAN KYC "
                    "by calling 9876543210 immediately.") == "SCAM"
    assert _verdict("Share the OTP sent to your mobile with our executive at 9876543210 "
                    "to stop your card being blocked.") == "SCAM"
    assert _verdict("Aapka bijli connection aaj raat 9:30 baje kaat diya jayega. Turant "
                    "officer se 9876543210 par sampark karein.") != "NO_RED_FLAGS"


def test_real_bank_otp_alert_is_not_flagged():
    assert _verdict("Your OTP for transaction of Rs 2,500 at Amazon is 482913. "
                    "Do not share it with anyone. - HDFC Bank") == "NO_RED_FLAGS"


# ------------------------------------------------------------- images + reports

DIGITAL_ARREST = ("This is CBI officer. A parcel in your name has drugs. You are under "
                  "digital arrest. Transfer Rs 50,000 to RBI safe account today. "
                  "Call 9876543210")


def render_png(text: str, dark: bool = False) -> bytes:
    """Draw text like a phone screenshot, for OCR tests."""
    import io
    import textwrap

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=30)
    lines = textwrap.wrap(text, 40)
    bg, fg = ("#111111", "#f0f0f0") if dark else ("white", "black")
    img = Image.new("RGB", (800, 60 + 42 * len(lines)), bg)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 42 * i), line, fill=fg, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_ocr_reads_a_scam_screenshot():
    import pytest

    from kagaz.core import ocr

    if not ocr.tesseract_text(render_png("hello world test")):
        pytest.skip("tesseract not installed")
    for dark in (False, True):
        text, method = ocr.image_to_text(render_png(DIGITAL_ARREST, dark=dark))
        assert method == "ocr"
        assert "digital arrest" in " ".join(text.lower().split())
        assert verify.verify(reader.extract(text), use_llm=False).verdict == "SCAM"


def test_ocr_rejects_empty_or_huge_images():
    from kagaz.core import ocr

    assert ocr.image_to_text(b"") == ("", "")
    assert ocr.image_to_text(b"x" * (ocr.MAX_IMAGE_BYTES + 1)) == ("", "")
    assert ocr.image_to_text(b"not an image") == ("", "")


def test_scam_report_pack():
    from kagaz.core import report

    fields = reader.extract(DIGITAL_ARREST + " Pay via UPI to cbi.safe@okaxis")
    rep = verify.verify(fields, use_llm=False).to_dict()
    complaint = report.complaint_text(fields, rep, today=date(2026, 10, 22))
    assert "22 Oct 2026" in complaint
    assert "9876543210" in complaint
    assert "cbi.safe@okaxis" in complaint
    assert "digital arrest" in complaint.lower()
    warning = report.family_warning(fields, rep)
    assert "1930" in warning and "डिजिटल अरेस्ट" in warning
    assert report.scam_kind(rep) == "digital_arrest"


def test_report_button_is_parsed():
    from agents.coordinator_agent import parse_selection

    assert parse_selection('{"action": "report_scam"}')["action"] == "report_scam"
    assert parse_selection("Report this scam + warn my family")["action"] == "report_scam"
