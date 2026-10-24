"""The request workflow as a LangGraph state graph.

    guard -> route -> {policy | appointment | lab | insurance | billing | medication | care | document} -> verify -> audit
      |                                                                                                  ^
      +-- emergency / injection / other patient / medication change / confirmation ---------------------+

Code decides (guard, tools, verifier); the LLM only helps route unclear questions and phrase answers.
"""

import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app import audit, guard, llm
from app.agents import (appointment, billing, care, directory, documents, insurance, lab, medication, policy,
                        router, safety)
from app.agents.base import refused
from app.auth import PATIENT_DATA_ROLES
from app.tools import AccessDenied
from app.verifier import verify

AGENT_OF = {
    "policy": "policy_agent", "department": "policy_agent", "help": "policy_agent",
    "appointments_view": "appointment_agent", "reschedule": "appointment_agent", "book": "appointment_agent",
    "lab": "lab_agent", "reports": "lab_agent",
    "insurance": "insurance_agent", "cost": "insurance_agent",
    "billing": "billing_agent", "refund": "billing_agent",
    "medication": "medication_agent",
    "care_plan": "care_agent", "readiness": "care_agent", "preop_pending": "care_agent",
    "document": "document_agent",
}


class State(TypedDict, total=False):
    question: str
    session: Any
    tools: Any
    as_of: Any
    pending: dict
    stats: dict
    guard: dict
    intent: str
    route_method: str
    agents: list
    response: dict
    verifier: dict
    trace_id: str
    started: float
    latency_ms: int


# ---------------------------------------------------------------- nodes

def guard_node(state):
    session, q = state["session"], state["question"]
    g = guard.inspect(q, session)
    out = {"guard": g, "agents": ["guard"]}
    other = g["other_patient"]
    if g["emergency"]:
        out.update(intent="emergency", response=safety.emergency(g["emergency"]))
    elif g["injection"]:
        out.update(intent="injection", response=safety.injection_refusal())
    elif other["other_patient_ids"] or other["other_patient_names"]:
        out.update(intent="privacy", response=safety.privacy_refusal(other, session))
    elif g["medication_change"]:
        out.update(intent="medication_change", response=safety.medication_change(state["tools"], session))
    elif state.get("pending") and appointment.parse_confirmation(q) is not None:
        out.update(intent="confirm")
    return out


def route_node(state):
    session, g = state["session"], state["guard"]
    drugs = state["tools"].find_drugs(state["question"])
    intent, method = router.route(state["question"], drugs, stats=state["stats"])
    out = {"intent": intent, "route_method": method, "agents": state["agents"] + [f"router:{method}"]}
    if intent in router.PERSONAL_INTENTS:
        if session.role not in PATIENT_DATA_ROLES:
            out["response"] = safety.staff_refusal()
        elif g["other_patient"]["third_party"] and session.role == "patient":
            out["response"] = safety.privacy_refusal(g["other_patient"], session)
    return out


def _agent(name, fn):
    def node(state):
        try:
            response = fn(state)
        except AccessDenied:
            response = safety.privacy_refusal({"third_party": []}, state["session"])
        return {"response": response, "agents": state["agents"] + [name]}
    return node


def _pid(state):
    return state["session"].subject_patient_id


def policy_fn(state):
    if state["intent"] == "department":
        return directory.department(state["tools"], state["question"])
    if state["intent"] == "help":
        return directory.help_text(state["session"])
    return policy.answer(state["question"], as_of=state.get("as_of"))


def appointment_fn(state):
    t, q, i = state["tools"], state["question"], state["intent"]
    if i == "reschedule":
        dept = next((d["department_name"] for d in t.departments() if d["department_name"].lower() in q.lower()), None)
        return appointment.reschedule(t, _pid(state), q, department=dept)
    if i == "book":
        return appointment.book_missed_care_item(t, _pid(state), q)
    return appointment.view(t, _pid(state))


def confirm_fn(state):
    option = appointment.parse_confirmation(state["question"])
    if option == 0:
        from app.agents.base import result
        return result("Okay, I haven't changed anything.", "clarification_needed", [], "Action cancelled by the patient.",
                      rephrase=False)
    return appointment.execute(state["tools"], state["pending"], option)


def lab_fn(state):
    if state["intent"] == "reports":
        return lab.reports(state["tools"], _pid(state))
    return lab.answer(state["tools"], _pid(state), state["question"], proxy=state["session"].is_proxy)


def insurance_fn(state):
    return insurance.answer(state["tools"], _pid(state), state["question"], cost=state["intent"] == "cost")


def billing_fn(state):
    if state["intent"] == "refund":
        return billing.refund(state["tools"], _pid(state))
    return billing.balance(state["tools"], _pid(state))


def medication_fn(state):
    return medication.answer(state["tools"], _pid(state), state["question"])


def care_fn(state):
    t, q, i = state["tools"], state["question"], state["intent"]
    if i == "readiness":
        return care.readiness(t, _pid(state), q)
    if i == "preop_pending":
        return care.pending_tests(t, _pid(state), q)
    return care.care_plan(t, _pid(state))


def document_fn(state):
    return documents.summarise(state["tools"], _pid(state), state["question"], stats=state["stats"])


def verify_node(state):
    response = state.get("response") or refused("I can't help with that request.")
    final, report = verify(state["question"], response, state["tools"], state["session"], stats=state["stats"])
    return {"response": final, "verifier": report, "agents": state["agents"] + ["verifier"]}


def audit_node(state):
    latency = int((time.perf_counter() - state["started"]) * 1000)
    stats = state["stats"]
    audit.record(state["trace_id"], state["session"], state["question"], state.get("intent", ""), state["agents"],
                 state["response"], state["tools"], state.get("guard", {}), state.get("verifier", {}), latency,
                 llm.model_name() if stats.get("llm_calls") else "none (deterministic)", stats.get("llm_calls", 0))
    return {"latency_ms": latency}


# ---------------------------------------------------------------- wiring

def _after_guard(state):
    if state.get("response"):
        return "verify"
    return "confirm" if state.get("intent") == "confirm" else "route"


def _after_route(state):
    return "verify" if state.get("response") else AGENT_OF.get(state["intent"], "policy_agent")


def build_graph():
    g = StateGraph(State)
    g.add_node("guard", guard_node)
    g.add_node("route", route_node)
    g.add_node("confirm", _agent("appointment_agent:confirm", confirm_fn))
    agents = {"policy_agent": policy_fn, "appointment_agent": appointment_fn, "lab_agent": lab_fn,
              "insurance_agent": insurance_fn, "billing_agent": billing_fn, "medication_agent": medication_fn,
              "care_agent": care_fn, "document_agent": document_fn}
    for name, fn in agents.items():
        g.add_node(name, _agent(name, fn))
        g.add_edge(name, "verify")
    g.add_node("verify", verify_node)
    g.add_node("audit", audit_node)
    g.set_entry_point("guard")
    g.add_conditional_edges("guard", _after_guard, {"verify": "verify", "confirm": "confirm", "route": "route"})
    g.add_conditional_edges("route", _after_route, {**{n: n for n in agents}, "verify": "verify"})
    g.add_edge("confirm", "verify")
    g.add_edge("verify", "audit")
    g.add_edge("audit", END)
    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
