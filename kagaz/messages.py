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
