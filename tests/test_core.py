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
