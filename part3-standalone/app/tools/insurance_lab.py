"""Insurance, cost, lab, report and medication tools. Owner: Person 2."""

import re

from app import db
from app.fmt import add_months, months_between, num


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


class InsuranceLabTools:
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

    def get_medications(self, patient_id):
        return self._run("get_medications", patient_id, {}, lambda: db.query(
            "SELECT * FROM prescriptions WHERE patient_id = ? AND status = 'active'", (patient_id,)))

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
