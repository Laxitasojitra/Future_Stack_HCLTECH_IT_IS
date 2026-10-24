"""Test ONLY the policy agent, step by step.

Put this file in the hospital-assistant folder (next to README.md) and run:
    python check_policy.py                      -> runs the 6 demo questions
    python check_policy.py "your own question"  -> runs just your question
"""
import os
import sys

os.environ.setdefault("EMBED_BACKEND", "hashing")   # no MiniLM download needed
os.environ["LLM_PROVIDER"] = "none"                 # no LLM: pure policy agent output

from app import db                       # noqa: E402
from app.agents import policy            # noqa: E402
from app.kb.store import get_kb          # noqa: E402

QUESTIONS = [
    "What are the visiting hours for ICU patients?",                 # normal fact, current version
    "How many visitors can my father have in the ICU per day?",      # government advisory overrides hospital
    "How long does a refund of excess deposit take?",                # draft document must be ignored
    "How long does an HbA1c report take?",                           # table row picked
    "How long should I fast before my treadmill stress test?",       # conflict_flagged
    "What is your protocol for proton beam therapy?",                # not_found
]

db.init_db()
kb = get_kb()
questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else QUESTIONS

for q in questions:
    print("=" * 90)
    print("QUESTION:", q)

    # Step 1: what hybrid search found (top 3 clauses in force today)
    print("\n  STEP 1 - search results (best first):")
    for h in kb.search(q, k=3):
        print(f"    {h['section_id']:<24} v{h['version']:<5} score={h['score']:.2f} "
              f"coverage={h['coverage']:.2f} dense={h['dense']:.2f} bm25={h['bm25']:.1f}")

    # Steps 2-5: the agent itself
    r = policy.answer(q)
    print("\n  ANSWER TYPE :", r["answer_type"])
    print("  ANSWER      :", r["answer"].replace("\n", "\n                "))
    print("  EXPLANATION :", r["explanation"])
    for c in r["citations"]:
        print(f"  CITATION    : {c['id']}  v{c.get('version', '')}  ({c['role']})")
    print()