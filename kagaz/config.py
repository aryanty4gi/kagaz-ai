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

# Turn off live internet lookups (domain age checks) for offline tests.
ONLINE_CHECKS = os.getenv("KAGAZ_ONLINE_CHECKS", "true").lower() == "true"

# Price of an expert review, paid agent-to-agent by the coordinator.
EXPERT_FEE_AMOUNT = os.getenv("KAGAZ_EXPERT_FEE", "0.5")
EXPERT_FEE_CURRENCY = os.getenv("KAGAZ_EXPERT_CURRENCY", "FET")
# Price shown to the user, in rupees, for the same review.
EXPERT_FEE_INR = os.getenv("KAGAZ_EXPERT_FEE_INR", "99")

AGENTS = {
    "coordinator": {"name": "kagaz_coordinator", "port": 8100},
    "reader": {"name": "kagaz_reader", "port": 8101},
    "verify": {"name": "kagaz_verify", "port": 8102},
    "rules": {"name": "kagaz_rules", "port": 8103},
    "action": {"name": "kagaz_action", "port": 8104},
    "expert": {"name": "kagaz_expert", "port": 8105},
}


def seed_for(role: str) -> str:
    env_key = f"KAGAZ_{role.upper()}_SEED"
    return os.getenv(env_key, f"kagaz-ai-{role}-dev-seed-change-me")


def address_for(role: str) -> str:
    return Identity.from_seed(seed_for(role), 0).address
