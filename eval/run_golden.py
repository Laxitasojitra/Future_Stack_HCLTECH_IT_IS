"""Evaluate the assistant on data/raw/golden_set.csv (47 questions) plus the live-ingestion check.

Runs on a throw-away copy of the data store, so bookings made during the test don't change your demo data.

    python -m eval.run_golden              # writes eval/report.md, eval/results.csv, eval/summary.json
    python -m eval.run_golden --only G024  # one question, printed in full

Metrics follow section 9 of the use-case document: answer accuracy, citation accuracy, privacy compliance,
tool accuracy, retrieval hit rate, action success rate, latency p50/p95 and safety compliance.
"""

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="hospital_eval_"))
os.environ["STORE_DIR"] = str(TMP / "store")          # isolated store: set before importing the app
sys.path.insert(0, str(ROOT))

from app import auth, config, db, service  # noqa: E402
from app.kb import ingest  # noqa: E402

PRIVACY_CATS = {"privacy", "injection"}
SAFETY_CATS = {"safety", "medication", "injection", "privacy", "not_found"}


def split(value):
    return [s.strip() for s in (value or "").split(";") if s.strip()]


def contains(text, phrase):
    return phrase.lower() in text.lower()


def contains_word(text, phrase):
    return re.search(rf"(?<![\w.,]){re.escape(phrase)}(?![\w])", text, re.IGNORECASE) is not None


def run_question(row):
    session = auth.session_for_user(row["user_id"])
    service.clear_pending(session.user_id)
    first = service.ask(session, row["question"])
    turns = [first]
    if first.get("pending_action") and row["expected_answer_type"] == "action_completed":
        turns.append(service.ask(session, "Yes, confirm option 1."))
    service.clear_pending(session.user_id)
    return turns


def score(row, turns):
    final = turns[-1]
    answer = final["answer"]
    cited = {c["id"] for t in turns for c in t["citations"]}
    tools = {c["tool"] for t in turns for c in t["tools_invoked"]}
    exp_sources, exp_tools = split(row["expected_sources"]), split(row["expected_tools"])
    must, must_not = split(row["must_include"]), split(row["must_not_include"])
    checks = {
        "answer_type": final["answer_type"] == row["expected_answer_type"],
        "citations": set(exp_sources) <= cited,
        "tools": set(exp_tools) <= tools,
        "must_include": all(contains(answer, p) for p in must),
        "must_not_include": not any(contains_word(answer, p) for p in must_not),
    }
    return checks, {"cited": sorted(cited), "tools": sorted(tools), "answer": answer,
                    "missing_include": [p for p in must if not contains(answer, p)],
                    "forbidden_found": [p for p in must_not if contains_word(answer, p)],
                    "missing_sources": sorted(set(exp_sources) - cited), "missing_tools": sorted(set(exp_tools) - tools),
                    "latency_ms": sum(t["latency_ms"] or 0 for t in turns), "trace_ids": [t["trace_id"] for t in turns],
                    "answer_type": final["answer_type"]}


def live_ingestion_checks():
    """R11: a new version becomes searchable without restart; a document without metadata is rejected."""
    admin = auth.session_for_user("U-ADMIN-01")
    patient = auth.session_for_user("U-P051")
    out = []
    try:
        ingest.ingest((ROOT / "data/ingestion_samples/bad_missing_metadata.md").read_text(encoding="utf-8"), "bad_missing_metadata.md",
                      session=admin)
        out.append(("Reject document with missing metadata", False, "was accepted"))
    except ingest.IngestionError as exc:
        out.append(("Reject document with missing metadata", True, str(exc)))
    r = service.ask(patient, "Can I visit the ICU at any time of the day?")
    out.append(("Rejected document stays unsearchable", "any time of the day" not in r["answer"].lower()
                and "10:00 AM" in r["answer"], r["answer"][:120]))
    before = service.ask(patient, "When can I get an MRI scan?")["answer"]
    info = ingest.ingest((ROOT / "data/ingestion_samples/SOP-RAD-001_v1.3.md").read_text(encoding="utf-8"), "SOP-RAD-001_v1.3.md",
                         session=admin)
    after = service.ask(patient, "When can I get an MRI scan?")["answer"]
    out.append(("New version searchable without restart (7 AM-10 PM -> 6 AM-11 PM)",
                "7:00 AM to 10:00 PM" in before and "6:00 AM to 11:00 PM" in after and "10:00 PM" not in after,
                f"ingested {info['doc_id']} v{info['version']}"))
    try:
        ingest.ingest("# x\n## 1. A\n1.1 test", "x.md", session=patient)
        out.append(("Only administrators can upload", False, "patient upload accepted"))
    except ingest.IngestionError as exc:
        out.append(("Only administrators can upload", True, str(exc)))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated question ids")
    args = parser.parse_args()

    db.init_db(reset=True)
    service.startup()
    golden = db.load_golden_set().to_dict("records")
    if args.only:
        wanted = set(args.only.split(","))
        golden = [r for r in golden if r["q_id"] in wanted]

    rows = []
    for r in golden:
        turns = run_question(r)
        checks, info = score(r, turns)
        rows.append({**r, **info, **{f"ok_{k}": v for k, v in checks.items()}, "passed": all(checks.values())})
        mark = "PASS" if rows[-1]["passed"] else "FAIL"
        print(f"{r['q_id']} {mark} {r['category']:<18} {info['answer_type']:<20} {info['latency_ms']:>5} ms  "
              f"{'' if rows[-1]['passed'] else {k: v for k, v in checks.items() if not v}}")
        if args.only:
            print(json.dumps({k: info[k] for k in ("answer", "cited", "tools", "missing_include", "forbidden_found",
                                                   "missing_sources", "missing_tools")}, indent=2, ensure_ascii=False))

    ingestion = [] if args.only else live_ingestion_checks()
    write_report(rows, ingestion)
    shutil.rmtree(TMP, ignore_errors=True)


def pct(n, d):
    return f"{(100 * n / d):.0f}% ({n}/{d})" if d else "n/a"


def write_report(rows, ingestion):
    n = len(rows)
    lat = sorted(r["latency_ms"] for r in rows)
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else 0
    with_sources = [r for r in rows if split(r["expected_sources"])]
    with_tools = [r for r in rows if split(r["expected_tools"])]
    policy_rows = [r for r in rows if any("#" in s for s in split(r["expected_sources"]))]
    privacy = [r for r in rows if r["category"] in PRIVACY_CATS]
    safety = [r for r in rows if r["category"] in SAFETY_CATS]
    actions = [r for r in rows if r["expected_answer_type"] == "action_completed"]
    metrics = {
        "Overall pass rate": pct(sum(r["passed"] for r in rows), n),
        "Answer accuracy (type + required facts + no forbidden facts)": pct(
            sum(r["ok_answer_type"] and r["ok_must_include"] and r["ok_must_not_include"] for r in rows), n),
        "Citation accuracy": pct(sum(r["ok_citations"] for r in with_sources), len(with_sources)),
        "Tool accuracy": pct(sum(r["ok_tools"] for r in with_tools), len(with_tools)),
        "Retrieval hit rate (expected clause cited)": pct(sum(r["ok_citations"] for r in policy_rows), len(policy_rows)),
        "Privacy compliance": pct(sum(r["passed"] for r in privacy), len(privacy)),
        "Safety compliance": pct(sum(r["passed"] for r in safety), len(safety)),
        "Action success rate": pct(sum(r["passed"] for r in actions), len(actions)),
        "Latency p50 / p95": f"{p50:.0f} ms / {p95:.0f} ms",
        "Live ingestion checks": pct(sum(ok for _, ok, _ in ingestion), len(ingestion)) if ingestion else "not run",
    }
    by_cat = {}
    for r in rows:
        c = by_cat.setdefault(r["category"], [0, 0])
        c[0] += r["passed"]
        c[1] += 1

    out_dir = ROOT / "eval"
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["q_id", "category", "user_id", "question", "expected_answer_type", "answer_type", "passed",
                "ok_answer_type", "ok_citations", "ok_tools", "ok_must_include", "ok_must_not_include",
                "missing_sources", "missing_tools", "missing_include", "forbidden_found", "latency_ms", "answer"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    (out_dir / "summary.json").write_text(json.dumps({
        "as_of_date": config.AS_OF_DATE.isoformat(), "metrics": metrics,
        "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in by_cat.items()},
        "ingestion": [{"check": c, "passed": ok, "detail": d} for c, ok, d in ingestion],
        "latencies_ms": lat,
        "questions": [{k: r[k] for k in ("q_id", "category", "passed", "answer_type", "latency_ms")} for r in rows],
    }, indent=2), encoding="utf-8")

    from app import llm
    from app.kb.store import get_kb

    lines = ["# Evaluation report", "",
             f"Golden set: {n} questions · as-of date {config.AS_OF_DATE} · LLM: {llm.model_name()} · "
             f"embeddings: {get_kb().embedder.name} · hybrid search (ChromaDB + BM25, RRF)", "",
             "| Metric | Result |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in metrics.items()]
    lines += ["", "## By category", "", "| Category | Passed |", "|---|---|"]
    lines += [f"| {k} | {v[0]}/{v[1]} |" for k, v in sorted(by_cat.items())]
    if ingestion:
        lines += ["", "## Live ingestion (R11)", "", "| Check | Result | Detail |", "|---|---|---|"]
        lines += [f"| {c} | {'pass' if ok else 'FAIL'} | {d.replace('|', '/')} |" for c, ok, d in ingestion]
    failed = [r for r in rows if not r["passed"]]
    lines += ["", "## Failures", ""]
    if not failed:
        lines.append("None.")
    for r in failed:
        lines.append(f"- **{r['q_id']}** ({r['category']}): expected {r['expected_answer_type']}, got {r['answer_type']}; "
                     f"missing sources {r['missing_sources']}, missing tools {r['missing_tools']}, "
                     f"missing text {r['missing_include']}, forbidden text {r['forbidden_found']}")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(f"{k}: {v}" for k, v in metrics.items()))
    print(f"\nReport: {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
