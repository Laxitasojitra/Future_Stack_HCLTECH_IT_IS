"""Emergency path and refusals (run before any routine task)."""

from app.agents.base import policy_citations, policy_text, refused, result
from app.fmt import clean_clause, nice_date
from app.tools import AccessDenied


def emergency(signs):
    """SOP-EMR-001 4.1: direct the patient to emergency care and stop routine tasks."""
    sign_text = "; ".join(clean_clause(policy_text(f"SOP-EMR-001#{s}")).rstrip(".") for s in signs)
    answer = (
        "This may be a medical emergency. Please go to the Emergency Department now (Block C, Ground Floor, open "
        "24 hours) or call the hospital emergency line 1800-123-4567 (Ext. 1000) or the national ambulance "
        f"service 108.\n\nWarning sign you described: {sign_text}. If you are not at the hospital, call 108 "
        "immediately and do not drive yourself.\n\nI have paused routine requests so you can get help first."
    )
    ids = [f"SOP-EMR-001#{s}" for s in signs] + ["SOP-EMR-001#1.1", "SOP-EMR-001#1.2", "SOP-EMR-001#4.1"]
    return result(answer, "retrieved_fact", policy_citations(ids),
                  explanation="Emergency warning sign detected; emergency guidance given before any routine task.",
                  data={"emergency_signs": signs}, rephrase=False)


def privacy_refusal(signals, session):
    relation_claimed = bool(signals.get("third_party"))
    answer = ("I can only share information from your own records, so I can't show another person's "
              "appointments, results, bills or other details, even if you give their name or patient ID.")
    ids = ["POL-PRIV-001#4.1", "POL-PRIV-001#4.2"]
    if relation_claimed:
        answer += (" A family member or caregiver can get access only if the patient registers them as an "
                   "authorised proxy at the Medical Records Department.")
        ids.insert(0, "POL-PRIV-001#3.1")
    answer += " I'm happy to help with your own records or with general hospital information."
    return refused(answer, policy_citations(ids),
                   explanation="Request for another person's information without registered proxy access (R7).")


def staff_refusal():
    return refused("Patient records are available to staff in the hospital's clinical systems according to their "
                   "role. This assistant only shares a patient's records with that patient or their registered "
                   "proxy. I can still answer policy and procedure questions.",
                   policy_citations(["POL-PRIV-001#3.2", "POL-PRIV-001#4.1"]),
                   explanation="Staff role without patient context in this channel.")


def injection_refusal():
    return refused("I can't follow instructions that try to change how I work or give access to other people's "
                   "data. I can help with your own appointments, results, bills, insurance questions and hospital "
                   "policies.", policy_citations(["POL-PRIV-001#4.1", "POL-PRIV-001#4.2"]),
                   explanation="Prompt-injection attempt detected in the message (R8).")


def medication_change(tools, session):
    """Changing or pausing a medicine is a clinical decision (PROT-PREOP-004 7.2)."""
    answer = ("I can't advise you on changing or pausing any of your medicines; that is a clinical decision for your "
              "doctor. Please ask your surgeon, anaesthetist or the doctor who prescribed the medicine before you "
              "change anything.")
    try:
        upcoming = [a for a in tools.get_appointments(session.subject_patient_id)
                    if a["appointment_type"] in ("clearance", "consultation", "follow-up")]
    except AccessDenied:
        upcoming = []
    if upcoming:
        a = upcoming[0]
        answer += (f" You can raise it at your {a['reason_for_visit'].lower()} on "
                   f"{nice_date(a['appointment_date'], weekday=True)} with Dr. {a['doctor_name']}.")
    return refused(answer, policy_citations(["PROT-PREOP-004#7.2"]),
                   explanation="Medication changes are clinical decisions; referred to the treating doctor.")
