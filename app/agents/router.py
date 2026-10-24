"""Router: decides which agent handles a question.

Rules first (fast, predictable, auditable); the LLM is asked only when no rule matches (ROUTER_MODE=hybrid)
or for every question (ROUTER_MODE=llm). Safety checks (emergency, privacy, injection, medication changes)
run before this, in the guard node, and cannot be overridden by the LLM.
"""

import re

from app import config, llm
from app.agents.insurance import UNTRUSTED_CLAIM
from app.tools import find_lab_tests

INTENTS = {
    "policy": "General hospital policy or procedure question (visiting hours, admission, discharge, preparation, fees).",
    "department": "Which department or specialist to visit for a problem.",
    "appointments_view": "The patient's own upcoming appointments.",
    "reschedule": "Reschedule or cancel one of the patient's appointments.",
    "book": "Book a new appointment or rebook a missed session.",
    "lab": "The patient's own lab test results.",
    "reports": "The patient's own imaging or ECG reports.",
    "insurance": "Whether the patient's insurance covers a procedure, co-pay, pre-authorisation.",
    "cost": "How much the patient will pay for a procedure (estimate).",
    "billing": "How much the patient owes, bills, balance due.",
    "refund": "Status of the patient's refund.",
    "medication": "Whether medicines can be taken together (interaction check).",
    "care_plan": "Missed or upcoming follow-ups in the patient's care plan.",
    "readiness": "Whether the patient is ready for planned surgery.",
    "preop_pending": "Which pre-operative tests are still pending.",
    "document": "Summarise a document the patient uploaded.",
    "help": "Greeting or what the assistant can do.",
}
PERSONAL_INTENTS = set(INTENTS) - {"policy", "department", "help"}

HOW_TO = re.compile(r"^\s*(?:how\s+(?:do|can|should|would)\s+i|how\s+to|what\s+is\s+the\s+(?:process|procedure))\b", re.I)
MINE = re.compile(r"\b(?:my|mine|me|am\s+i|i\s+am|do\s+i|will\s+i|i\s+have|have\s+i|i'm)\b", re.I)

RULES = [
    ("document", r"\b(?:uploaded|upload|referral\s+letter|letter\s+i|document\s+i|my\s+(?:letter|document))\b"),
    ("book", r"\b(?:book|rebook|re-book)\b.{0,60}\b(?:session|appointment|slot|follow-?up|physio\w*|consultation)"),
    ("reschedule", r"\b(?:reschedule|re-schedule|postpone|cancel|move|shift|change)\b.{0,40}\bappointment"),
    ("readiness", r"\b(?:ready|cleared|clearance|proceed|go\s+ahead)\b.{0,40}\b(?:surgery|operation)|"
                  r"\b(?:surgery|operation)\b.{0,30}\bready\b|everything\s+ready"),
    ("preop_pending", r"(?:pre-?op(?:erative)?|before\s+(?:my\s+)?surgery).{0,40}\b(?:tests?|pending|investigations?)|"
                      r"\bpending\b.{0,30}\b(?:tests?|investigations?)|\btests?\b.{0,30}\bstill\s+pending"),
    ("care_plan", r"\bmiss(?:ed)?\b.{0,40}\b(?:follow-?ups?|sessions?|appointments?|physio\w*)|\bcare\s+plan\b|"
                  r"\bfollow-?ups?\b.{0,30}\b(?:after|due|pending|left)"),
    ("refund", r"\bmy\s+refund\b|\bwhere\s+is\s+(?:my\s+)?(?:the\s+)?refund\b|\brefund\s+status\b|status\s+of\s+(?:my\s+)?refund"),
    ("billing", r"\b(?:owe|outstanding|balance\s+due|my\s+balance|amount\s+due|my\s+bills?|unpaid|pending\s+(?:bill|payment))\b"),
    ("cost", r"\bhow\s+much\b.{0,30}\b(?:pay|cost|charge)|\b(?:pay\s+anything|have\s+to\s+pay|out\s+of\s+pocket|"
             r"my\s+share|cost\s+estimate|estimate\s+for)\b"),
    ("insurance", r"\b(?:covered|cover|coverage|insurance|insurer|co-?pay(?:ment)?|eligible|eligibility|claim|"
                  r"pre-?auth\w*|cashless|scheme)\b"),
    ("reports", r"\bmy\b.{0,30}\b(?:mri|ct|x-?ray|ecg|scan|imaging)\b.{0,20}\b(?:report|result|findings)"),
    ("lab", r"\b(?:result|reading|level|value|report|count)s?\b"),
    ("appointments_view", r"\b(?:my|upcoming|next)\b.{0,30}\bappointments?\b|\bwhen\s+is\s+my\b.{0,30}\bappointment"),
    ("department", r"\bwhich\s+(?:department|doctor|specialist|clinic|speciality)\b|\bwho\s+should\s+i\s+see\b|"
                   r"\bwhere\s+should\s+i\s+go\s+for\b"),
    ("help", r"^\s*(?:hi|hello|hey|help|good\s+(?:morning|afternoon|evening)|what\s+can\s+you\s+do)\b"),
]
_RULES = [(intent, re.compile(p, re.I)) for intent, p in RULES]


LAB_WORDS = re.compile(r"\b(?:results?|readings?|levels?|values?|count)\b", re.I)
RECENT = re.compile(r"\b(?:latest|last|recent|current|previous|newest)\b", re.I)
NEEDS_MINE = {"appointments_view", "billing", "cost", "insurance", "lab", "reports", "refund"}


def rule_route(question, drugs=()):
    """Return (intent, confidence). Confidence < 0.5 means "no rule matched" (default: policy search)."""
    q = question.strip()
    personal = bool(MINE.search(q))
    if drugs and re.search(r"\b(?:take|taking|with|together|safe|interact\w*|combine|mix|along)\b", q, re.I):
        return "medication", 0.9
    if HOW_TO.search(q):
        return "policy", 0.8
    for intent, pattern in _RULES:
        if intent == "lab":
            if personal and (LAB_WORDS.search(q) or (find_lab_tests(q) and RECENT.search(q))):
                return "lab", 0.9 if find_lab_tests(q) else 0.7
            continue
        if not pattern.search(q):
            continue
        if intent == "insurance" and not personal and UNTRUSTED_CLAIM.search(q):
            return "insurance", 0.9
        if intent in NEEDS_MINE and not personal:
            continue
        return intent, 0.9
    return "policy", 0.4


LLM_PROMPT = (
    "You route questions for a hospital patient-services assistant. Choose exactly one intent from this list and "
    "reply as JSON {\"intent\": \"<name>\"}. Intents:\n" + "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
)


def route(question, drugs=(), stats=None):
    """Return (intent, method)."""
    intent, confidence = rule_route(question, drugs)
    use_llm = llm.available() and (config.ROUTER_MODE == "llm" or (config.ROUTER_MODE == "hybrid" and confidence < 0.5))
    if use_llm:
        try:
            choice = llm.chat_json(LLM_PROMPT, f"Question: {question}", stats=stats).get("intent", "")
            if choice in INTENTS:
                return choice, "llm"
        except llm.LLMUnavailable:
            pass
    return intent, "rules"
