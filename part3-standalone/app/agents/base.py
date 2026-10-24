"""Shared helpers for agents. An agent returns a plain dict that the verifier checks:

    {"answer", "answer_type", "citations", "explanation", "data", "pending_action", "notes"}
"""

from app.fmt import doc_citation
from app.kb.store import get_kb

ANSWER_TYPES = {"retrieved_fact", "calculated", "action_completed", "clarification_needed",
                "not_found", "refused", "conflict_flagged"}
NOT_FOUND_MESSAGE = "I could not find this information in the authorised hospital sources."


def result(answer, answer_type, citations=None, explanation="", data=None, pending_action=None, notes=None,
           rephrase=True):
    assert answer_type in ANSWER_TYPES, answer_type
    return {"answer": answer.strip(), "answer_type": answer_type, "citations": citations or [],
            "explanation": explanation, "data": data or {}, "pending_action": pending_action,
            "notes": notes or [], "rephrase": rephrase}


def not_found(explanation="No authorised source contains an answer.", citations=None, data=None):
    return result(NOT_FOUND_MESSAGE, "not_found", citations, explanation, data=data, rephrase=False)


def refused(answer, citations=None, explanation="Request is unauthorised or unsafe."):
    return result(answer, "refused", citations, explanation, rephrase=False)


def policy_citations(section_ids, role="supporting"):
    """Citations for clauses by id, using the version in force (so a superseded clause is never cited)."""
    found = {c["section_id"]: c for c in get_kb().usable_sections(section_ids)}
    return [doc_citation(found[s], role) for s in dict.fromkeys(section_ids) if s in found]


def policy_text(section_id):
    found = get_kb().usable_sections([section_id])
    return found[0]["text"] if found else ""
