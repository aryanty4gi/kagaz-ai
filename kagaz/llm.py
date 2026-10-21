"""Thin wrapper around the ASI:One LLM (OpenAI-compatible API).

Every caller passes a fallback so the agents keep working, with reduced
polish, if the LLM is slow, rate-limited or unreachable.
"""

import contextvars
import json
import logging
import re
from typing import Any

from kagaz import config

log = logging.getLogger("kagaz.llm")

_client = None

# Set while running a fallback, so a slow LLM is never called twice in a row.
OFFLINE = contextvars.ContextVar("kagaz_llm_offline", default=False)


def _get_client():
    global _client
    if _client is None and config.ASI1_API_KEY:
        from openai import OpenAI

        _client = OpenAI(
            base_url=config.ASI1_BASE_URL,
            api_key=config.ASI1_API_KEY,
            # One try, no retry: a retry doubled the wait past the coordinator's
            # specialist timeout, so slow LLM replies always fell back.
            timeout=15,
            max_retries=0,
        )
    return _client


def available() -> bool:
    return _get_client() is not None


def complete(system: str, user: str, max_tokens: int = 1200) -> str | None:
    """Return the model's text, or None if the call failed."""
    client = _get_client()
    if client is None or OFFLINE.get():
        return None
    try:
        resp = client.chat.completions.create(
            model=config.ASI1_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as err:  # network, auth, rate limit
        log.warning("ASI:One call failed: %s", err)
        return None


def complete_json(system: str, user: str, max_tokens: int = 1200) -> dict[str, Any] | None:
    """Ask for JSON and parse it, tolerating code fences and stray prose."""
    text = complete(
        system + "\nReply with a single JSON object only. No prose, no code fences.",
        user,
        max_tokens,
    )
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        log.warning("ASI:One returned invalid JSON")
        return None
