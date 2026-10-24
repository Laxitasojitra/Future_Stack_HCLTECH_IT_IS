"""Minimal policy lookup (stand-in for Person 1's hybrid search). Finds clauses by ID or by keywords,
keeping only documents in force on the as-of date (approved, effective, not expired, not superseded)."""

import re

from app import config, db

_kb = None


class SimpleKB:
    def __init__(self):
        day = config.AS_OF_DATE.isoformat()
        docs = {d["doc_id"]: d for d in db.query("SELECT * FROM hospital_policies")}
        live = {k for k, d in docs.items() if d["status"] != "draft" and d["effective_date"] <= day
                and (not d["expiry_date"] or day <= d["expiry_date"])}
        live -= {d["supersedes"] for k, d in docs.items() if k in live and d["supersedes"]}
        self.sections = []
        for s in db.query("SELECT * FROM hospital_policy_sections"):
            if s["doc_id"] in live:
                d = docs[s["doc_id"]]
                self.sections.append({**s, "clause": s["clause_number"], "expiry_date": d["expiry_date"]})

    def usable_sections(self, section_ids, as_of=None):
        wanted = set(section_ids)
        return [s for s in self.sections if s["section_id"] in wanted]

    def search(self, query, as_of=None, k=5, doc_ids=None):
        words = set(re.findall(r"[a-z]{3,}", query.lower()))
        pool = [s for s in self.sections if not doc_ids or s["doc_id"] in doc_ids]
        scored = sorted(pool, key=lambda s: -len(words & set(re.findall(r"[a-z]{3,}", s["text"].lower()))))
        return scored[:k]


def get_kb():
    global _kb
    if _kb is None:
        _kb = SimpleKB()
    return _kb
