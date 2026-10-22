"""Messages the Kagaz agents exchange with each other.

The coordinator talks to users through the Agent Chat Protocol; these models
are the internal contract between the coordinator and its specialists.
Rich results travel as JSON strings so every model stays simple and stable.
"""

from uagents import Model


class ReadRequest(Model):
    notice_text: str


class ReadResult(Model):
    ok: bool
    fields_json: str  # NoticeFields as JSON
    error: str = ""


class VerifyRequest(Model):
    notice_text: str
    fields_json: str = "{}"


class VerifyResult(Model):
    ok: bool
    report_json: str  # VerifyReport as JSON
    error: str = ""


class RulesRequest(Model):
    fields_json: str
    language: str = "en"


class RulesResult(Model):
    ok: bool
    explanation_json: str
    error: str = ""


class ActionRequest(Model):
    fields_json: str
    explanation_json: str
    user_name: str = ""
    language: str = "en"


class ActionResult(Model):
    ok: bool
    plan_json: str
    error: str = ""


class ExpertReviewRequest(Model):
    case_id: str
    fields_json: str
    draft_text: str


class ExpertReviewResult(Model):
    case_id: str
    ok: bool
    review_text: str
    reviewer: str = ""
    error: str = ""


# ---------------------------------------------------------------- expert marketplace
# Verified CAs and lawyers run their own Expert agent (agents/expert_agent.py),
# register with the Kagaz Marketplace agent, and get paid per job through the
# Agent Payment Protocol. The coordinator never talks to experts directly.


class RegisterExpert(Model):
    name: str
    profession: str             # "CA" or "Lawyer" (the built-in desk uses "AI desk")
    credential_id: str          # ICAI membership no. / Bar Council enrolment no.
    specialties: list[str]      # notice types, e.g. ["gst_notice", "it_143_1"] or ["all"]
    fee: str                    # per review, in the marketplace currency (FET)
    languages: list[str] = ["en"]
    ai_assisted: bool = False


class RegisterExpertAck(Model):
    ok: bool
    status: str                 # approved | pending | rejected
    reason: str = ""


class ExpertJobRequest(Model):
    case_id: str
    notice_type: str
    fields_json: str
    draft_text: str
    budget: str                 # most the marketplace may pay the expert


class ExpertJobResult(Model):
    case_id: str
    ok: bool
    review_text: str = ""
    reviewer: str = ""
    payout: str = ""
    tx: str = ""
    error: str = ""
