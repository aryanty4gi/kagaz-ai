"""Shared agent factory so every Kagaz agent is configured the same way."""

import os
from pathlib import Path

from uagents import Agent

from kagaz import config

README_DIR = Path(__file__).parent / "readmes"
# Agents re-publish their Agentverse profile on every start, so the avatar must
# come from code; otherwise each restart clears an avatar uploaded by hand.
# Team-chosen avatar (uploaded on Agentverse). assets/kagaz-avatar.png is a spare
# logo: set KAGAZ_AVATAR_URL to its raw GitHub URL to use it instead.
DEFAULT_AVATAR_URL = (
    "https://storage.googleapis.com/agentverse-prod-assets/agent-avatars-pub/1189459575.jpg"
)


def make_agent(role: str, description: str, mailbox: bool | None = None,
               name: str | None = None, seed: str | None = None) -> Agent:
    spec = config.AGENTS[role]
    use_mailbox = config.USE_MAILBOX if mailbox is None else mailbox
    readme = README_DIR / f"{role}.md"
    kwargs = dict(
        name=name or spec["name"],
        seed=seed or config.seed_for(role),
        port=spec["port"],
        description=description,
        readme_path=str(readme) if readme.exists() else None,
        publish_agent_details=True,
        avatar_url=(os.getenv(f"KAGAZ_{role.upper()}_AVATAR_URL")
                    or os.getenv("KAGAZ_AVATAR_URL") or DEFAULT_AVATAR_URL),
        # Coordinator waits for specialists inside a handler; concurrent
        # handling lets those replies arrive while it is waiting.
        handle_messages_concurrently=True,
    )
    if use_mailbox:
        kwargs["mailbox"] = True
    else:
        kwargs["endpoint"] = [f"http://127.0.0.1:{spec['port']}/submit"]
    return Agent(**kwargs)
