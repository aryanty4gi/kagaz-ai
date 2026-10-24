"""Run all seven Kagaz agents in one process.

    python run_local.py

Every agent connects to its own Agentverse mailbox (so ASI:One can reach the
coordinator and each specialist on its own) unless KAGAZ_USE_MAILBOX=false.
Set KAGAZ_SPECIALIST_MAILBOX=false to keep the specialists local-only.
Inside this process the coordinator reaches the specialists directly.
For one process per agent, run each file in agents/ separately instead.
"""

import os

from uagents import Bureau

from agents import (
    action_agent, coordinator_agent, expert_agent, marketplace_agent, reader_agent, rules_agent,
    verify_agent,
)
from kagaz import config


def build_all():
    mailbox = config.USE_MAILBOX
    solo = mailbox and config.SPECIALIST_MAILBOX
    return [
        coordinator_agent.build(mailbox),
        reader_agent.build(solo),
        verify_agent.build(solo),
        rules_agent.build(solo),
        action_agent.build(solo),
        expert_agent.build(solo),
        marketplace_agent.build(solo),
    ]


if __name__ == "__main__":
    from run_all import start_cloud_helpers

    start_cloud_helpers()
    agents = build_all()
    for a in agents:
        print(f"{a.name:<20} {a.address}")
    bureau = Bureau(agents=agents, port=int(os.getenv("KAGAZ_BUREAU_PORT", "8000")))
    bureau.run()
