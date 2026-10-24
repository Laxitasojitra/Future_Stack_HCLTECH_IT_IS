"""Test ONLY the billing agent, step by step.

Put this file in the hospital-assistant folder (next to README.md) and run:
    python check_billing.py
"""
import os

os.environ.setdefault("EMBED_BACKEND", "hashing")
os.environ["LLM_PROVIDER"] = "none"

from app import auth, db                    # noqa: E402
from app.agents import billing              # noqa: E402
from app.kb.store import get_kb             # noqa: E402
from app.tools import AccessDenied, ToolBox  # noqa: E402

db.init_db()
get_kb()   # billing cites policy clauses, so the knowledge base must be loaded


def show(title, r):
    print(f"\n  {title}")
    print("  ANSWER TYPE :", r["answer_type"])
    print("  ANSWER      :", r["answer"].replace("\n", "\n                "))
    print("  EXPLANATION :", r["explanation"])
    for c in r["citations"]:
        print(f"  CITATION    : {c['id']}  {('v' + str(c['version'])) if c.get('version') else ''}")


for user_id in ["U-P057", "U-P060", "U-P051"]:
    session = auth.session_for_user(user_id)
    pid = session.subject_patient_id
    tools = ToolBox(session, None)
    print("=" * 90)
    print(f"LOGGED IN AS {user_id}  (patient {pid})")

    # Step 1: the raw rows the tool returns (what the agent works from)
    print("\n  STEP 1 - bills from the billing system:")
    for b in tools.get_billing_summary(pid):
        print(f"    {b['bill_id']:<8} {b['bill_type']:<9} total={b['amount']:<9} paid={b['amount_paid']:<9} "
              f"balance={b['balance_due']:<9} {b['payment_status']}")
    print("  STEP 1 - refunds:")
    for r in tools.get_refund_status(pid) or [{"refund_id": "(none)"}]:
        print("   ", {k: r[k] for k in r if k in ("refund_id", "amount_inr", "status", "expected_by")})

    # Step 2: the agent's answers
    show('Q: "How much do I owe the hospital?"', billing.balance(tools, pid))
    show('Q: "Where is my refund?"', billing.refund(tools, pid))
    print()

# Privacy check: a patient must NOT be able to read another patient's bills
print("=" * 90)
print("PRIVACY TEST: U-P057 tries to read P060's bills")
tools = ToolBox(auth.session_for_user("U-P057"), None)
try:
    tools.get_billing_summary("P060")
    print("  FAIL - data was returned!")
except AccessDenied as e:
    print("  PASS - blocked:", e)
