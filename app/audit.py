"""Audit trail (R10): one record per request with trace ID, sources, tools, privacy checks, latency and model.
Personal identifiers are masked before anything is stored (POL-PRIV-001 4.4)."""

import json
import uuid
from datetime import datetime, timezone

from app import db, guard


def new_trace_id():
    return f"HSP-{uuid.uuid4().hex[:8].upper()}"


def record(trace_id, session, question, intent, agents, response, tools, guard_report, verifier, latency_ms,
           model, llm_calls, error=""):
    masked_question, _ = guard.mask_pii(question)
    row = {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": session.user_id, "role": session.role, "subject_patient_id": session.subject_patient_id,
        "question_masked": masked_question, "intent": intent, "agents": json.dumps(agents),
        "answer_type": response["answer_type"],
        "retrieved_sources": json.dumps(response.get("data", {}).get("retrieved", [])),
        "citations": json.dumps([c["id"] for c in response["citations"]]),
        "tools_invoked": json.dumps(tools.calls),
        "privacy_checks": json.dumps({"identity_source": "login session",
                                      "session_patient_id": session.subject_patient_id,
                                      "tool_patient_checks": len([c for c in tools.calls if "patient_id" in c["args"]]),
                                      "denied": tools.denied,
                                      "pii_masked": guard_report.get("pii_masked", []),
                                      "other_patient_signals": guard_report.get("other_patient", {})}),
        "guard_flags": json.dumps({k: guard_report.get(k) for k in ("injection", "emergency", "medication_change")}),
        "verifier": json.dumps(verifier, default=str),
        "latency_ms": latency_ms, "model": model, "llm_calls": llm_calls, "error": error,
    }
    cols = ", ".join(row)
    db.execute(f"INSERT INTO audit_log ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    return row


def get(trace_id):
    row = db.query_one("SELECT * FROM audit_log WHERE trace_id = ?", (trace_id,))
    if not row:
        return None
    for k in ("agents", "retrieved_sources", "citations", "tools_invoked", "privacy_checks", "guard_flags", "verifier"):
        row[k] = json.loads(row[k]) if row[k] else None
    return row


def recent(limit=50):
    return [get(r["trace_id"]) for r in db.query("SELECT trace_id FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))]
