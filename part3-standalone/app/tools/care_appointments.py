"""Appointment, surgery, pre-op, care-plan and immunization tools. Owner: Person 3."""

import re
from datetime import timedelta

from app import db
from app.fmt import num, to_date


class CareAppointmentTools:
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

    # ------------------------------------------------------------ immunizations (child health)

    def _has_table(self, name):
        return bool(db.query_one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)))

    def get_immunization_record(self, patient_id):
        """Doses already given, from the EMR immunization record (empty list if the table is not loaded)."""
        def run():
            if not self._has_table("immunizations"):
                return []
            return db.query("SELECT * FROM immunizations WHERE patient_id = ? AND date_given <= ? ORDER BY date_given",
                            (patient_id, self.as_of.isoformat()))
        return self._run("get_immunization_record", patient_id, {}, run)

    def check_vaccines_due(self, patient_id, grace_days=28):
        """Compare the child's record with the official schedule (immunization_schedule table) by age in days.
        Status per dose: given, due (within grace_days of the recommended age), overdue, upcoming, or
        past_catch_up (older than the schedule's catch-up limit). Calculated here, never by the LLM."""
        def run():
            if not (self._has_table("immunization_schedule") and self._has_table("immunizations")):
                return {"available": False}
            child = db.query_one("SELECT patient_id, first_name, date_of_birth FROM patients WHERE patient_id = ?",
                                 (patient_id,))
            dob = to_date(child["date_of_birth"]) if child and child["date_of_birth"] else None
            if not dob:
                return {"available": False, "reason": "date of birth missing"}
            age_days = (self.as_of - dob).days
            given = {(r["vaccine"].lower(), r["dose"]): r for r in db.query(
                "SELECT * FROM immunizations WHERE patient_id = ? AND date_given <= ?", (patient_id, self.as_of.isoformat()))}
            items = []
            for s in db.query("SELECT * FROM immunization_schedule"):
                rec = int(num(s["recommended_age_days"], 0) or 0)
                limit = int(num(s.get("catch_up_until_days"), 0) or 0)
                item = {**s, "recommended_age_days": rec, "due_date": (dob + timedelta(days=rec)).isoformat()}
                done = given.get((s["vaccine"].lower(), s["dose"]))
                if done:
                    item.update(status="given", date_given=done["date_given"])
                elif limit and age_days > limit:
                    item["status"] = "past_catch_up"
                elif age_days > rec + grace_days:
                    item["status"] = "overdue"
                elif age_days >= rec:
                    item["status"] = "due"
                else:
                    item["status"] = "upcoming"
                items.append(item)
            items.sort(key=lambda i: (i["recommended_age_days"], i["vaccine"], i["dose"]))
            return {"available": True, "child": child, "age_days": age_days, "items": items}
        return self._run("check_vaccines_due", patient_id, {}, run)
