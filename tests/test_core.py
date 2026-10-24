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


# ------------------------------------------------------------- new notice types

def test_new_notice_types():
    expected = {
        "06_gst_asmt10.txt": ("gst_notice", "₹48,600"),
        "07_court_summons.txt": ("court_summons", "₹240,000"),
        "08_school_fee.txt": ("school_fee", "₹18,500"),
    }
    for name, (ntype, amount) in expected.items():
        fields, report = check(name)
        assert fields.notice_type == ntype, name
        assert fields.amount == amount, name
        assert fields.deadline, name
        assert report.verdict == "NO_RED_FLAGS", name
        plan = action.build_plan(fields, rules.explain(fields))
        assert plan["draft"] and plan["steps"], name
    court, _ = check("07_court_summons.txt")
    assert rules.explain(court)["escalate"] is True
    assert knowledge.classify("You won a lottery, pay processing fee") == "unknown"


# ------------------------------------------------------------- marketplace + gateway

def _reg(**kw):
    from kagaz.messages import RegisterExpert

    base = dict(name="A. Gupta", profession="CA", credential_id="123456",
                specialties=["gst_notice"], fee="0.4")
    base.update(kw)
    return RegisterExpert(**base)


def test_expert_credentials_are_checked(monkeypatch):
    from agents import marketplace_agent as m
    from kagaz import config

    monkeypatch.setattr(config, "AUTO_APPROVE_EXPERTS", False)
    monkeypatch.setattr(config, "VERIFIED_EXPERTS", {"agent1qverified"})
    assert m.validate(_reg(), "agent1qnew")[0] == "pending"
    assert m.validate(_reg(), "agent1qverified")[0] == "approved"
    assert m.validate(_reg(credential_id="12AB"), "agent1qnew")[0] == "rejected"
    assert m.validate(_reg(profession="Lawyer", credential_id="D/1234/2015"),
                      "agent1qverified")[0] == "approved"
    assert m.validate(_reg(profession="Astrologer"), "agent1qverified")[0] == "rejected"
    # Nobody can pretend to be the built-in AI desk.
    assert m.validate(_reg(ai_assisted=True), "agent1qnew")[0] == "rejected"
    assert m.validate(_reg(ai_assisted=True), config.address_for("expert"))[0] == "approved"


def test_marketplace_picks_matching_expert_within_budget():
    from agents import marketplace_agent as m
    from kagaz import config

    experts = {
        "a1": {"status": "approved", "ai_assisted": False, "fee": "0.4",
               "specialties": ["gst_notice"], "active": 1, "done": 9},
        "a2": {"status": "approved", "ai_assisted": False, "fee": "0.3",
               "specialties": ["all"], "active": 0, "done": 2},
        "a3": {"status": "pending", "ai_assisted": False, "fee": "0.1",
               "specialties": ["all"], "active": 0, "done": 0},
        "a4": {"status": "approved", "ai_assisted": False, "fee": "0.9",
               "specialties": ["gst_notice"], "active": 0, "done": 50},
    }
    assert m.pick_expert(experts, "gst_notice", 0.5) == "a2"          # least busy
    assert m.pick_expert(experts, "gst_notice", 0.5, {"a2"}) == "a1"
    assert m.pick_expert(experts, "gst_notice", 0.5, {"a1", "a2"}) == config.address_for("expert")
    label = m.reviewer_label({"name": "A. Gupta", "profession": "CA", "credential_id": "123456"})
    assert "verified" in label and "123456" not in label


def test_gateway_demo_mode_and_amounts(monkeypatch):
    from kagaz import payments

    monkeypatch.setattr(payments, "STRIPE_SECRET_KEY", "")
    assert payments.enabled() is False
    assert payments.amount_minor("99") == 9900
    assert payments.demo_transaction_id().startswith("demo-")
    assert payments.refund("") is False


def test_new_agents_get_private_derived_seeds(monkeypatch):
    from kagaz import config

    monkeypatch.setenv("KAGAZ_COORDINATOR_SEED", "secret-coordinator-seed")
    monkeypatch.delenv("KAGAZ_MARKETPLACE_SEED", raising=False)
    seed = config.seed_for("marketplace")
    assert seed != "kagaz-ai-marketplace-dev-seed-change-me" and "secret" not in seed
    monkeypatch.setenv("KAGAZ_MARKETPLACE_ADDRESS", "agent1qoverride")
    assert config.address_for("marketplace") == "agent1qoverride"


def test_stripe_checkout_payload_never_leaks_secret(monkeypatch):
    import types

    from kagaz import payments

    created = []

    class Session:
        @staticmethod
        def create(**kw):
            created.append(kw)
            n = len(created)
            return types.SimpleNamespace(id=f"cs_test_{n}", client_secret=f"cs_secret_{n}",
                                         url=f"https://checkout.stripe.com/c/pay/cs_test_{n}")

        @staticmethod
        def retrieve(sid):
            return types.SimpleNamespace(payment_status="paid" if sid == "cs_test_2" else "unpaid",
                                         payment_intent="pi_123")

    fake = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=Session), api_key="")
    monkeypatch.setattr(payments, "_stripe", lambda: fake)
    monkeypatch.setattr(payments, "STRIPE_SECRET_KEY", "sk_test_SECRET")
    monkeypatch.setattr(payments, "STRIPE_PUBLISHABLE_KEY", "pk_test_x")
    out = payments.create_checkout("case1", "agent1quser", "99", "Expert review")
    assert created[0]["ui_mode"] == "embedded"
    assert created[0]["line_items"][0]["price_data"]["unit_amount"] == 9900
    assert out["stripe"]["client_secret"] == "cs_secret_1"
    assert out["hosted_url"].startswith("https://checkout.stripe.com")
    assert "sk_test_SECRET" not in str(out)
    assert payments.paid_session(out["session_ids"]) == ("cs_test_2", "pi_123")


def test_hindi_scam_sms_is_caught():
    text = ("प्रिय उपभोक्ता, आपका बिजली कनेक्शन आज रात 9:30 बजे काट दिया जाएगा। "
            "तुरंत बिजली अधिकारी से 9876543210 पर संपर्क करें।")
    fields = reader.extract(text)
    assert fields.notice_type == "electricity_disconnection"
    assert verify.verify(fields, use_llm=False).verdict == "SCAM"


def test_next_steps_drops_used_buttons_and_speaks_hindi():
    from kagaz import cards
    expl = {"title": "Electricity disconnection notice", "escalate": False}
    plan = {"deadline": "2099-11-05", "reminders": ["2099-10-29"]}
    first = cards.next_steps(expl, plan, "99", False)
    assert [c["selection"]["action"] for c in first["ctas"]] == ["remind", "expert", "done"]
    after = cards.next_steps(expl, plan, "99", False, used=["remind"])
    assert [c["selection"]["action"] for c in after["ctas"]] == ["expert", "done"]
    assert any(c.get("primary") for c in after["ctas"])
    hi = cards.next_steps(expl, plan, "99", False, language="hi")
    assert hi["title"] == "आपके अगले कदम"
    assert "Set deadline reminders" in hi["ctas"][0]["label"]  # still matches prose clicks
    report = cards.after_report({"title": "Traffic e-challan"})
    assert [c["selection"]["action"] for c in report["ctas"]] == ["proceed", "done"]


def test_hindi_explanation_translates_steps(monkeypatch):
    from kagaz import llm
    from kagaz.core import rules

    def fake(system, user, max_tokens=1200):
        if "Translate each numbered line" in system:
            n = len(user.splitlines())
            return "\n".join(f"{i}. हिंदी कदम {i}" for i in range(1, n + 1))
        return "हिंदी में समझाया गया।"

    monkeypatch.setattr(llm, "complete", fake)
    fields = reader.extract(load("03_income_tax_143_1.txt"))
    out = rules.explain(fields, "hi")
    assert out["steps"] and all(s.startswith("हिंदी कदम") for s in out["steps"])
    assert out["rights"] and all(r.startswith("हिंदी कदम") for r in out["rights"])
    # A broken translation falls back to the English steps.
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "oops")
    assert rules.translate_lines(["Pay on incometax.gov.in"]) == ["Pay on incometax.gov.in"]


def test_short_scam_texts_are_checked_but_chit_chat_is_not():
    from agents.coordinator_agent import looks_like_notice
    for text in ["Your KYC expired, update: bit.ly/kyc9",
                 "Share OTP to get Rs 5000 cashback",
                 "You won lottery! Pay fee now",
                 "Pending e-challan, pay today",
                 "आपका बिजली कनेक्शन आज रात काटा जाएगा"]:
        assert looks_like_notice(text), text
    for text in ["hi", "hello kagaz", "what can you do?", "explain in hindi",
                 "हिंदी में समझाओ", "thanks a lot!"]:
        assert not looks_like_notice(text), text


def test_kyc_update_link_is_a_scam_but_plain_kyc_reminder_is_not():
    from kagaz.core import reader, verify
    scam = verify.verify(reader.extract_rule_based("Your KYC expired, update: bit.ly/kyc9"), use_llm=False)
    assert scam.verdict == "SCAM", scam
    plain = verify.verify(reader.extract_rule_based(
        "Dear customer, please complete your KYC at your nearest branch."), use_llm=False)
    assert plain.verdict != "SCAM"


def test_single_agent_answers_are_complete():
    """Each specialist's own answer (used when it is chatted with directly)."""
    from agents import solo
    from kagaz.core import action, rules

    elec = (SAMPLES / "04_electricity_disconnection.txt").read_text()
    fields = solo.read_fields(elec)
    assert "₹6,240" in solo.format_fields(fields)
    exp = rules.explain(fields, "en")
    text = solo.format_explanation(exp)
    assert "Your rights" in text and "What to do" in text
    plan = action.build_plan(fields, exp)
    assert "Your draft reply" in solo.format_plan(exp, plan, scam=False)
    assert "Your draft reply" not in solo.format_plan(exp, plan, scam=True)
    assert solo.scam_warning(fields) == ""
    fake = solo.read_fields((SAMPLES / "01_fake_echallan_sms.txt").read_text())
    assert solo.scam_warning(fake).startswith("🚨")


def test_mailbox_auto_connect_skips_coordinator_by_default(monkeypatch):
    from agents.common import should_auto_connect

    monkeypatch.delenv("KAGAZ_CONNECT_COORDINATOR", raising=False)
    monkeypatch.delenv("KAGAZ_AUTO_CONNECT", raising=False)
    assert not should_auto_connect("coordinator")
    assert should_auto_connect("verify")
    monkeypatch.setenv("KAGAZ_AUTO_CONNECT", "false")
    assert not should_auto_connect("rules")


def test_agent_mention_is_removed_from_chat_text():
    from agents.solo import strip_mentions

    msg = "**@agent1qwuwfl3hrczx3qzmevuftewx5znkrway5533e3ah33c74led46zykkhskge** Your KYC expired, update: bit.ly/kyc9"
    assert strip_mentions(msg) == "Your KYC expired, update: bit.ly/kyc9"
    assert strip_mentions("@kagaz pay to help@okaxis now") == "pay to help@okaxis now"
