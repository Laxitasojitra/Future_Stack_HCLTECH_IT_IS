"""Medication safety agent: interaction check against the approved reference (R5). Never gives dosing
advice; every finding ends with a referral to the doctor or pharmacist."""

from app.agents.base import not_found, result
from app.fmt import SYSTEMS, system_citation

REFERENCE = system_citation(SYSTEMS["pharmacy_ref"], "Synthetic formulary interaction reference v1 (demo only)")
PHARMACY = system_citation(SYSTEMS["pharmacy"])


def answer(tools, patient_id, question):
    mentioned = tools.find_drugs(question)
    if not mentioned:
        return not_found("Medicine not found in the hospital formulary.", [REFERENCE])
    citations = [REFERENCE]
    if len(mentioned) >= 2:
        checked = mentioned
        pairs_note = " and ".join(mentioned)
    else:
        active = tools.get_medications(patient_id)
        current = [m["drug_name"] for m in active if m["drug_name"].lower() != mentioned[0].lower()]
        checked = mentioned + current
        citations.append(PHARMACY)
        pairs_note = f"{mentioned[0]} and your current medicines ({', '.join(current) or 'none on record'})"
    found = tools.drug_interaction_check(checked)
    relevant = [f for f in found if any(m.lower() in (f["drug_a"].lower(), f["drug_b"].lower()) for m in mentioned)]

    if not relevant:
        text = (f"The hospital's interaction reference lists no interaction between {pairs_note}. That doesn't replace "
                f"a check by your doctor or pharmacist, so please confirm with them before starting a new medicine.")
    else:
        lines = [f"- {f['drug_a']} + {f['drug_b']}: {f['severity']} interaction. {f['effect']} "
                 f"Recommended: {f['recommended_action']}" for f in relevant]
        text = (f"I checked {pairs_note} against the hospital's drug interaction reference and found "
                f"{len(relevant)} listed interaction{'s' if len(relevant) > 1 else ''}:\n" + "\n".join(lines) +
                "\n\nPlease talk to your doctor or pharmacist before taking these together, and don't change how you "
                "take any prescribed medicine without their advice.")
    return result(text, "calculated", citations, "Interaction check by the deterministic medication safety tool.",
                  data={"checked": checked, "interactions": [f["interaction_id"] for f in relevant]})
