"""Central configuration for the Kagaz AI agent team.

Every agent's address is derived from its seed, so the coordinator always
knows where its specialists live without anyone copy-pasting addresses.
Set real, private seeds in .env before deploying.
"""

import os

from dotenv import load_dotenv
from uagents_core.identity import Identity

load_dotenv()

ASI1_API_KEY = os.getenv("ASI1_API_KEY", "")
ASI1_BASE_URL = os.getenv("ASI1_BASE_URL", "https://api.asi1.ai/v1")
ASI1_MODEL = os.getenv("ASI1_MODEL", "asi1")

# "true" = connect each agent to its Agentverse mailbox (needed for ASI:One).
# "false" = local-only endpoints, useful for offline testing.
USE_MAILBOX = os.getenv("KAGAZ_USE_MAILBOX", "true").lower() == "true"
# Give every specialist its own mailbox too, so each one can be chatted with
# on its own from ASI:One. Inside one process (run_local.py) the coordinator
# still reaches them directly, so this adds no delay to the main flow.
USE_HANDLES = os.getenv("KAGAZ_HANDLES", "true").lower() == "true"
SPECIALIST_MAILBOX = os.getenv("KAGAZ_SPECIALIST_MAILBOX", str(USE_MAILBOX)).lower() == "true"
# Agentverse API key (agentverse.ai -> Profile -> API Keys). When set, each
# agent creates its own Agentverse mailbox on start, so no inspector clicks.
# Pasted keys often carry a trailing newline or space, which HTTP headers reject.
AGENTVERSE_API_KEY = "".join(os.getenv("AGENTVERSE_API_KEY", "").split())

# Turn off live internet lookups (domain age checks) for offline tests.
ONLINE_CHECKS = os.getenv("KAGAZ_ONLINE_CHECKS", "true").lower() == "true"

# Price of an expert review, paid agent-to-agent by the coordinator.
EXPERT_FEE_AMOUNT = os.getenv("KAGAZ_EXPERT_FEE", "0.5")
EXPERT_FEE_CURRENCY = os.getenv("KAGAZ_EXPERT_CURRENCY", "FET")
# Price shown to the user, in rupees, for the same review.
EXPERT_FEE_INR = os.getenv("KAGAZ_EXPERT_FEE_INR", "99")

AGENTS = {
    "coordinator": {"name": "kagaz_coordinator", "port": 8100},
    "reader": {"name": "kagaz_reader", "handle": "kagazreader", "port": 8101},
    "verify": {"name": "kagaz_verify", "handle": "kagazverify", "port": 8102},
    "rules": {"name": "kagaz_rules", "handle": "kagazrules", "port": 8103},
    "action": {"name": "kagaz_action", "handle": "kagazaction", "port": 8104},
    "expert": {"name": "kagaz_expert", "handle": "kagazexpert", "port": 8105},
    "marketplace": {"name": "kagaz_marketplace", "handle": "kagazmarketplace", "port": 8106},
}


def seed_for(role: str) -> str:
    env_key = f"KAGAZ_{role.upper()}_SEED"
    if os.getenv(env_key):
        return os.environ[env_key]
    # Agents added later (e.g. the marketplace) derive a private seed from the
    # coordinator's, so an existing deployment needs no new secret.
    base = os.getenv("KAGAZ_COORDINATOR_SEED")
    if base and role != "coordinator":
        import hashlib

        return hashlib.sha256(f"{base}:{role}".encode()).hexdigest()
    return f"kagaz-ai-{role}-dev-seed-change-me"


def address_for(role: str) -> str:
    # An outside expert's machine doesn't have Kagaz's seeds, so it is told the
    # marketplace address directly (KAGAZ_MARKETPLACE_ADDRESS).
    override = os.getenv(f"KAGAZ_{role.upper()}_ADDRESS")
    if override:
        return override
    return Identity.from_seed(seed_for(role), 0).address


# Expert marketplace
PLATFORM_FEE_PERCENT = float(os.getenv("KAGAZ_PLATFORM_FEE_PERCENT", "20"))
# Comma-separated agent addresses whose credentials the Kagaz team has checked.
VERIFIED_EXPERTS = {a.strip() for a in os.getenv("KAGAZ_VERIFIED_EXPERTS", "").split(",") if a.strip()}
# Demo only: approve any expert whose credential number is well-formed.
AUTO_APPROVE_EXPERTS = os.getenv("KAGAZ_AUTO_APPROVE_EXPERTS", "false").lower() == "true"
