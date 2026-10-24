"""Small text helpers shared by BM25, query expansion and the relevance gate."""

import re

STOPWORDS = set("""
a an the and or but if of to in on at by for with from into onto about as is are was were be been being
do does did done have has had having i me my mine we our you your he she his her they them their it its
this that these those there here what which who whom whose when where why how can could should would
will shall may might must not no yes any all some each per than then so too very just also please tell
show give get got let know want need like after before up down out over under again once am im ive
hospital patient patients long take takes taken soon
""".split())

# Light query expansion: acronyms and everyday words -> words used in the documents.
SYNONYMS = {
    "icu": ["intensive", "care", "unit"],
    "mri": ["mri", "scan"],
    "ct": ["ct", "scan"],
    "sugar": ["glucose"],
    "stitches": ["suture", "staple", "wound"],
    "stitch": ["suture", "staple"],
    "deposit": ["deposit", "advance"],
    "documents": ["bring", "id", "card"],
    "thyroid": ["thyroid", "endocrinology"],
    "stress": ["treadmill", "tmt", "stress"],
    "treadmill": ["treadmill", "tmt"],
    "refund": ["refund", "refunded", "excess"],
    "kids": ["children"],
    "child": ["children"],
    "fast": ["fasting", "eat"],
    "timings": ["hours", "time"],
    "timing": ["hours", "time"],
    "visit": ["visiting", "visitor"],
    "visitors": ["visitor", "visits"],
    "admission": ["admission", "admitted"],
    "report": ["result", "available", "report"],
    "reports": ["result", "available", "report"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[/.][a-z0-9]+)*")


def stem(word):
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: len(word) - len(suffix)] + repl
    return word


def tokens(text):
    return [stem(t) for t in _TOKEN_RE.findall((text or "").lower())]


def content_terms(text):
    """Query terms that carry meaning (stopwords removed), stemmed, de-duplicated, order kept."""
    seen, out = set(), []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        t = stem(raw)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def expand_query(text):
    extra = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        extra += SYNONYMS.get(raw, [])
    return text if not extra else f"{text} {' '.join(extra)}"


TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b|\b\d{1,2}\s*(?:AM|PM)\b|24 hours a day", re.IGNORECASE)
NUMBER_RE = re.compile(r"₹\s?[\d,]+|\b\d+(?:\.\d+)?\s*(?:%|hours?|days?|minutes?|weeks?|months?|years?|working days)\b|"
                       r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b", re.IGNORECASE)
WHEN_Q = re.compile(r"^\s*(?:when|what\s+time)|\b(?:hours|timings?|time\s+of\s+(?:the\s+)?day|open|opening)\b", re.IGNORECASE)
QUANTITY_Q = re.compile(r"\bhow\s+(?:many|much|long|soon|often)\b|\bwhat\s+is\s+the\s+(?:deposit|fee|cost|charge|limit)", re.IGNORECASE)
