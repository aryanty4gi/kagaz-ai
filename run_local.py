"""Run all six Kagaz agents in one process.

    python run_local.py

The coordinator connects to its Agentverse mailbox (so ASI:One can reach it)
unless KAGAZ_USE_MAILBOX=false. Specialists talk to the coordinator in-process.
For one process per agent, run each file in agents/ separately instead.
"""

import os

from uagents import Bureau

from agents import action_agent, coordinator_agent, expert_agent, reader_agent, rules_agent, verify_agent
from kagaz import config


def build_all():
    mailbox = config.USE_MAILBOX
    return [
        coordinator_agent.build(mailbox),
        reader_agent.build(False),
        verify_agent.build(False),
        rules_agent.build(False),
        action_agent.build(False),
        expert_agent.build(False),
    ]


if __name__ == "__main__":
    from run_all import start_cloud_helpers

    start_cloud_helpers()
    agents = build_all()
    for a in agents:
        print(f"{a.name:<20} {a.address}")
    bureau = Bureau(agents=agents, port=int(os.getenv("KAGAZ_BUREAU_PORT", "8000")))
    bureau.run()
