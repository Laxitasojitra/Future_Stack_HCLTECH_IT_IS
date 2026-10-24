"""Care coordination agent (multi-step, R6): surgery readiness, pending pre-op tests and care-plan
follow-ups. It combines records, orders, results, pre-authorisation and the pre-op protocol, then
explains. It reports readiness status but never grants clinical clearance (PROT-PREOP-004 7.2)."""

import re

from app.agents.base import policy_citations, result
from app.fmt import SYSTEMS, nice_date, nice_time, system_citation
from app.kb.store import get_kb
from app.tools import find_procedure

EMR = system_citation(SYSTEMS["emr"])
CATEGORY_SECTION = {"minor": "3.1", "intermediate": "3.2", "major": "3.3"}


def _surgery(tools, patient_id, question):
    code = find_procedure(question)
    surgeries = tools.get_surgery_details(patient_id, procedure_code=code) or tools.get_surgery_details(patient_id)
    return surgeries[0] if surgeries else None


def _no_surgery():
    return result("I couldn't find a planned surgery in your record. If your doctor has recommended surgery, the "
                  "surgical team will create the plan and order the pre-operative tests.", "not_found", [EMR],
                  "No scheduled surgery on record.", rephrase=False)


def _item_line(it):
    if it["status"] == "complete":
        return f"{it['item_name']} - done {nice_date(it['completed_date'])}"
    if it["status"] == "auto":
        return f"{it['item_name']} - {it['detail']}"
    if it["status"] == "expired":
        return f"{it['item_name']} - result from {nice_date(it['completed_date'])} is {it['detail']}; needs repeating"
    if it.get("item_type") == "financial":
        detail = re.sub(r"\d{4}-\d{2}-\d{2}", lambda m: nice_date(m.group(0)), it["detail"])
        return f"{it['item_name']} - {detail}"
    if it.get("scheduled_date"):
        verb = "scheduled for" if it["detail"] == "scheduled" else "pending, planned for"
        return f"{it['item_name']} - {verb} {nice_date(it['scheduled_date'], weekday=True)}"
    return f"{it['item_name']} - {it['detail']}"


def readiness(tools, patient_id, question):
    s = _surgery(tools, patient_id, question)
    if not s:
        return _no_surgery()
    pre = tools.get_preop_orders(patient_id, s)
    tools.get_lab_results(patient_id, [r["item_code"] for r in pre["reviews"]] or ["HBA1C", "INR", "CREAT"])
    pas = tools.get_preauth_status(patient_id, surgery_id=s["surgery_id"])
    items, reviews = pre["items"], pre["reviews"]
    tests = [i for i in items if i["item_type"] in ("lab", "cardiac", "imaging")]
    clearances = [i for i in items if i["item_type"] in ("clearance", "financial")]
    open_items = [i for i in items if i["status"] in ("pending", "expired")]
    ready = not open_items and not reviews

    proc = s["procedure"]["procedure_name"]
    head = (f"Your {proc.lower()} is planned for {nice_date(s['scheduled_date'], weekday=True)} at "
            f"{nice_time(s['scheduled_time'])} with Dr. {s['surgeon']['first_name']} {s['surgeon']['last_name']}.")
    lines = [head]
    if ready:
        lines.append("Administratively, you are ready: all required pre-operative tests are complete and valid, and "
                     "all clearances (pre-anesthesia check-up, signed consent and pre-authorisation) are complete.")
    else:
        lines.append("You are not yet ready for surgery. These items are still open:")
        for it in open_items:
            lines.append(f"- {_item_line(it)}")
        for rv in reviews:
            name = rv.get("test_name", rv["item_code"])
            unit = "" if rv["unit"] in ("", "ratio") else ("%" if rv["unit"] == "%" else f" {rv['unit']}")
            lines.append(f"- {name} result {rv['value']}{unit} is above {rv['threshold']}{unit} and needs review by "
                         f"the {rv['reviewer'].lower()} before clearance")
    done = [i["item_name"] for i in tests + clearances if i["status"] == "complete"]
    if done:
        lines.append("Already complete: " + ", ".join(done) + ".")
    auto = [i for i in items if i["status"] == "auto"]
    if auto:
        lines.append(" ".join(f"{i['item_name']} is {i['detail']} within 72 hours before surgery; you don't need to book it."
                              for i in auto))
    lines.append("Final clinical clearance for surgery is given only by your operating surgeon and anaesthetist "
                 f"(Dr. {s['anesthetist']['first_name']} {s['anesthetist']['last_name']}). I can report status but "
                 "cannot clear you for surgery.")

    sec = CATEGORY_SECTION[s["surgery_category"]]
    ids = [f"PROT-PREOP-004#{sec}", "PROT-PREOP-004#4.1"]
    if auto or any(i["item_code"] == "BLOOD_GROUP" for i in items):
        ids.append("PROT-PREOP-004#4.3")
    ids += [rv["source"] for rv in reviews] + ["PROT-PREOP-004#6.1", "PROT-PREOP-004#6.2", "PROT-PREOP-004#6.3",
                                               "PROT-PREOP-004#7.1", "PROT-PREOP-004#7.2"]
    citations = [EMR, system_citation(SYSTEMS["lab"]), system_citation(SYSTEMS["insurance"])] + policy_citations(ids)
    return result("\n".join(lines), "calculated", citations,
                  "Readiness calculated by combining orders, results, clearances and pre-authorisation against "
                  "PROT-PREOP-004; clinical clearance is not granted by the assistant.",
                  data={"surgery_id": s["surgery_id"], "ready": ready, "open_items": [i["item_code"] for i in open_items],
                        "reviews": [r["item_code"] for r in reviews], "preauth": pas[0]["status"] if pas else None})


def pending_tests(tools, patient_id, question):
    s = _surgery(tools, patient_id, question)
    if not s:
        return _no_surgery()
    pre = tools.get_preop_orders(patient_id, s)
    items = pre["items"]
    tests = [i for i in items if i["item_type"] in ("lab", "cardiac", "imaging")]
    pending = [i for i in tests if i["status"] in ("pending", "expired")]
    auto = [i for i in tests if i["status"] == "auto"]
    complete = [i for i in tests if i["status"] == "complete"]
    clear_open = [i for i in items if i["item_type"] in ("clearance", "financial") and i["status"] != "complete"]

    proc = s["procedure"]["procedure_name"].lower()
    if pending:
        lines = [f"For your {proc} on {nice_date(s['scheduled_date'], weekday=True)}, "
                 f"{'this pre-operative test is' if len(pending) == 1 else 'these pre-operative tests are'} still pending:"]
        lines += [f"- {_item_line(i)}" for i in pending]
    else:
        lines = [f"All required pre-operative tests for your {proc} are complete and valid."]
    if auto:
        lines.append(" ".join(f"{i['item_name']} is {i['detail']} within 72 hours before surgery, so you don't need to "
                              f"book it." for i in auto))
    if complete:
        lines.append("Complete and valid within the protocol's time limits: " +
                     ", ".join(i["item_name"] for i in complete) + ".")
    if clear_open:
        lines.append("Separately, these clearances are still open: " + "; ".join(_item_line(i) for i in clear_open) + ".")
    sec = CATEGORY_SECTION[s["surgery_category"]]
    ids = [f"PROT-PREOP-004#{sec}", "PROT-PREOP-004#4.1", "PROT-PREOP-004#4.3"]
    if clear_open:
        ids += ["PROT-PREOP-004#6.1", "PROT-PREOP-004#6.2", "PROT-PREOP-004#6.3"]
    return result("\n".join(lines), "calculated", [EMR] + policy_citations(ids),
                  "Pending tests calculated from orders against PROT-PREOP-004.",
                  data={"pending": [i["item_code"] for i in pending]})


def care_plan(tools, patient_id):
    plans = tools.check_care_plan_compliance(patient_id)
    if not plans:
        return result("You don't have an active care plan on record.", "calculated",
                      [system_citation(SYSTEMS["care_plan"])], "No active care plan.")
    lines, ids = [], []
    missed_total = 0
    for p in plans:
        missed = [i for i in p["items"] if i["compliance"] in ("missed", "overdue")]
        upcoming = [i for i in p["items"] if i["compliance"] in ("scheduled", "pending")]
        missed_total += len(missed)
        lines.append(f"{p['plan']['plan_name']}:")
        for i in missed:
            lines.append(f"- Missed: {i['description']} (due {nice_date(i['due_date'])})")
        for i in upcoming:
            lines.append(f"- Coming up: {i['description']} ({i['status']}, from {nice_date(i['due_date'])})")
        if p["plan"].get("source_doc_id"):
            for i in missed + upcoming[:1]:
                hits = get_kb().search(i["description"], doc_ids=[p["plan"]["source_doc_id"]], k=1)
                if hits:
                    ids.append(hits[0]["section_id"])
    intro = (f"Yes, you have missed {missed_total} item{'s' if missed_total != 1 else ''} in your care plan."
             if missed_total else "You're up to date: no missed items in your care plan.")
    tail = ("\n\nYou can rebook a missed session through this assistant - for example, \"book the physiotherapy "
            "session I missed\"." if missed_total else "")
    if missed_total:
        ids.append("POL-DIS-003#5.2")
    return result(intro + "\n" + "\n".join(lines) + tail, "calculated",
                  [system_citation(SYSTEMS["care_plan"])] + policy_citations(ids),
                  "Compliance calculated from care-plan items against today's date.",
                  data={"missed": missed_total})


# ---------------------------------------------------------------- child vaccinations

IMMUNIZATION = system_citation("Immunization record (EMR)")


def _age_text(days):
    years, rem = divmod(days, 365)
    months = rem // 30
    if years:
        return f"{years} year{'s' if years > 1 else ''}" + (f" {months} month{'s' if months != 1 else ''}" if months else "")
    return f"{months} month{'s' if months != 1 else ''}" if months else f"{days} days"


def vaccinations(tools, patient_id, is_guardian=True):
    """Vaccination status: given, due now, overdue and next, from the child's record and the official schedule.
    Due dates come from the date of birth and the schedule table (deterministic). The pediatrician decides
    what is actually given at the visit."""
    tools.get_immunization_record(patient_id)
    status = tools.check_vaccines_due(patient_id)
    if not status.get("available"):
        return result("I couldn't find a vaccination record and schedule for this patient in the hospital system. "
                      "Please contact the Pediatrics front desk (Ext. 2820).", "not_found", [IMMUNIZATION],
                      "Immunization record or schedule not available.", rephrase=False)
    child, items = status["child"], status["items"]
    whose = f"{child['first_name']}'s" if is_guardian else "Your"
    by = {k: [i for i in items if i["status"] == k] for k in ("given", "due", "overdue", "upcoming", "past_catch_up")}

    def name(i):
        return f"{i['vaccine']} (dose {i['dose']})" if str(i["dose"]).isdigit() else f"{i['vaccine']} ({i['dose']})"

    lines = [f"{whose} vaccination status at age {_age_text(status['age_days'])}, from the immunization record and "
             f"the official schedule:"]
    if by["overdue"]:
        lines.append("- Overdue: " + "; ".join(f"{name(i)}, due from {nice_date(i['due_date'])}" for i in by["overdue"]))
    if by["due"]:
        lines.append("- Due now: " + "; ".join(name(i) for i in by["due"]))
    if not by["overdue"] and not by["due"]:
        lines.append("- Nothing is due right now.")
    if by["upcoming"]:
        nxt = by["upcoming"][0]
        same = [i for i in by["upcoming"] if i["recommended_age_days"] == nxt["recommended_age_days"]]
        lines.append(f"- Next: {', '.join(name(i) for i in same)} from {nice_date(nxt['due_date'])}")
    if by["past_catch_up"]:
        lines.append("- Past the schedule's catch-up age (ask the pediatrician): "
                     + "; ".join(name(i) for i in by["past_catch_up"]))
    lines.append(f"- Already given: {len(by['given'])} dose{'s' if len(by['given']) != 1 else ''}"
                 + (f" (latest {name(by['given'][-1])} on {nice_date(by['given'][-1]['date_given'])})" if by["given"] else ""))
    lines.append("\nThe pediatrician will confirm which vaccines to give at the visit, especially if the child is unwell "
                 "or had a reaction before. I can book a Pediatrics appointment for you.")
    sources = list(dict.fromkeys(f"{i['source_doc_id']}#{i['source_section']}" for i in items
                                 if i.get("source_doc_id") and i["status"] in ("due", "overdue", "upcoming")))
    return result("\n".join(lines), "calculated", [IMMUNIZATION] + policy_citations(sources[:4]),
                  "Due and overdue doses calculated from date of birth, the immunization record and the schedule table.",
                  data={"overdue": [name(i) for i in by["overdue"]], "due": [name(i) for i in by["due"]],
                        "given": len(by["given"])})
