"""Policy agent: answers policy and procedure questions from approved documents onlypython.

Hybrid search -> relevance gate  -> pick the best clauses -> resolve
claim-level conflicts (higher authority wins; otherwise conflict_flagged) -> cite each clause
with document, section, version and effective date (R2).
"""

import re

from app.agents.base import not_found, result
from app.fmt import clean_clause, cite_label, doc_citation, nice_date
from app.kb import resolver, text
from app.kb.store import get_kb

MIN_COVERAGE = 0.45          # share of the question's meaningful words found in the best clause
MIN_COVERAGE_WITH_DENSE = 0.3
MIN_DENSE = 0.55
MAX_CLAUSES = 4


def _relevant(hit):
    return hit["coverage"] >= MIN_COVERAGE or (hit["coverage"] >= MIN_COVERAGE_WITH_DENSE and hit["dense"] >= MIN_DENSE)


def _table_rows(clause_text, question):
    """For clauses that are Markdown tables, keep only the rows that match the question."""
    lines = [ln for ln in clause_text.splitlines() if ln.strip()]
    rows = [ln for ln in lines if ln.strip().startswith("|")]
    if len(rows) < 3:
        return clause_text
    intro = " ".join(ln for ln in lines if not ln.strip().startswith("|"))
    header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]]
    terms = text.content_terms(question)
    matched = [r for r in body if any(t in text.tokens(" ".join(r)) for t in terms)] or body
    rendered = []
    for r in matched:
        pairs = [f"{h}: {v}" for h, v in zip(header[1:], r[1:])]
        rendered.append(f"{r[0]} - " + "; ".join(pairs))
    return (intro + " " if intro else "") + " | ".join(rendered)


PROCEDURE_Q = re.compile(r"\bhow\s+(?:do|can|should)\s+i\b|\bhow\s+to\b|\bsteps?\b|\bprocess\b|\bprocedure\b", re.IGNORECASE)


def select_clauses(question, hits):
    primary = hits[0]
    chosen = [primary]
    if PROCEDURE_Q.search(question):
        # "How do I ...?" -> give the whole numbered procedure from the best section, in order.
        section = [c for c in get_kb().usable_sections_by_doc(primary["doc_key"])
                   if c["section_number"] == primary["section_number"] and c["clause"] != primary["section_number"]]
        section = list({c["section_id"]: c for c in section}.values())
        if 1 < len(section) <= 8:
            return sorted(section, key=lambda c: [int(x) for x in re.findall(r"\d+", c["clause"])])
    for h in hits[1:6]:
        if len(chosen) >= MAX_CLAUSES:
            break
        if any(h["section_id"] == c["section_id"] for c in chosen):
            continue
        same_topic = h["doc_id"] == primary["doc_id"] and h["section_number"] == primary["section_number"]
        if (same_topic and h["score"] >= 0.8 * primary["score"]) or \
                (h["score"] >= 0.9 * primary["score"] and h["coverage"] >= primary["coverage"] * 0.8):
            chosen.append(h)
    return chosen


def answer(question, as_of=None):
    kb = get_kb()
    hits = kb.search(question, as_of=as_of, k=8)
    if not hits or not _relevant(hits[0]):
        best = hits[0] if hits else None
        return not_found(explanation="No approved document in force covers this question "
                                     f"(best match {best['section_id'] if best else 'none'}, "
                                     f"coverage {best['coverage'] if best else 0:.2f}).",
                         data={"retrieved": [h["section_id"] for h in hits[:5]]})

    chosen = select_clauses(question, hits)
    quoted, overridden, conflicts = list(chosen), [], []
    for c in list(chosen):
        for h in hits[:6]:
            if h["chunk_id"] == c["chunk_id"] or not resolver.conflicting(c, h):
                continue
            winner = resolver.resolve_conflict(c, h)
            if winner is None:
                conflicts.append((c, h))
            elif winner["chunk_id"] == h["chunk_id"]:
                overridden.append((c, h))
                quoted = [h if q["chunk_id"] == c["chunk_id"] else q for q in quoted]
            else:
                overridden.append((h, c))
    quoted = list({q["chunk_id"]: q for q in quoted}.values())
    overridden = list({(lo["chunk_id"], w["chunk_id"]): (lo, w) for lo, w in overridden}.values())
    retrieved = [h["section_id"] for h in hits[:6]]

    if conflicts:
        a, b = conflicts[0]
        lines = ["Two current documents with the same authority give different instructions, so I can't "
                 "resolve this automatically:"]
        for c in (a, b):
            lines.append(f"- {c['title']} ({c['doc_id']} §{c['clause']}, v{c['version']}, effective "
                         f"{nice_date(c['effective_date'])}): {clean_clause(c['text'])}")
        lines.append("Please confirm the correct instruction with the department before your appointment. "
                     "This conflict has been logged for the document owners.")
        return result("\n".join(lines), "conflict_flagged", [doc_citation(a, "conflict"), doc_citation(b, "conflict")],
                      explanation="Same authority level, version and effective date; conflict cannot be resolved by the "
                                  "precedence rules.", data={"retrieved": retrieved}, rephrase=False)

    lines = []
    for c in quoted:
        lines.append(f"{_table_rows(clean_clause(c['text']), question)} {cite_label(c)}")
    body = lines[0] if len(lines) == 1 else "\n".join(f"- {ln}" for ln in lines)
    notes = []
    for loser, winner in overridden:
        until = f" until {nice_date(winner['expiry_date'])}" if winner.get("expiry_date") else ""
        notes.append(f"Note: {winner['title']} ({winner['doc_id']}, {winner['authority_level'].replace('_', ' ')}) "
                     f"takes precedence over {loser['title']} ({loser['doc_id']} §{loser['clause']}){until}.")
    answer_text = body + ("\n\n" + "\n".join(notes) if notes else "")
    citations = [doc_citation(c) for c in quoted] + [doc_citation(l, "overridden") for l, _ in overridden]
    explanation = "Answered from approved documents in force on the as-of date"
    if overridden:
        explanation += "; a higher-authority document overrides a conflicting hospital clause"
    return result(answer_text, "retrieved_fact", citations, explanation + ".",
                  data={"retrieved": retrieved, "clauses": [c["section_id"] for c in quoted]})
