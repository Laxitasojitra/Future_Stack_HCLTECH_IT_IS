"""Department finder and help text."""

from app.agents.base import not_found, policy_citations, result
from app.fmt import SYSTEMS, system_citation


def department(tools, question):
    d = tools.find_department(question)
    if not d:
        return not_found("No department in the directory matches the question.", policy_citations(["DIR-001#2"]))
    text = (f"For that, visit {d['department']} ({d['location']}, Central Hospital; front desk {d['front_desk']}). "
            f"{d['department']} handles: {d['reasons'].lower()}. I can also book a consultation for you.")
    return result(text, "retrieved_fact", policy_citations(["DIR-001#2"]) + [system_citation(SYSTEMS["directory"])],
                  "Matched against the hospital's Departments and Services Directory.", data={"department": d})


def help_text(session):
    who = session.display_name.split(" (")[0]
    text = (f"Hello {who}. I can help with:\n"
            "- Hospital policies and procedures (visiting hours, admission, discharge, test preparation)\n"
            "- Your appointments: view, reschedule or book (I always ask before changing anything)\n"
            "- Your lab results and reports\n"
            "- Insurance coverage, pre-authorisation and cost estimates\n"
            "- Your bills and refunds\n"
            "- Medicine interaction checks\n"
            "- Surgery readiness and care-plan follow-ups\n\n"
            "If you have a medical emergency, go to the Emergency Department or call 108.")
    return result(text, "retrieved_fact", policy_citations(["SOP-EMR-001#1.2"]), "Capabilities overview.",
                  rephrase=False)
