"""Placeholder answers for agents a teammate has not merged yet.

Each placeholder module keeps the real agent's function names and arguments, so app/graph.py runs
unchanged. When the real agent lands, its file replaces the placeholder and nothing else changes.
"""

from app.agents.base import result

OWNERS = {
    "policy_agent": "Person 1", "document_agent": "Person 1", "billing_agent": "Person 1",
    "care_agent": "Person 3", "appointment_agent": "Person 3", "directory_agent": "Person 3",
}


def placeholder(agent, topic):
    return result(f"Answers about {topic} aren't available in this build yet. Please contact the hospital help desk "
                  f"for now.", "not_found", [],
                  f"{agent} is a placeholder on this branch ({OWNERS.get(agent, 'teammate')} owns it).",
                  rephrase=False)
