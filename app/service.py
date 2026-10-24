"""Entry point used by the API, the UI and the evaluation: ask(session, question) -> response JSON."""

import threading
import time

from app import audit, config, db, llm
from app.graph import get_graph
from app.tools import ToolBox

_pending = {}                 # user_id -> pending action awaiting confirmation
_pending_lock = threading.Lock()


def startup():
    db.init_db()
    from app.kb.store import get_kb

    get_kb()
    get_graph()


def ask(session, question, as_of=None):
    question = (question or "").strip()[:2000]
    trace_id = audit.new_trace_id()
    with _pending_lock:
        pending = _pending.get(session.user_id)
    state = {"question": question, "session": session, "tools": ToolBox(session, as_of), "as_of": as_of,
             "pending": pending, "stats": {}, "agents": [], "trace_id": trace_id, "started": time.perf_counter()}
    final = get_graph().invoke(state)
    response = final["response"]

    with _pending_lock:
        if response.get("pending_action"):
            _pending[session.user_id] = response["pending_action"]
        elif final.get("intent") == "confirm" or pending:
            _pending.pop(session.user_id, None)

    tools = final["tools"]
    return {
        "trace_id": trace_id,
        "answer": response["answer"],
        "answer_type": response["answer_type"],
        "citations": response["citations"],
        "tools_invoked": [{"tool": c["tool"], "status": c["status"]} for c in tools.calls],
        "explanation": response.get("explanation", ""),
        "intent": final.get("intent", ""),
        "agents": final.get("agents", []),
        "pending_action": _public_pending(response.get("pending_action")),
        "latency_ms": final.get("latency_ms"),
        "model": llm.model_name() if final["stats"].get("llm_calls") else "none (deterministic)",
        "verifier": {"passed": final["verifier"]["passed"], "rephrased": final["verifier"].get("rephrased", False),
                     "problems": final["verifier"]["problems"]},
        "as_of_date": (as_of or config.AS_OF_DATE).isoformat(),
    }


def _public_pending(p):
    if not p:
        return None
    return {"action_id": p["action_id"], "type": p["type"], "summary": p["summary"],
            "options": [{"option": o["option"], "label": o["label"]} for o in p["options"]]}


def clear_pending(user_id):
    with _pending_lock:
        _pending.pop(user_id, None)
