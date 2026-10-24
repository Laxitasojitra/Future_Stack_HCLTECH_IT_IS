"""Deterministic healthcare tools (R5). The LLM never calculates eligibility, results or bills.

Every tool that reads patient data takes patient_id and checks it against the logged-in
session (R7). Agents pass session.subject_patient_id; a mismatch raises AccessDenied and is
recorded in the audit trace. Every call is recorded (name, masked args, status, latency).
"""

import re
import time
from datetime import timedelta

from app import config, db, guard
from app.auth import authorize_patient
from app.fmt import add_months, months_between, num, to_date


class AccessDenied(PermissionError):
    pass


LAB_SYNONYMS = [
    (r"hba1c|a1c|glycated|average\s+(?:blood\s+)?sugar", ["HBA1C"]),
    (r"blood\s+sugar|sugar|glucose|\bfbs\b", ["FBS"]),
    (r"potassium|\bk\+?\b", ["K"]),
    (r"sodium", ["NA"]),
    (r"electrolytes?", ["NA", "K"]),
    (r"creatinine|kidney\s+function", ["CREAT"]),
    (r"\binr\b|pt/inr|prothrombin|clotting", ["INR"]),
    (r"\btsh\b|thyroid", ["TSH"]),
    (r"cholesterol|\bldl\b|lipid", ["LDL"]),
    (r"ha?emoglobin|\bhb\b", ["HB"]),
    (r"white\s+(?:blood\s+)?cells?|\bwbc\b", ["WBC"]),
    (r"platelets?|\bplt\b", ["PLT"]),
    (r"\bcbc\b|blood\s+count", ["HB", "WBC", "PLT"]),
    (r"blood\s+group", ["BLOOD_GROUP"]),
]

PROCEDURE_SYNONYMS = [
    (r"knee\s+(?:replacement|surgery|operation)|\btkr\b", "TKR"),
    (r"hip\s+(?:replacement|surgery|operation)|\bthr\b", "THR"),
    (r"gall\s*bladder|cholecystectomy", "LAPCHOLE"),
    (r"append(?:ix|ectomy|icitis)", "APPEND"),
    (r"hernia", "HERNIA"),
    (r"cataract", "CATARACT"),
    (r"angiogra", "ANGIO"),
    (r"chemo", "CHEMO"),
    (r"\bmri\b", "MRI"),
    (r"\bct\b|ct\s+scan|cat\s+scan", "CT"),
    (r"x-?ray", "XRAY"),
    (r"\becho(?:cardiogram)?\b", "ECHO"),
    (r"stress\s+test|treadmill|\btmt\b", "TMT"),
    (r"\becg\b|\bekg\b", "ECG"),
    (r"physio", "PHYSIO"),
    (r"pre-?anaes?thesia|\bpac\b", "PAC"),
    (r"consult", "CONSULT"),
    (r"lab(?:oratory)?\s+tests?|blood\s+tests?", "LAB"),
]


def find_lab_tests(text):
    lowered = (text or "").lower()
    for pattern, codes in LAB_SYNONYMS:
        if re.search(pattern, lowered):
            return codes
    return []


def find_procedure(text):
    lowered = (text or "").lower()
    for pattern, code in PROCEDURE_SYNONYMS:
        if re.search(pattern, lowered):
            return code
    return None


class ToolBox:
    def __init__(self, session, as_of=None):
        self.session = session
        self.as_of = as_of or config.AS_OF_DATE
        self.calls = []
        self.denied = []
        self.results = []          # raw outputs, used by the verifier as evidence (never logged)

    # ------------------------------------------------------------ plumbing

    def _record(self, name, args, started, status="ok", rows=0):
        masked = {k: (guard.mask_pii(v)[0] if isinstance(v, str) else v) for k, v in args.items()}
        self.calls.append({"tool": name, "args": masked, "status": status, "rows": rows,
                           "ms": int((time.perf_counter() - started) * 1000)})

    def _authorize(self, name, patient_id, started, args):
        if not authorize_patient(self.session, patient_id):
            self._record(name, args, started, status="denied")
            self.denied.append({"tool": name, "requested_patient_id": patient_id,
                                "session_patient_id": self.session.subject_patient_id})
            raise AccessDenied(f"{name}: patient {patient_id} is not the logged-in patient")

    def _run(self, name, patient_id, args, fn):
        started = time.perf_counter()
        if patient_id is not None:
            self._authorize(name, patient_id, started, {"patient_id": patient_id, **args})
        result = fn()
        self.results.append(result)
        rows = len(result) if isinstance(result, list) else int(bool(result))
        self._record(name, {"patient_id": patient_id, **args} if patient_id else args, started, rows=rows)
        return result

    def tool_names(self):
        return list(dict.fromkeys(c["tool"] for c in self.calls))

    # ------------------------------------------------------------ reference data (no patient)

    def find_department(self, query):
        def run():
            table = db.query_one("SELECT text FROM hospital_policy_sections WHERE section_id = 'DIR-001#2'")["text"]
            rows = [r.strip().strip("|").split("|") for r in table.splitlines()[2:] if r.strip().startswith("|")]
            terms = set(re.findall(r"[a-z]+", query.lower())) - {"which", "department", "should", "visit", "for",
                                                                     "the", "my", "i", "what", "problems", "problem"}
            best, best_score = None, 0
            for cells in rows:
                name, reasons = cells[0].strip(), cells[1].strip()
                words = set(re.findall(r"[a-z]+", f"{name} {reasons}".lower()))
                score = sum(1 for t in terms if t in words or (t.rstrip("s") in words))
                if score > best_score:
                    best, best_score = cells, score
            if not best:
                return None
            return {"department": best[0].strip(), "reasons": best[1].strip(), "location": best[2].strip(),
                    "front_desk": best[3].strip()}
        return self._run("find_department", None, {"query": query}, run)

    def drug_interaction_check(self, medication_list):
        def run():
            names = [m.lower() for m in medication_list]
            found = []
            for r in db.query("SELECT * FROM drug_interactions"):
                a, b = r["drug_a"].lower(), r["drug_b"].lower()
                if a in names and b in names:
                    found.append(r)
            return found
        return self._run("drug_interaction_check", None, {"medication_list": medication_list}, run)

    def find_drugs(self, text):
        lowered = (text or "").lower()
        return [r["generic_name"] for r in db.query("SELECT generic_name FROM medications")
                if re.search(rf"\b{re.escape(r['generic_name'].lower())}\b", lowered)]

    def get_available_slots(self, doctor_id=None, department_id=None, after=None, before=None, limit=3):
        def run():
            start = to_date(after) if after else self.as_of + timedelta(days=1)
            sql = "SELECT * FROM doctor_slots WHERE slot_status = 'available' AND slot_date >= ?"
            params = [start.isoformat()]
            if doctor_id:
                sql += " AND doctor_id = ?"
                params.append(doctor_id)
            elif department_id:
                sql += " AND department_id = ?"
                params.append(department_id)
            if before:
                sql += " AND slot_date < ?"
                params.append(to_date(before).isoformat())
            sql += " ORDER BY slot_date, slot_time LIMIT ?"
            params.append(limit)
            rows = db.query(sql, params)
            for r in rows:
                r["doctor"] = self.doctor(r["doctor_id"])
            return rows
        return self._run("get_available_slots", None,
                         {"doctor_id": doctor_id or "", "department_id": department_id or ""}, run)

    def departments(self):
        return db.query("SELECT * FROM departments")

    def doctor(self, doctor_id):
        return db.query_one("SELECT d.*, p.department_name FROM doctors d LEFT JOIN departments p "
                            "ON d.department_id = p.department_id WHERE doctor_id = ?", (doctor_id,))

    def procedure(self, code):
        return db.query_one("SELECT * FROM procedures WHERE procedure_code = ?", (code,))

    # ------------------------------------------------------------ appointments

    def get_appointments(self, patient_id, upcoming_only=True, department=None):
        def run():
            sql = ("SELECT a.*, p.department_name, d.first_name || ' ' || d.last_name AS doctor_name "
                   "FROM appointments a LEFT JOIN departments p ON a.department_id = p.department_id "
                   "LEFT JOIN doctors d ON a.doctor_id = d.doctor_id WHERE a.patient_id = ?")
            params = [patient_id]
            if upcoming_only:
                sql += " AND a.status = 'Scheduled' AND a.appointment_date >= ?"
                params.append(self.as_of.isoformat())
            if department:
                sql += " AND LOWER(p.department_name) LIKE ?"
                params.append(f"%{department.lower()}%")
            sql += " ORDER BY a.appointment_date, a.appointment_time"
            return db.query(sql, params)
        return self._run("get_appointments", patient_id, {"upcoming_only": upcoming_only,
                                                          "department": department or ""}, run)

    def reschedule_appointment(self, patient_id, appointment_id, slot_id):
        def run():
            appt = db.query_one("SELECT * FROM appointments WHERE appointment_id = ? AND patient_id = ?",
                                (appointment_id, patient_id))
            slot = db.query_one("SELECT * FROM doctor_slots WHERE slot_id = ? AND slot_status = 'available'", (slot_id,))
            if not appt or not slot:
                return None
            db.execute("UPDATE doctor_slots SET slot_status = 'available', appointment_id = '' WHERE appointment_id = ?",
                       (appointment_id,))
            db.execute("UPDATE doctor_slots SET slot_status = 'booked', appointment_id = ? WHERE slot_id = ?",
                       (appointment_id, slot_id))
            db.execute("UPDATE appointments SET appointment_date = ?, appointment_time = ?, doctor_id = ? "
                       "WHERE appointment_id = ?", (slot["slot_date"], slot["slot_time"], slot["doctor_id"], appointment_id))
            return {**appt, "old_date": appt["appointment_date"], "old_time": appt["appointment_time"],
                    "appointment_date": slot["slot_date"], "appointment_time": slot["slot_time"],
                    "doctor": self.doctor(slot["doctor_id"])}
        return self._run("reschedule_appointment", patient_id, {"appointment_id": appointment_id, "slot_id": slot_id}, run)

    def book_appointment(self, patient_id, slot_id, reason, appointment_type="consultation"):
        def run():
            slot = db.query_one("SELECT * FROM doctor_slots WHERE slot_id = ? AND slot_status = 'available'", (slot_id,))
            if not slot:
                return None
            last = db.query_one("SELECT MAX(CAST(SUBSTR(appointment_id, 2) AS INTEGER)) AS n FROM appointments")["n"] or 0
            new_id = f"A{int(last) + 1:03d}"
            doc = self.doctor(slot["doctor_id"])
            dept = db.query_one("SELECT * FROM departments WHERE department_id = ?", (slot["department_id"],))
            db.execute("INSERT INTO appointments (appointment_id, patient_id, doctor_id, department_id, appointment_date, "
                       "appointment_time, reason_for_visit, appointment_type, status, hospital_branch, location) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)",
                       (new_id, patient_id, slot["doctor_id"], slot["department_id"], slot["slot_date"], slot["slot_time"],
                        reason, appointment_type, doc["hospital_branch"], dept["location"] if dept else ""))
            db.execute("UPDATE doctor_slots SET slot_status = 'booked', appointment_id = ? WHERE slot_id = ?", (new_id, slot_id))
            return {"appointment_id": new_id, "appointment_date": slot["slot_date"], "appointment_time": slot["slot_time"],
                    "doctor": doc, "department_name": dept["department_name"] if dept else "",
                    "location": dept["location"] if dept else "", "reason_for_visit": reason}
        return self._run("book_appointment", patient_id, {"slot_id": slot_id, "reason": reason}, run)

    # ------------------------------------------------------------ labs and reports

    def get_lab_results(self, patient_id, test_codes=None):
        def run():
            rows = db.query("SELECT r.*, t.critical_low, t.critical_high, t.turnaround_hours FROM lab_results r "
                            "LEFT JOIN lab_tests t ON r.test_code = t.test_code WHERE r.patient_id = ? "
                            "AND r.reported_date <= ? ORDER BY r.reported_date DESC, r.result_id DESC",
                            (patient_id, self.as_of.isoformat()))
            if test_codes:
                rows = [r for r in rows if r["test_code"] in test_codes]
            return rows
        return self._run("get_lab_results", patient_id, {"tests": ",".join(test_codes or [])}, run)

    def get_diagnostic_reports(self, patient_id):
        return self._run("get_diagnostic_reports", patient_id, {}, lambda: db.query(
            "SELECT * FROM diagnostic_reports WHERE patient_id = ? AND report_date <= ? ORDER BY report_date DESC",
            (patient_id, self.as_of.isoformat())))

    # ------------------------------------------------------------ medications

    def get_medications(self, patient_id):
        return self._run("get_medications", patient_id, {}, lambda: db.query(
            "SELECT * FROM prescriptions WHERE patient_id = ? AND status = 'active'", (patient_id,)))

    # ------------------------------------------------------------ insurance, surgery, pre-op

    def patient(self, patient_id):
        return self._run("get_patient_profile", patient_id, {}, lambda: db.query_one(
            "SELECT patient_id, first_name, last_name, age, gender, insurance_provider, insurance_plan_id, "
            "insurance_number, policy_start_date FROM patients WHERE patient_id = ?", (patient_id,)))

    def check_insurance_eligibility(self, patient_id, procedure_code):
        def run():
            p = db.query_one("SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
            proc = self.procedure(procedure_code)
            out = {"procedure_code": procedure_code, "procedure_name": proc["procedure_name"] if proc else procedure_code,
                   "plan_id": p["insurance_plan_id"], "status": None}
            if not p["insurance_plan_id"]:
                out["status"] = "self_pay"
                return out
            plan = db.query_one("SELECT * FROM insurance_plans WHERE plan_id = ?", (p["insurance_plan_id"],))
            rule = db.query_one("SELECT * FROM insurance_coverage_rules WHERE plan_id = ? AND procedure_code = ?",
                                (p["insurance_plan_id"], procedure_code))
            out.update({"plan": plan, "rule": rule, "policy_start_date": p["policy_start_date"]})
            if not rule:
                out["status"] = "not_listed"
                return out
            sections = [s.strip() for s in rule["source_section"].split(";") if s.strip()]
            out["source_sections"] = [f"{rule['source_doc_id']}#{s}" for s in sections]
            if rule["covered"] != "Y":
                out["status"] = "not_covered"
                return out
            waiting = int(num(rule["waiting_period_months"], 0) or 0)
            months = months_between(p["policy_start_date"], self.as_of) if p["policy_start_date"] else 0
            out.update({"waiting_period_months": waiting, "months_covered": months,
                        "copay_pct": num(rule["copay_pct"], 0), "sublimit_inr": num(rule["sublimit_inr"]),
                        "package_rate_inr": num(rule["package_rate_inr"]),
                        "annual_opd_limit_inr": num(rule["annual_opd_limit_inr"]),
                        "preauth_required": rule["preauth_required"] == "Y"})
            if waiting and months < waiting:
                out["status"] = "waiting_period"
                out["eligible_from"] = add_months(p["policy_start_date"], waiting).isoformat()
            else:
                out["status"] = "covered"
            return out
        return self._run("check_insurance_eligibility", patient_id, {"procedure": procedure_code}, run)

    def get_surgery_details(self, patient_id, procedure_code=None, include_completed=False):
        def run():
            sql = "SELECT * FROM surgeries WHERE patient_id = ?"
            params = [patient_id]
            if not include_completed:
                sql += " AND status = 'scheduled' AND scheduled_date >= ?"
                params.append(self.as_of.isoformat())
            if procedure_code:
                sql += " AND procedure_code = ?"
                params.append(procedure_code)
            rows = db.query(sql + " ORDER BY scheduled_date", params)
            for r in rows:
                r["procedure"] = self.procedure(r["procedure_code"])
                r["surgeon"] = self.doctor(r["surgeon_id"])
                r["anesthetist"] = self.doctor(r["anesthetist_id"])
            return rows
        return self._run("get_surgery_details", patient_id, {"procedure": procedure_code or ""}, run)

    def get_preauth_status(self, patient_id, surgery_id=None, procedure_code=None):
        def run():
            sql, params = "SELECT * FROM preauthorizations WHERE patient_id = ?", [patient_id]
            if surgery_id:
                sql += " AND surgery_id = ?"
                params.append(surgery_id)
            elif procedure_code:
                sql += " AND procedure_code = ?"
                params.append(procedure_code)
            rows = db.query(sql, params)
            for r in rows:
                r["plan"] = db.query_one("SELECT * FROM insurance_plans WHERE plan_id = ?", (r["plan_id"],))
            return rows
        return self._run("get_preauth_status", patient_id, {"surgery_id": surgery_id or "",
                                                            "procedure": procedure_code or ""}, run)

    def get_preop_orders(self, patient_id, surgery):
        """Evaluate every PROT-PREOP-004 requirement for the surgery's category against orders,
        results, clearances and pre-authorisation (complete / pending / expired / auto)."""
        def run():
            sdate = to_date(surgery["scheduled_date"])
            patient = db.query_one("SELECT age, insurance_plan_id FROM patients WHERE patient_id = ?", (patient_id,))
            reqs = db.query("SELECT * FROM preop_requirements WHERE surgery_category = ?", (surgery["surgery_category"],))
            orders = db.query("SELECT * FROM orders WHERE patient_id = ? ORDER BY ordered_date DESC", (patient_id,))
            preauths = db.query("SELECT * FROM preauthorizations WHERE patient_id = ? AND surgery_id = ?",
                                (patient_id, surgery["surgery_id"]))
            items = []
            for req in reqs:
                cond = req["condition"]
                if cond.startswith("age >="):
                    if int(num(patient["age"], 0)) < int(cond.split(">=")[1]):
                        continue
                item = {"item_code": req["item_code"], "item_name": req["item_name"], "item_type": req["item_type"],
                        "source": [f"{req['source_doc_id']}#{s.strip()}" for s in req["source_section"].split(";")]}
                if req["item_type"] == "financial":
                    if not patient["insurance_plan_id"]:
                        item.update(item_name="Admission deposit (self-pay)", status="pending",
                                    detail="to be paid at the Admission Desk as per the Admission Policy")
                    else:
                        pa = preauths[0] if preauths else None
                        st = pa["status"] if pa else "not_submitted"
                        detail = {"approved": f"approved on {pa['decision_date'] if pa else ''}",
                                  "submitted": f"submitted on {pa['submitted_date'] if pa else ''}, awaiting the insurer's decision",
                                  "rejected": "rejected by the insurer; please contact the TPA desk",
                                  "not_submitted": "not yet submitted by the TPA desk"}.get(st, st)
                        item.update(item_name="Insurance pre-authorisation", status="complete" if st == "approved" else "pending",
                                    preauth=pa, detail=detail)
                    items.append(item)
                    continue
                if req["auto_scheduled"] == "Y":
                    item.update(status="auto", detail="scheduled automatically by the blood bank")
                    items.append(item)
                    continue
                validity = int(num(req["validity_days"], 0) or 0)
                mine = [o for o in orders if o["item_code"] == req["item_code"]]
                done = [o for o in mine if o["status"] == "completed" and o["completed_date"]
                        and o["completed_date"] <= self.as_of.isoformat()]
                valid = [o for o in done if not validity or (sdate - to_date(o["completed_date"])).days <= validity]
                if valid:
                    item.update(status="complete", completed_date=valid[0]["completed_date"])
                else:
                    open_orders = [o for o in mine if o["status"] in ("pending", "scheduled")]
                    if open_orders:
                        o = open_orders[0]
                        item.update(status="pending", detail=o["status"], scheduled_date=o["scheduled_date"],
                                    linked_appointment_id=o["linked_appointment_id"])
                    elif done:
                        item.update(status="expired", completed_date=done[0]["completed_date"],
                                    detail=f"older than {validity} days before surgery")
                    else:
                        item.update(status="pending", detail="not yet ordered")
                items.append(item)

            reviews = []
            for t in db.query("SELECT * FROM clinical_review_thresholds"):
                if t["item_code"] == "ECG":
                    ecg = db.query_one("SELECT * FROM diagnostic_reports WHERE patient_id = ? AND modality = 'ECG' "
                                       "ORDER BY report_date DESC", (patient_id,))
                    if ecg and ecg["result_flag"] == "abnormal":
                        reviews.append({"item_code": "ECG", "value": "abnormal", "unit": "", "reviewer": t["reviewer"],
                                        "threshold": "abnormal", "source": f"{t['source_doc_id']}#{t['source_section']}"})
                    continue
                latest = db.query_one("SELECT * FROM lab_results WHERE patient_id = ? AND test_code = ? AND reported_date <= ? "
                                      "ORDER BY reported_date DESC, result_id DESC",
                                      (patient_id, t["item_code"], self.as_of.isoformat()))
                if latest and num(latest["value"]) is not None and num(latest["value"]) > num(t["threshold_value"]):
                    reviews.append({"item_code": t["item_code"], "test_name": latest["test_name"], "value": latest["value"],
                                    "unit": latest["unit"], "threshold": t["threshold_value"], "reviewer": t["reviewer"],
                                    "source": f"{t['source_doc_id']}#{t['source_section']}"})
            return {"items": items, "reviews": reviews}
        return self._run("get_preop_orders", patient_id, {"surgery_id": surgery["surgery_id"]}, run)

    # ------------------------------------------------------------ care plans

    def check_care_plan_compliance(self, patient_id):
        def run():
            plans = db.query("SELECT * FROM care_plans WHERE patient_id = ? AND status = 'active'", (patient_id,))
            out = []
            for plan in plans:
                items = db.query("SELECT * FROM care_plan_items WHERE care_plan_id = ? ORDER BY due_date", (plan["care_plan_id"],))
                for it in items:
                    overdue = it["status"] in ("pending", "scheduled") and it["due_date"] < self.as_of.isoformat()
                    it["compliance"] = "missed" if it["status"] == "missed" else "overdue" if overdue else it["status"]
                    if it["linked_appointment_id"]:
                        it["appointment"] = db.query_one("SELECT * FROM appointments WHERE appointment_id = ?",
                                                         (it["linked_appointment_id"],))
                out.append({"plan": plan, "items": items})
            return out
        return self._run("check_care_plan_compliance", patient_id, {}, run)

    # ------------------------------------------------------------ billing

    def get_billing_summary(self, patient_id):
        return self._run("get_billing_summary", patient_id, {}, lambda: db.query(
            "SELECT * FROM billing WHERE patient_id = ? AND bill_type != 'voided' ORDER BY bill_date DESC", (patient_id,)))

    def get_refund_status(self, patient_id):
        return self._run("get_refund_status", patient_id, {}, lambda: db.query(
            "SELECT * FROM refunds WHERE patient_id = ? ORDER BY initiated_date DESC", (patient_id,)))

    def estimate_procedure_cost(self, patient_id, procedure_code, eligibility):
        def run():
            proc = self.procedure(procedure_code)
            if not proc:
                return None
            price = num(proc["list_price_inr"], 0)
            est = {"procedure_name": proc["procedure_name"], "list_price": price, "insurer_pays": 0.0,
                   "patient_pays": price, "copay_amount": 0.0, "above_sublimit": 0.0, "basis": "self_pay"}
            if eligibility and eligibility.get("status") == "covered":
                pkg, sub = eligibility.get("package_rate_inr"), eligibility.get("sublimit_inr")
                pct = (eligibility.get("copay_pct") or 0) / 100
                if pkg:
                    est.update(basis="package", package_rate=pkg, insurer_pays=pkg, patient_pays=0.0, list_price=pkg)
                else:
                    admissible = min(price, sub) if sub else price
                    copay = round(admissible * pct)
                    above = price - admissible
                    est.update(basis="insured", admissible=admissible, copay_amount=copay, above_sublimit=above,
                               insurer_pays=admissible - copay, patient_pays=copay + above, sublimit=sub,
                               copay_pct=eligibility.get("copay_pct"))
            elif eligibility and eligibility.get("status") in ("not_covered", "waiting_period", "not_listed"):
                est["basis"] = "not_covered"
            return est
        return self._run("estimate_procedure_cost", patient_id, {"procedure": procedure_code}, run)

    # ------------------------------------------------------------ uploaded documents (untrusted)

    def read_uploaded_document(self, patient_id, upload_id=None):
        def run():
            sql, params = "SELECT * FROM uploaded_documents WHERE patient_id = ?", [patient_id]
            if upload_id:
                sql += " AND upload_id = ?"
                params.append(upload_id)
            rows = db.query(sql + " ORDER BY uploaded_date DESC", params)
            for r in rows:
                path = config.UPLOADS_DIR / r["file_name"]
                r["raw_text"] = path.read_text(encoding="utf-8") if path.exists() else ""
            return rows
        return self._run("read_uploaded_document", patient_id, {"upload_id": upload_id or ""}, run)
