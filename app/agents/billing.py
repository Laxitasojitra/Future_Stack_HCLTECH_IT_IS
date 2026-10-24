"""Billing agent: outstanding balance and refund status from the billing system."""

from app.agents.base import policy_citations, result
from app.fmt import SYSTEMS, inr, nice_date, num, system_citation

BILLING = system_citation(SYSTEMS["billing"])


def balance(tools, patient_id):
    bills = tools.get_billing_summary(patient_id)
    due = [b for b in bills if (num(b["balance_due"], 0) or 0) > 0 and b["bill_type"] == "final"]
    total = sum(num(b["balance_due"], 0) for b in due)
    estimates = [b for b in bills if b["bill_type"] == "estimate"]
    if not due:
        text = "You have no outstanding balance with the hospital."
    else:
        lines = [f"- {b['description']} (bill {b['bill_id']}, {nice_date(b['bill_date'])}): total {inr(b['amount'])}, "
                 f"paid {inr(b['amount_paid'])}, balance {inr(b['balance_due'])} ({b['payment_status']})" for b in due]
        text = f"You owe the hospital {inr(total)} in total:\n" + "\n".join(lines)
        text += ("\n\nYou can pay by UPI, debit or credit card, net banking, or cash (below ₹2,00,000 per transaction) at "
                 "the Billing Desk, Ground Floor, Block A.")
    if estimates:
        text += "\n\nEstimates for planned treatment are not due yet: " + "; ".join(
            f"{b['description']} (your share about {inr(b['patient_amount'])})" for b in estimates) + "."
    return result(text, "calculated", [BILLING] + policy_citations(["POL-BIL-003#3.1", "POL-BIL-003#5.2"]),
                  "Balance calculated from the billing system.", data={"balance_due": total})


def refund(tools, patient_id):
    rows = tools.get_refund_status(patient_id)
    if not rows:
        return result("There is no refund in progress on your account.", "retrieved_fact",
                      [BILLING] + policy_citations(["POL-BIL-003#4.1"]), "Retrieved from the billing system.")
    r = rows[0]
    text = (f"Your refund of {inr(r['amount_inr'])} (bill {r['bill_id']}) was initiated on {nice_date(r['initiated_date'])} "
            f"to your original payment method ({r['refund_mode']}). Status: {r['status']}. It is expected by "
            f"{nice_date(r['expected_by'])}, in line with the policy of refunding excess deposits within 7 working days "
            f"of the final bill.")
    return result(text, "retrieved_fact", [BILLING] + policy_citations(["POL-BIL-003#4.1", "POL-BIL-003#4.2"]),
                  "Retrieved from the billing system.", data={"refund_id": r["refund_id"], "status": r["status"]})
