"""Appointment agent: view, reschedule and book. Writes need the patient's confirmation first."""

import re
import uuid

from app.agents.base import policy_citations, result
from app.fmt import SYSTEMS, nice_date, nice_time, system_citation

APPT_SYSTEM = system_citation(SYSTEMS["appointments"])


def _describe(a):
    dept = a.get("department_name") or ""
    doc = f" with Dr. {a['doctor_name']}" if a.get("doctor_name") else ""
    return (f"{dept}: {a['reason_for_visit']}{doc} on {nice_date(a['appointment_date'], weekday=True)} at "
            f"{nice_time(a['appointment_time'])}, {a['location']}")


def view(tools, patient_id):
    appts = tools.get_appointments(patient_id)
    if not appts:
        return result("You have no upcoming appointments in the appointment system.", "retrieved_fact",
                      [APPT_SYSTEM], "Retrieved from the appointment system.")
    lines = [f"- {_describe(a)} (ref {a['appointment_id']})" for a in appts]
    return result(f"You have {len(appts)} upcoming appointment{'s' if len(appts) > 1 else ''}:\n" + "\n".join(lines),
                  "retrieved_fact", [APPT_SYSTEM], "Retrieved from the patient appointment management system.",
                  data={"appointments": [a["appointment_id"] for a in appts]})


def _slot_options(slots):
    return [{"option": i + 1, "slot_id": s["slot_id"], "date": s["slot_date"], "time": s["slot_time"],
             "doctor": f"Dr. {s['doctor']['first_name']} {s['doctor']['last_name']}" if s.get("doctor") else "",
             "label": f"{nice_date(s['slot_date'], weekday=True)} at {nice_time(s['slot_time'])}"
                      + (f" with Dr. {s['doctor']['first_name']} {s['doctor']['last_name']}" if s.get("doctor") else "")}
            for i, s in enumerate(slots)]


def _surgery_deadline(tools, patient_id, appt):
    """If the appointment is a pre-op step (ECG, PAC, consent), new slots must be before surgery."""
    surgeries = tools.get_surgery_details(patient_id)
    if surgeries and appt["appointment_type"] in ("diagnostic", "clearance", "consultation") and \
            re.search(r"pre-?(?:operative|anesthesia|surgery)", appt["reason_for_visit"], re.IGNORECASE):
        return surgeries[0]
    return None


def reschedule(tools, patient_id, question, department=None):
    appts = tools.get_appointments(patient_id, department=department)
    if not appts:
        what = f"{department} " if department else ""
        return result(f"I couldn't find an upcoming {what}appointment to reschedule.", "not_found", [APPT_SYSTEM],
                      "No matching upcoming appointment.", rephrase=False)
    if len(appts) > 1:
        lines = [f"{i + 1}. {_describe(a)}" for i, a in enumerate(appts)]
        return result("You have more than one matching appointment. Which one would you like to reschedule?\n"
                      + "\n".join(lines), "clarification_needed", [APPT_SYSTEM],
                      "Several appointments match; asking which one before changing anything.",
                      data={"candidates": [a["appointment_id"] for a in appts]}, rephrase=False)
    appt = appts[0]
    surgery = _surgery_deadline(tools, patient_id, appt)
    slots = tools.get_available_slots(doctor_id=appt["doctor_id"], before=surgery["scheduled_date"] if surgery else None)
    slots = [s for s in slots if (s["slot_date"], s["slot_time"]) != (appt["appointment_date"], appt["appointment_time"])]
    if not slots:
        return result(f"There are no free slots with Dr. {appt['doctor_name']} right now. Please call the "
                      f"{appt['department_name']} front desk.", "not_found", [APPT_SYSTEM], "No available slots.",
                      rephrase=False)
    options = _slot_options(slots)
    note = (f" Because this is part of your preparation for surgery on {nice_date(surgery['scheduled_date'])}, "
            f"I've only shown slots before that date." if surgery else "")
    pending = {"action_id": uuid.uuid4().hex[:10], "type": "reschedule", "patient_id": patient_id,
               "appointment_id": appt["appointment_id"], "options": options,
               "summary": f"Reschedule {appt['department_name']} appointment {appt['appointment_id']}"}
    text = (f"Your current appointment: {_describe(appt)}.{note}\n\nAvailable slots:\n"
            + "\n".join(f"{o['option']}. {o['label']}" for o in options)
            + "\n\nPlease confirm which slot you want (for example, \"confirm option 1\"). Nothing is changed until "
              "you confirm.")
    return result(text, "clarification_needed", [APPT_SYSTEM], "Showing available slots; waiting for confirmation.",
                  pending_action=pending, rephrase=False)


def book_missed_care_item(tools, patient_id, question):
    """Rebook a missed follow-up from the care plan (POL-DIS-003 5.2)."""
    plans = tools.check_care_plan_compliance(patient_id)
    missed = [it for p in plans for it in p["items"] if it["compliance"] in ("missed", "overdue")]
    words = set(re.findall(r"[a-z]+", question.lower()))
    match = [it for it in missed if words & set(re.findall(r"[a-z]+", f"{it['item_type']} {it['description']}".lower()))
             - {"session", "the", "of", "after", "and", "within", "days"}]
    item = (match or missed or [None])[0]
    if not item:
        return result("I couldn't find a missed session in your care plan. If you'd like a new appointment, tell me "
                      "which department.", "not_found", [system_citation(SYSTEMS["care_plan"])],
                      "No missed care-plan item.", rephrase=False)
    appt = item.get("appointment") or {}
    doctor_id = appt.get("doctor_id")
    department_id = appt.get("department_id")
    slots = tools.get_available_slots(doctor_id=doctor_id, department_id=department_id)
    if not slots:
        return result("There are no free slots right now; please call the department front desk.", "not_found",
                      [APPT_SYSTEM], "No available slots.", rephrase=False)
    options = _slot_options(slots)
    label = item["item_type"].replace("_", " ").title()
    pending = {"action_id": uuid.uuid4().hex[:10], "type": "book", "patient_id": patient_id,
               "reason": item["description"], "appointment_type": "therapy" if "physio" in item["item_type"] else "follow-up",
               "care_item_id": item["item_id"], "options": options, "label": label,
               "summary": f"Book {label}: {item['description']}"}
    text = (f"You missed: {item['description']} (due {nice_date(item['due_date'])}). Missed follow-ups can be rebooked "
            f"through this assistant.\n\nAvailable {label} slots:\n"
            + "\n".join(f"{o['option']}. {o['label']}" for o in options)
            + "\n\nPlease confirm which slot you want (for example, \"confirm option 1\").")
    return result(text, "clarification_needed",
                  [system_citation(SYSTEMS["care_plan"]), APPT_SYSTEM] + policy_citations(["POL-DIS-003#5.2"]),
                  "Missed care-plan item found; showing slots and waiting for confirmation.",
                  pending_action=pending, rephrase=False)


def execute(tools, pending, option):
    """Run a confirmed action and report action_completed."""
    chosen = next((o for o in pending["options"] if o["option"] == option), None)
    if not chosen:
        return result("That option isn't in the list. Please choose one of the numbered slots.", "clarification_needed",
                      [APPT_SYSTEM], "Invalid option.", pending_action=pending, rephrase=False)
    if pending["type"] == "reschedule":
        done = tools.reschedule_appointment(pending["patient_id"], pending["appointment_id"], chosen["slot_id"])
        if not done:
            return result("Sorry, that slot was just taken. Please ask again to see fresh slots.", "not_found",
                          [APPT_SYSTEM], "Slot no longer available.", rephrase=False)
        dept = done.get("department_id", "")
        text = (f"Done. As you confirmed, your appointment {done['appointment_id']} has been moved from "
                f"{nice_date(done['old_date'])} {nice_time(done['old_time'])} to "
                f"{nice_date(done['appointment_date'], weekday=True)} at {nice_time(done['appointment_time'])} with "
                f"Dr. {done['doctor']['first_name']} {done['doctor']['last_name']}, {done['location']}. "
                f"You'll get an SMS confirmation.")
        return result(text, "action_completed", [APPT_SYSTEM], "Rescheduling workflow executed after confirmation.",
                      data={"appointment_id": done["appointment_id"], "department": dept}, rephrase=False)
    booked = tools.book_appointment(pending["patient_id"], chosen["slot_id"], pending["reason"], pending["appointment_type"])
    if not booked:
        return result("Sorry, that slot was just taken. Please ask again to see fresh slots.", "not_found",
                      [APPT_SYSTEM], "Slot no longer available.", rephrase=False)
    text = (f"Booked, as you confirmed: {pending['label']} on {nice_date(booked['appointment_date'], weekday=True)} at "
            f"{nice_time(booked['appointment_time'])} with Dr. {booked['doctor']['first_name']} "
            f"{booked['doctor']['last_name']}, {booked['department_name']}, {booked['location']}. "
            f"Reference {booked['appointment_id']}. This replaces: {booked['reason_for_visit']}.")
    return result(text, "action_completed", [APPT_SYSTEM] + policy_citations(["POL-DIS-003#5.2"]),
                  "Booking workflow executed after confirmation.", data={"appointment_id": booked["appointment_id"]},
                  rephrase=False)


def parse_confirmation(message):
    """'confirm option 2', 'yes', '2', 'the first one' -> option number; 'no'/'cancel' -> 0; else None."""
    m = message.lower().strip()
    if re.search(r"\b(?:no|cancel|stop|don'?t|do not)\b", m):
        return 0
    ordinals = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3, "one": 1, "two": 2, "three": 3}
    d = re.search(r"\b([1-9])\b", m)
    if d:
        return int(d.group(1))
    for word, n in ordinals.items():
        if re.search(rf"\b{word}\b", m):
            return n
    if re.search(r"\b(?:yes|yeah|yep|ok|okay|sure|confirm|go ahead|book it|do it)\b", m):
        return 1
    return None
