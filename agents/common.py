"""Shared agent factory so every Kagaz agent is configured the same way."""

import os
from pathlib import Path

from uagents import Agent, Context

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
        # Short @name on ASI:One/Agentverse (e.g. @kagazverify); the
        # coordinator keeps the @kagaz handle set by hand.
        handle=(spec.get("handle") if config.USE_HANDLES else None),
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
    agent = Agent(**kwargs)
    if use_mailbox and config.AGENTVERSE_API_KEY and should_auto_connect(role):
        @agent.on_event("startup")
        async def _connect(ctx: Context):
            await connect_mailbox(agent, ctx)
    return agent


def should_auto_connect(role: str) -> bool:
    # The coordinator's mailbox already exists and works, so leave it alone
    # unless asked (KAGAZ_CONNECT_COORDINATOR=true).
    if role == "coordinator":
        return os.getenv("KAGAZ_CONNECT_COORDINATOR", "false").lower() == "true"
    return os.getenv("KAGAZ_AUTO_CONNECT", "true").lower() == "true"


async def connect_mailbox(agent: Agent, ctx: Context) -> bool:
    """Create (or refresh) this agent's Agentverse mailbox and profile, like
    clicking Connect -> Mailbox in the inspector. Never raises."""
    try:
        from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
        from uagents_core.registration import RegistrationRequest

        async def register(handle):
            registration = RegistrationRequest(
                address=agent.address, name=agent.name, handle=handle,
                url=None, profile=agent._build_registration_profile(),
                endpoints=agent._endpoints, protocols=list(agent.protocols.keys()),
                metadata=agent.metadata)
            return await register_in_agentverse(
                request=AgentverseConnectRequest(user_token=config.AGENTVERSE_API_KEY,
                                                 agent_type="mailbox"),
                identity=agent._identity, prefix=agent._prefix,
                agentverse=agent._agentverse, agent_details=registration)

        handle = getattr(agent, "_handle", None)
        result = await register(handle)
        if not result.success and handle:
            # The @handle may already belong to another agent; connect without it.
            ctx.logger.warning(f"Could not claim @{handle} ({result.detail}); "
                               "connecting without a handle")
            handle = None
            result = await register(None)
        if result.success:
            ctx.logger.info("Agentverse mailbox connected" + (f" as @{handle}" if handle else ""))
        else:
            ctx.logger.warning(f"Agentverse mailbox not connected: {result.detail}")
        return bool(result.success)
    except Exception as err:
        ctx.logger.warning(f"Agentverse mailbox connect failed: {err}")
        return False
