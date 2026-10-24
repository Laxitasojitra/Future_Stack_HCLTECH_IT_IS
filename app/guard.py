"""Safety guard: runs before routing and on any untrusted content.

- PII masking (POL-PRIV-001 4.3, 4.4): text sent to an LLM or written to the audit log is masked.
- Prompt-injection detection (R8, OWASP LLM01).
- Emergency red flags (SOP-EMR-001 section 2 and 4.1): routine tasks stop.
- Third-party / other-patient requests (R7, POL-PRIV-001 3.1, 4.1, 4.2).
- Requests to change medicines (clinical decisions, PROT-PREOP-004 7.2).
"""

import re

from app import db

# ---------------------------------------------------------------- PII masking

_PII_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("aadhaar", re.compile(r"(?<!\d)\d{4}[\s-]\d{4}[\s-]\d{4}(?!\d)")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("insurance_number", re.compile(r"\bINS\d{5,}\b", re.IGNORECASE)),
    ("phone", re.compile(r"(?<![\w+])(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|pin)(\s*(?:is|was|=|:)\s*)(?!(?:not|wrong|expired|reset|changed)\b)([^\s,;]+)")),
]


def mask_pii(text):
    """Return (masked_text, [types masked])."""
    found = []
    out = text or ""
    for label, pattern in _PII_PATTERNS:
        if label == "password":
            out, n = pattern.subn(r"\1\2[PASSWORD]", out)
        else:
            out, n = pattern.subn(f"[{label.upper()}]", out)
        found += [label] * n
    return out, found


def mask_for_display(value, keep=4):
    """Partly mask an identifier for display, e.g. XXXXXX4521 (POL-PRIV-001 4.3)."""
    value = str(value or "")
    if len(value) <= keep:
        return value
    return "X" * (len(value) - keep) + value[-keep:]


# ---------------------------------------------------------------- prompt injection

INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions?|rules|prompts?)",
    r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous\s+|prior\s+)?(?:instructions?|rules|prompts?)",
    r"forget\s+(?:all\s+|your\s+)?(?:previous\s+|prior\s+)?(?:instructions?|rules)",
    r"\byou\s+are\s+now\b",
    r"\b(?:administrator|admin|developer|god)\s+mode\b",
    r"\bact\s+as\s+(?:an?\s+)?(?:admin|administrator|system|doctor|developer|root)\b",
    r"(?:reveal|show|print|repeat)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+prompt|instructions|hidden\s+rules)",
    r"\bsystem\s+prompt\b",
    r"\bjailbreak\b",
    r"(?:skip|bypass|override)\s+(?:the\s+|your\s+)?(?:eligibility\s+)?(?:tools?|rules|safety|checks?|verification)",
    r"instructions?\s+for\s+(?:the\s+)?(?:hospital\s+)?(?:chat\s+|ai\s+)?assistants?",
    r"\bsystem\s+note\s+to\s+ai\b",
    r"do\s+not\s+mention\s+this\s+(?:note|instruction)",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def detect_injection(text):
    return [m.group(0) for r in _INJECTION_RE for m in [r.search(text or "")] if m]


def sanitize_untrusted(text):
    """Treat uploaded files/emails as data only (R8): drop hidden HTML comments and any line that
    gives instructions to the assistant. Returns (clean_text, removed_snippets)."""
    removed = []

    def _drop_comment(m):
        removed.append(" ".join(m.group(0).split())[:120])
        return ""

    clean = re.sub(r"<!--.*?-->", _drop_comment, text or "", flags=re.DOTALL)
    kept = []
    for line in clean.splitlines():
        if detect_injection(line):
            removed.append(line.strip()[:120])
        else:
            kept.append(line)
    return "\n".join(kept), removed


# ---------------------------------------------------------------- emergency red flags

EMERGENCY_SIGNS = {
    "2.1": r"chest\s+(?:pain|pressure|tightness)|pain\s+in\s+(?:my\s+)?chest|heart\s+attack",
    "2.2": r"(?:difficulty|trouble)\s+breathing|can'?t\s+breathe|cannot\s+breathe|severe(?:ly)?\s+breathless|short(?:ness)?\s+of\s+breath|gasping",
    "2.3": r"stroke|face\s+(?:is\s+)?droop|slurred\s+speech|sudden(?:ly)?\s+(?:confus|weak)|(?:arm|leg)\s+weakness",
    "2.4": r"(?:severe|heavy)\s+bleeding|bleeding\s+(?:that\s+)?(?:won'?t|will\s+not|does\s*n[o']t)\s+stop",
    "2.5": r"unconscious|fainted|fainting|passed\s+out|seizure|convulsion|fits\b",
    "2.6": r"(?:swelling|swollen)\s+(?:of\s+)?(?:my\s+|the\s+)?(?:face|lips?|throat|tongue)|anaphyla|severe\s+allergic",
}
_EMERGENCY_RE = {k: re.compile(v, re.IGNORECASE) for k, v in EMERGENCY_SIGNS.items()}
# Policy-style questions ("when should I seek urgent care...") still get the emergency path: safer.


def detect_emergency(text):
    return [clause for clause, r in _EMERGENCY_RE.items() if r.search(text or "")]


# ---------------------------------------------------------------- other patients / third parties

RELATION_WORDS = r"(?:wife|husband|spouse|partner|mother|mom|mum|father|dad|son|daughter|brother|sister|" \
                 r"grand(?:mother|father|ma|pa)|uncle|aunt|friend|neighbou?r|colleague|relative|child)"
_THIRD_PARTY_RE = [
    re.compile(rf"\bmy\s+{RELATION_WORDS}(?:'s)?\b", re.IGNORECASE),
    re.compile(rf"\bI\s*(?:am|'m)\s+.{{0,40}}?\b{RELATION_WORDS}\b", re.IGNORECASE),
    re.compile(r"\b(?:her|his|their)\s+(?:appointments?|results?|reports?|bills?|records?|lab|medicines?|"
               r"medications?|prescriptions?|insurance|surgery|care\s+plan)\b", re.IGNORECASE),
]
_PATIENT_ID_RE = re.compile(r"\bP\d{3,}\b", re.IGNORECASE)
_names_cache = None


def _patient_names():
    global _names_cache
    if _names_cache is None:
        rows = db.query("SELECT patient_id, first_name, last_name FROM patients")
        _names_cache = [(r["patient_id"], f"{r['first_name']} {r['last_name']}".lower()) for r in rows]
    return _names_cache


def detect_other_patient(text, session):
    """Signals that the message asks about someone other than the session's subject patient."""
    subject = session.subject_patient_id
    lowered = (text or "").lower()
    ids = {m.group(0).upper() for m in _PATIENT_ID_RE.finditer(text or "")} - {subject}
    names = [pid for pid, name in _patient_names() if pid != subject and re.search(rf"\b{re.escape(name)}\b", lowered)]
    relations = [m.group(0) for r in _THIRD_PARTY_RE for m in [r.search(text or "")] if m]
    return {"other_patient_ids": sorted(ids), "other_patient_names": names, "third_party": relations}


# ---------------------------------------------------------------- medication changes

_MED_CHANGE_RE = re.compile(
    r"\b(?:should|can|could|do|must|shall)\s+i\s+(?:\w+\s+){0,2}?(?:stop|skip|pause|discontinue|quit|reduce|lower|"
    r"increase|double|halve|change|cut\s+down)\b", re.IGNORECASE)
_MED_WORDS_RE = re.compile(r"\b(?:medicine|medication|tablet|dose|drug|pill|insulin|injection)s?\b", re.IGNORECASE)


_drug_cache = None


def _drug_names():
    global _drug_cache
    if _drug_cache is None:
        _drug_cache = [r["generic_name"] for r in db.query("SELECT generic_name FROM medications")]
    return _drug_cache


def detect_medication_change(text, drug_names=()):
    if not _MED_CHANGE_RE.search(text or ""):
        return False
    drug_names = drug_names or _drug_names()
    lowered = (text or "").lower()
    return bool(_MED_WORDS_RE.search(text)) or any(d.lower() in lowered for d in drug_names)


# ---------------------------------------------------------------- one call

def inspect(text, session, drug_names=()):
    masked, pii = mask_pii(text)
    return {
        "masked_text": masked,
        "pii_masked": pii,
        "injection": detect_injection(text),
        "emergency": detect_emergency(text),
        "other_patient": detect_other_patient(text, session),
        "medication_change": detect_medication_change(text, drug_names),
    }
