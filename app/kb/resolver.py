"""Version and policy resolution (R4).

1. A document is usable on the as-of date only if it is not a draft, its effective date has
   arrived, it has not expired, and no usable document supersedes it. This is computed from
   the dates, not taken from the CSV's currently_effective column.
2. A newer version of the same doc_id replaces the older one. A partial amendment (a document
   that says the other sections "remain unchanged") replaces only the clauses it restates.
3. When two usable clauses make different claims about the same thing (e.g. "4 hours before the
   test" vs "2 hours before the test"), the higher authority wins (government regulation >
   national guideline > hospital policy > department SOP). Same authority: the superseding or
   newer document wins. Otherwise the conflict cannot be resolved and is flagged.
"""

import re

AUTHORITY_RANK = {"government_regulation": 1, "national_guideline": 2, "hospital_policy": 3, "department_sop": 4}
INACTIVE_STATUSES = {"draft", "withdrawn", "rejected", "retired"}

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_CLAIM_RE = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUM_WORDS) + r")\s+(?:designated\s+|more\s+|additional\s+)?"
    r"(visitors?|hours?|days?|minutes?|weeks?|months?|years?|working\s+days?)\b((?:\s+[a-z]+){0,3})", re.IGNORECASE)


def parse_supersedes(value):
    """'POL-VIS-001' or 'SOP-RAD-001 v1.2' (comma separated) -> [(doc_id, version or None)]."""
    out = []
    for part in re.split(r"[;,]", value or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"([A-Z0-9][A-Z0-9-]+)(?:\s*(?:@|v)\s*([\w.-]+))?$", part, re.IGNORECASE)
        if m:
            out.append((m.group(1).upper(), m.group(2)))
    return out


def _version_key(version):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", str(version)))


def usable_docs(docs, as_of):
    """Return {doc_key: doc} for documents in force on as_of, plus {doc_key: superseding doc}."""
    day = as_of.isoformat()
    live = {d["doc_key"]: d for d in docs
            if d["status"] not in INACTIVE_STATUSES and d["effective_date"] and d["effective_date"] <= day
            and (not d.get("expiry_date") or day <= d["expiry_date"])}

    superseded_by = {}
    for d in live.values():
        for target_id, target_ver in parse_supersedes(d.get("supersedes")):
            for other in docs:
                if other["doc_key"] != d["doc_key"] and other["doc_id"] == target_id and \
                        (target_ver is None or str(other["version"]) == str(target_ver)):
                    superseded_by[other["doc_key"]] = d
    # Several live versions of one doc_id: the newest replaces the others.
    by_id = {}
    for d in live.values():
        by_id.setdefault(d["doc_id"], []).append(d)
    for versions in by_id.values():
        if len(versions) > 1:
            newest = max(versions, key=lambda d: (d["effective_date"], _version_key(d["version"])))
            for d in versions:
                if d is not newest:
                    superseded_by.setdefault(d["doc_key"], newest)
    return live, superseded_by


def usable_chunk_ids(docs, chunks, as_of):
    live, superseded_by = usable_docs(docs, as_of)
    clauses_by_doc = {}
    for c in chunks:
        clauses_by_doc.setdefault(c["doc_key"], set()).add(c["clause"])
    ok = set()
    for c in chunks:
        if c["doc_key"] not in live:
            continue
        newer = superseded_by.get(c["doc_key"])
        if newer and newer["doc_key"] in live:
            if not newer.get("partial") or c["clause"] in clauses_by_doc.get(newer["doc_key"], set()):
                continue
        ok.add(c["chunk_id"])
    return ok


# ---------------------------------------------------------------- claim-level conflicts

def claims(text):
    out = []
    for m in _CLAIM_RE.finditer(text or ""):
        qty_raw = m.group(1).lower()
        qty = float(_NUM_WORDS[qty_raw]) if qty_raw in _NUM_WORDS else float(qty_raw)
        unit = re.sub(r"s$", "", " ".join(m.group(2).lower().split()))
        ctx = " ".join(re.findall(r"[a-z]+", m.group(3).lower())[:3])
        if ctx:
            out.append((unit, ctx, qty))
    return out


def conflicting(a, b):
    """True if two clauses from different documents state different quantities for the same thing."""
    if a["doc_id"] == b["doc_id"]:
        return False
    a_claims, b_claims = claims(a["text"]), claims(b["text"])
    return any(ua == ub and ca == cb and qa != qb for ua, ca, qa in a_claims for ub, cb, qb in b_claims)


def resolve_conflict(a, b):
    """Return the winning clause, or None if the conflict cannot be resolved automatically."""
    if a["authority_rank"] != b["authority_rank"]:
        return a if a["authority_rank"] < b["authority_rank"] else b
    if any(t == b["doc_id"] for t, _ in parse_supersedes(a.get("supersedes"))):
        return a
    if any(t == a["doc_id"] for t, _ in parse_supersedes(b.get("supersedes"))):
        return b
    if a["effective_date"] != b["effective_date"]:
        return a if a["effective_date"] > b["effective_date"] else b
    return None
