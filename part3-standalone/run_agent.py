"""Run Person 3's agents on their own: Care coordination + Appointment (with the department finder).

    python run_agent.py                      # chat as U-P051
    python run_agent.py --user U-P060        # chat as another demo user
    python run_agent.py --demo               # run the scripted demo for all scenario patients
    python run_agent.py --toy-vaccines       # adds a TEST-ONLY 2-year-old (login U-G900) with made-up vaccines

Only pandas is needed. Teammates' parts are replaced by small stand-ins (simple keyword policy lookup,
no login password, no LLM, no verifier), so this checks your agents' logic, not the full system.
"""

import argparse
import re
from datetime import timedelta

from app import config, db
from app.agents import appointment, care, directory
from app.auth import session_for_user
from app.tools import AccessDenied, ToolBox

RULES = [
    ("vaccination", r"\b(?:vaccin\w*|immuni[sz]\w*|boosters?|shots?)\b"),
    ("book", r"\b(?:book|rebook|re-book)\b"),
    ("reschedule", r"\b(?:reschedule|postpone|move|shift|change)\b.{0,40}\bappointment"),
    ("readiness", r"\b(?:ready|cleared|proceed|go\s+ahead)\b.{0,40}\bsurgery|\bsurgery\b.{0,30}\bready\b|everything\s+ready"),
    ("preop_pending", r"(?:pre-?op(?:erative)?|before\s+(?:my\s+)?surgery).{0,40}\btests?|\bpending\b.{0,30}\btests?"),
    ("care_plan", r"\bmiss(?:ed)?\b|\bcare\s+plan\b|\bfollow-?ups?\b"),
    ("appointments", r"\bappointments?\b"),
    ("department", r"\bwhich\s+(?:department|doctor|specialist)\b|\bwho\s+should\s+i\s+see\b"),
]


def route(question):
    for intent, pattern in RULES:
        if re.search(pattern, question, re.IGNORECASE):
            return intent
    return None


class Chat:
    def __init__(self, user_id):
        self.session = session_for_user(user_id)
        self.pending = None

    def ask(self, question):
        tools = ToolBox(self.session)
        pid = self.session.subject_patient_id
        try:
            if self.pending and appointment.parse_confirmation(question) is not None:
                option = appointment.parse_confirmation(question)
                pending, self.pending = self.pending, None
                if option == 0:
                    return {"answer": "Okay, I haven't changed anything.", "answer_type": "clarification_needed",
                            "citations": []}, tools
                return appointment.execute(tools, pending, option), tools
            self.pending = None
            intent = route(question)
            if intent == "vaccination":
                r = care.vaccinations(tools, pid, is_guardian=self.session.role != "patient")
            elif intent == "readiness":
                r = care.readiness(tools, pid, question)
            elif intent == "preop_pending":
                r = care.pending_tests(tools, pid, question)
            elif intent == "book":
                r = appointment.book_missed_care_item(tools, pid, question)
            elif intent == "reschedule":
                dept = next((d["department_name"] for d in tools.departments()
                             if d["department_name"].lower() in question.lower()), None)
                r = appointment.reschedule(tools, pid, question, department=dept)
            elif intent == "care_plan":
                r = care.care_plan(tools, pid)
            elif intent == "appointments":
                r = appointment.view(tools, pid)
            elif intent == "department":
                r = directory.department(tools, question)
            else:
                r = {"answer": "This standalone build only has the care coordination and appointment agents. Try: "
                               "'Can I proceed with surgery next week?', 'What are my upcoming appointments?', "
                               "'Reschedule my cardiology appointment.', 'Have I missed any follow-ups?'",
                     "answer_type": "not_found", "citations": []}
        except AccessDenied:
            r = {"answer": "I can only share information from your own records.", "answer_type": "refused",
                 "citations": []}
        if r.get("pending_action"):
            self.pending = r["pending_action"]
        return r, tools


def show(r, tools):
    print(f"\n[{r['answer_type']}]\n{r['answer']}")
    cites = [c["id"] + (f" (v{c['version']})" if c.get("version") else "") for c in r.get("citations", [])]
    if cites:
        print("Sources: " + "; ".join(cites))
    if tools.calls:
        print("Tools:   " + ", ".join(f"{c['tool']}[{c['status']}]" for c in tools.calls))


def add_toy_vaccines():
    """TEST-ONLY data: a 2-year-old with made-up vaccine names. Real schedules must come from the official
    immunization schedule (Person 1 loads it in the full system)."""
    dob = (config.AS_OF_DATE - timedelta(days=730)).isoformat()
    db.execute("INSERT INTO patients (patient_id, first_name, last_name, gender, date_of_birth, age) "
               "VALUES ('P900', 'Aanya', 'Test', 'F', ?, '2')", (dob,))
    db.execute("INSERT INTO users (user_id, role, display_name, patient_id, proxy_for_patient_id, department_id, "
               "can_upload_documents) VALUES ('U-G900', 'proxy', 'Test parent of P900', '', 'P900', '', 'N')")
    db.execute("CREATE TABLE immunization_schedule (schedule_id TEXT, vaccine TEXT, dose TEXT, recommended_age_days TEXT, "
               "catch_up_until_days TEXT, source_doc_id TEXT, source_section TEXT)")
    db.execute("CREATE TABLE immunizations (record_id TEXT, patient_id TEXT, vaccine TEXT, dose TEXT, date_given TEXT)")
    for row in [("S1", "Test vaccine A", "1", "42"), ("S2", "Test vaccine A", "2", "70"),
                ("S3", "Test vaccine B", "1", "270"), ("S4", "Test vaccine C", "booster", "480"),
                ("S5", "Test vaccine D", "1", "1825")]:
        db.execute("INSERT INTO immunization_schedule VALUES (?, ?, ?, ?, '', '', '')", row)
    for rid, dose, days_ago in [("R1", "1", 680), ("R2", "2", 655)]:
        db.execute("INSERT INTO immunizations VALUES (?, 'P900', 'Test vaccine A', ?, ?)",
                   (rid, dose, (config.AS_OF_DATE - timedelta(days=days_ago)).isoformat()))


DEMO = [
    ("U-P051", "What are my upcoming appointments?"),
    ("U-P051", "Can I proceed with surgery next week?"),
    ("U-P051", "My doctor recommended surgery. What pre-operative tests are still pending?"),
    ("U-P054", "Am I ready for my surgery?"),
    ("U-P055", "Is everything ready for my hernia surgery?"),
    ("U-P060", "Have I missed any follow-ups after my surgery?"),
    ("U-P060", "Book the physiotherapy session I missed."),
    ("U-P060", "confirm option 1"),
    ("U-P051", "Reschedule my cardiology appointment."),
    ("U-P051", "confirm option 2"),
    ("U-P059", "Reschedule my cardiology appointment."),
    ("U-P052", "Which department should I visit for thyroid problems?"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="U-P051")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--toy-vaccines", action="store_true")
    args = parser.parse_args()

    db.init_db(reset=True)                   # fresh copy of the data every run
    if args.toy_vaccines:
        add_toy_vaccines()
        if args.user == "U-P051" and not args.demo:
            args.user = "U-G900"

    if args.demo:
        chats = {}
        script = DEMO + ([("U-G900", "Which vaccines does my daughter need now?")] if args.toy_vaccines else [])
        for user, q in script:
            chat = chats.setdefault(user, Chat(user))
            print(f"\n{'=' * 90}\n{user} ({chat.session.display_name}): {q}")
            show(*chat.ask(q))
        return

    chat = Chat(args.user)
    print(f"Logged in as {chat.session.user_id} ({chat.session.display_name}), as of {config.AS_OF_DATE}. "
          f"Type 'quit' to exit.")
    while True:
        try:
            q = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("quit", "exit"):
            break
        if q:
            show(*chat.ask(q))


if __name__ == "__main__":
    main()
