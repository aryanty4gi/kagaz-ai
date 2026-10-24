"""Casual chat: greetings and general questions, not a notice.

"hi" gets a friendly hello; "can my landlord cut my electricity?" gets a short
answer from the ASI:One LLM, with a fallback when the LLM is offline.
"""

import re

from kagaz import llm

GREETING = re.compile(
    r"^\s*(hi+|hello+|hey+|hii+|namaste|namaskar|good (?:morning|afternoon|evening)|"
    r"thanks?|thank you|kaise ho|kya haal|who are you|what can you do|help)"
    r"\b[\s!.?🙏👋]*$", re.IGNORECASE)
QUESTION_START = re.compile(
    r"^\s*(what|how|why|when|where|which|who|can|could|should|is|are|do|does|will|"
    r"kya|kaise|kab|kyun|kyon|kitna|kaun|mujhe|mera|meri|agar|if)\b", re.IGNORECASE)

ROLE_HINT = {
    "coordinator": "Kagaz AI, a friendly assistant for Indian citizens who get official notices "
                   "(electricity, income tax, GST, bank, court, traffic challans, school fees) "
                   "or suspicious SMS",
    "expert": "the Kagaz expert desk, an experienced Indian CA and lawyer assistant",
    "rules": "the Kagaz Rules agent, who explains Indian laws and citizens' rights simply",
    "action": "the Kagaz Action agent, who tells people the practical next steps",
    "verify": "the Kagaz Verify agent, an expert on Indian SMS, UPI and phone scams",
    "reader": "the Kagaz Reader agent, who reads notices and pulls out the key facts",
}


def is_greeting(text: str) -> bool:
    return bool(GREETING.match(text or ""))


def is_question(text: str) -> bool:
    """A general question in the user's own words (not a pasted notice)."""
    text = (text or "").strip()
    if not 6 <= len(text) <= 300 or re.search(
            r"https?://|www\.|bit\.ly|\b\d{10}\b|₹|\brs\.?\s?\d|\binr\s?\d", text, re.IGNORECASE):
        return False
    return text.endswith("?") or bool(QUESTION_START.match(text))


def greet(role: str, welcome: str, language: str = "en") -> str:
    hello = "नमस्ते! 🙏 " if language == "hi" else "Hi! 👋 "
    return hello + welcome


def answer(question: str, role: str, language: str = "en") -> str | None:
    """Short, safe general answer, or None if the LLM is unavailable."""
    lang = "Reply in simple Hindi (Devanagari)." if language == "hi" else \
        "Reply in simple English (if the user writes Hinglish, you may reply in Hinglish)."
    system = (
        f"You are {ROLE_HINT.get(role, ROLE_HINT['coordinator'])}. Answer the user's general "
        "question in under 120 words, with short bullet points if helpful. Be warm and "
        "practical. Use Indian law and official portals (cybercrime.gov.in, helpline 1930, "
        "incometax.gov.in, gst.gov.in, sancharsaathi.gov.in) where relevant. Never ask for an "
        "OTP, PIN or password. Don't invent section numbers you are unsure of. End with one "
        "line inviting them to paste their notice or SMS, or upload a photo, for an exact "
        f"check. {lang} Add: General information, not legal advice.")
    reply = llm.complete(system, question, max_tokens=400)
    return reply or None
