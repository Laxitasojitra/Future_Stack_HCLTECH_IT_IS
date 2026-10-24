"""Response verifier: deterministic checks before anything reaches the patient (R2, R3, R7, R9).

1. answer_type is one of the 7 allowed types.
2. Factual answers carry at least one citation; cited documents are in force today.
3. Every number in the answer appears in the evidence (tool outputs, cited clauses, the question).
4. No other patient's name or ID appears; phone and insurance numbers are masked.
5. An optional LLM rephrasing must keep every number and citation of the verified draft, or it is discarded.
"""

import json
import re

from app import guard, llm
from app.agents.base import ANSWER_TYPES, NOT_FOUND_MESSAGE
from app.auth import PATIENT_DATA_ROLES
from app.kb.store import get_kb

NEEDS_CITATION = {"retrieved_fact", "calculated", "action_completed", "conflict_flagged"}
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CITE_RE = re.compile(r"\[[A-Z][A-Z0-9-]+ §[\d.]+[^\]]*\]")

REPHRASE_PROMPT = (
    "Rewrite the draft answer for a hospital patient so it reads clearly and warmly. Rules: use ONLY facts in the "
    "draft; keep every number, amount, date, time and every bracketed citation exactly as written; keep the same "
    "meaning and any safety advice; do not add medical advice; keep lists as lists; plain text only."
)


def numbers(text_value):
    out = set()
    for m in _NUM_RE.findall(text_value or ""):
        v = m.replace(",", "")
        out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def _evidence_numbers(question, tools, response):
    blobs = [question, json.dumps(tools.results, default=str), json.dumps(response.get("data", {}), default=str),
             json.dumps(response.get("pending_action") or {}, default=str)]
    kb = get_kb()
    doc_ids = [c["id"] for c in response["citations"] if c.get("type") == "document"]
    blobs += [c["text"] for c in kb.usable_sections(doc_ids)]
    blobs.append(json.dumps(response["citations"], default=str))     # versions and effective dates in labels
    ev = set()
    for b in blobs:
        ev |= numbers(b)
        ev |= set(re.findall(r"\d+", b or ""))          # date and time parts: 2026-10-27 -> 2026, 10, 27
    return ev


def _unsupported(answer, evidence):
    bad = []
    for n in numbers(answer):
        if n in evidence or (n.isdigit() and int(n) <= 12):
            continue
        bad.append(n)
    return bad


def _other_patient_names(answer, session):
    subject = session.subject_patient_id
    lowered = re.sub(r"\bdr\.?\s+[a-z]+\s+[a-z]+", " ", answer.lower())   # doctors may share a patient's name
    hits = [pid for pid, name in guard._patient_names() if pid != subject and name in lowered]
    hits += [m for m in re.findall(r"\bP\d{3}\b", answer) if m != subject]
    return hits


def _mask_identifiers(answer):
    answer = re.sub(r"(?<![\w-])([6-9]\d{9})(?![\w-])", lambda m: guard.mask_for_display(m.group(1)), answer)
    return re.sub(r"\bINS\d{4,}\b", lambda m: guard.mask_for_display(m.group(0)), answer)


def check(question, response, tools, session):
    """Return (ok, checks, problems)."""
    problems = []
    if response["answer_type"] not in ANSWER_TYPES:
        problems.append(f"invalid answer_type {response['answer_type']}")
    if response["answer_type"] in NEEDS_CITATION and not response["citations"]:
        problems.append("factual answer without a citation")
    kb = get_kb()
    doc_ids = [c["id"] for c in response["citations"] if c.get("type") == "document"]
    live = {c["section_id"] for c in kb.usable_sections(doc_ids)}
    stale = [d for d in doc_ids if d not in live]
    if stale:
        problems.append(f"cites documents not in force: {stale}")
    unsupported = _unsupported(response["answer"], _evidence_numbers(question, tools, response))
    if unsupported and response["answer_type"] not in ("refused", "not_found"):
        problems.append(f"numbers not found in any source: {unsupported}")
    leaks = _other_patient_names(response["answer"], session) if session.role in PATIENT_DATA_ROLES else []
    if leaks:
        problems.append(f"mentions another patient: {leaks}")
    checks = {"citations": len(response["citations"]), "stale_citations": stale, "unsupported_numbers": unsupported,
              "other_patient_mentions": leaks, "tool_access_denied": tools.denied}
    return not problems, checks, problems


def _rephrase(question, draft, stats):
    try:
        new = llm.chat(REPHRASE_PROMPT, f"Patient question: {question}\n\nDraft answer:\n{draft}", stats=stats,
                       max_tokens=700)
    except llm.LLMUnavailable:
        return None
    if numbers(new) != numbers(draft):
        return None
    if sorted(_CITE_RE.findall(new)) != sorted(_CITE_RE.findall(draft)):
        return None
    return new


def verify(question, response, tools, session, stats=None):
    """Run the checks, optionally rephrase with the LLM, and return the final response + verifier report."""
    from app import config

    ok, checks, problems = check(question, response, tools, session)
    report = {"passed": ok, "problems": problems, "checks": checks, "rephrased": False}
    if not ok:
        if any("another patient" in p for p in problems):
            response = {**response, "answer": "I can only share information from your own records.",
                        "answer_type": "refused", "citations": [], "pending_action": None}
        else:
            response = {**response, "answer": NOT_FOUND_MESSAGE, "answer_type": "not_found", "pending_action": None}
        report["action"] = f"blocked and replaced with {response['answer_type']}"
    elif response.get("rephrase") and config.LLM_REPHRASE and llm.available():
        new = _rephrase(question, response["answer"], stats)
        if new:
            response = {**response, "answer": new}
            report["rephrased"] = True
        else:
            report["rephrase_rejected"] = True
    response = {**response, "answer": _mask_identifiers(response["answer"])}
    report["answer_type"] = response["answer_type"]
    return response, report
