"""Lets every Kagaz specialist be used on its own from ASI:One.

Each specialist still serves the coordinator over its internal protocol.
On top of that it speaks the Agent Chat Protocol: a person can @mention
just that agent (for example the Verify agent with a scam SMS) and get a
complete answer for that agent's job, without going through the coordinator.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    ResourceContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity

from kagaz import cards, config, security
from kagaz.core import ocr, reader, talk, verify
from kagaz.core.reader import NoticeFields

Handler = Callable[[Context, str, str, str], Awaitable[str]]

FOOTER = ("\n\n---\n_Part of the Kagaz AI team. For the full flow (scam check, "
          "explanation, reply draft, reminders, expert review) chat with **@kagaz**._")


# ASI:One / Agentverse put the agent's @handle or @address in front of the
# message (sometimes in bold). It is not part of the notice, so drop it.
MENTION_RE = re.compile(r"(?<!\S)\*{0,2}@[A-Za-z0-9_\-]+\*{0,2}")


def strip_mentions(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", MENTION_RE.sub("", text)).strip()


def chat_text(msg: ChatMessage) -> str:
    return strip_mentions(" ".join(c.text for c in msg.content if isinstance(c, TextContent)))


def image_text(item: ResourceContent, identity: Identity) -> str:
    """Download an uploaded image and read its text (OCR, with LLM vision fallback)."""
    from agents import coordinator_agent  # same download logic as the coordinator

    data, mime = coordinator_agent.download_image(item, identity)
    mime = (mime or "image/png").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        raise ValueError("not an image")
    text, _ = ocr.image_to_text(data, mime)
    return text


def chat_protocol(role: str, welcome: str, handler: Handler, images: bool = True,
                  min_chars: int = 12) -> Protocol:
    """Agent Chat Protocol for a specialist: ack, read text or image, run the job, reply."""
    from agents.coordinator_agent import detect_language, strip_language_request

    chat = Protocol(spec=chat_protocol_spec)
    identity = Identity.from_seed(config.seed_for(role), 0)
    limiter = security.RateLimiter()

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc),
                                                   acknowledged_msg_id=msg.msg_id))
        if images and any(isinstance(c, StartSessionContent) for c in msg.content):
            await ctx.send(sender, ChatMessage(
                timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                content=[MetadataContent(type="metadata", metadata={"attachments": "true"})]))
        if not limiter.allow(sender):
            if limiter.should_warn(sender):
                await ctx.send(sender, cards.text_message(security.SLOW_DOWN))
            return
        text = security.clip(chat_text(msg))
        # Hindi when the message is in Hindi or asks for it; each message stands alone.
        language = detect_language(text, "en")
        text = strip_language_request(text)
        note = ""
        resources = [c for c in msg.content if isinstance(c, ResourceContent)]
        if images and resources:
            try:
                read = await asyncio.to_thread(image_text, resources[0], identity)
            except Exception:
                ctx.logger.exception("Image read failed")
                read = ""
            if len(read) < ocr.MIN_USEFUL_CHARS:
                await ctx.send(sender, cards.text_message(
                    "I couldn't read enough text in that image. Please send a clearer, "
                    "straight photo, or paste the text."))
                return
            preview = read if len(read) <= 300 else read[:300] + " …"
            note = "📷 I read this from your image:\n> " + preview.replace("\n", "\n> ") + "\n\n"
            text = (text + "\n" + read).strip()
        if not note and role != "marketplace":
            if talk.is_greeting(text):
                await ctx.send(sender, cards.text_message(talk.greet(role, welcome, language)))
                return
            if talk.is_question(text):
                reply = await asyncio.to_thread(talk.answer, text, role, language)
                await ctx.send(sender, cards.text_message((reply or welcome) + FOOTER))
                return
        if len(text) < min_chars:
            await ctx.send(sender, cards.text_message(welcome))
            return
        try:
            reply = await handler(ctx, sender, text, language)
        except Exception:
            ctx.logger.exception(f"{role} chat failed")
            reply = "Sorry, something went wrong on my side. Please send it again."
        await ctx.send(sender, cards.text_message(note + reply + FOOTER))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    return chat


# ---------------------------------------------------------------- formatting

def scam_warning(fields: NoticeFields) -> str:
    """One-line warning to put on top of a specialist's answer if the text looks fake."""
    report = verify.verify(fields)
    if report.verdict == "SCAM":
        return (f"🚨 **Warning: this looks like a SCAM** (risk {report.score}/100). Don't click "
                "links, pay or share OTPs. Send it to the Kagaz Verify agent for the full "
                "scam report.\n\n")
    if report.verdict == "SUSPICIOUS":
        return (f"⚠️ _Some warning signs found (risk {report.score}/100). Check it on the "
                "official portal before acting._\n\n")
    return ""


def format_fields(fields: NoticeFields) -> str:
    d = fields.to_dict()
    rows = [
        ("Notice", d.get("title") or "Official notice"),
        ("From", d.get("issuer") or "Not stated"),
        ("To", d.get("recipient") or ""),
        ("Amount", d.get("amount") or "None stated"),
        ("Deadline", cards._deadline_text(d)),
        ("Reference no.", ", ".join(d.get("references") or [])),
        ("Links", ", ".join(d.get("urls") or [])),
        ("Phone numbers", ", ".join(d.get("phones") or [])),
    ]
    lines = ["📄 **Here is what this notice says**", ""]
    lines += [f"- **{k}:** {v}" for k, v in rows if v]
    if d.get("summary"):
        lines += ["", f"**In one line:** {d['summary']}"]
    return "\n".join(lines)


def format_explanation(exp: dict) -> str:
    hi = exp.get("language") == "hi"
    law = f" ({exp['law']})" if exp.get("law") else ""
    parts = [f"📘 **{exp['title']}**{law}", exp["plain_explanation"]]
    if exp.get("days_left") is not None:
        left = exp["days_left"]
        parts.append(("⏳ **दिन बाकी:** " if hi else "⏳ **Days left:** ")
                     + (str(left) if left >= 0 else f"deadline passed {abs(left)} days ago"))
    parts.append(("**आपके अधिकार**" if hi else "**Your rights**") + "\n"
                 + "\n".join(f"- {r}" for r in exp.get("rights", [])))
    parts.append(("**क्या करें**" if hi else "**What to do**") + "\n"
                 + "\n".join(f"{i}. {s}" for i, s in enumerate(exp.get("steps", []), 1)))
    if exp.get("portals"):
        parts.append(("**सरकारी लिंक:** " if hi else "**Official links:** ")
                     + ", ".join(exp["portals"]))
    if exp.get("escalate"):
        parts.append(("🧑‍⚖️ **विशेषज्ञ की सलाह लें:** " if hi else "🧑‍⚖️ **Get an expert to check:** ")
                     + exp.get("escalate_reason", ""))
    parts.append("_General information, not legal advice._")
    return "\n\n".join(parts)


def format_plan(exp: dict, plan: dict, scam: bool) -> str:
    hi = exp.get("language") == "hi"
    parts = [f"📝 **Action plan: {exp['title']}**"]
    parts.append(("**क्या करें**" if hi else "**Steps**") + "\n"
                 + "\n".join(f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)))
    if plan.get("deadline"):
        parts.append(f"**Deadline:** {plan['deadline']}")
    if plan.get("reminders"):
        parts.append("**Suggested reminder dates:** " + ", ".join(plan["reminders"]))
    if not scam:
        parts.append("**Your draft reply** (fill in the [brackets]):\n```\n"
                     + plan["draft"].strip() + "\n```")
    if plan.get("portals"):
        parts.append("**Official links:** " + ", ".join(plan["portals"]))
    parts.append("_General information, not legal advice._")
    return "\n\n".join(parts)


def read_fields(text: str) -> NoticeFields:
    """Fast, rule-based reading (no LLM) for agents that only need the notice type and facts."""
    fields = reader.extract_rule_based(text)
    fields.raw_text = fields.raw_text or text
    return fields
