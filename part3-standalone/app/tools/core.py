"""ToolBox core: per-call authorisation (R7) and recording for the audit trail (R10). Owner: Person 3.

Every tool that reads patient data takes patient_id and checks it against the logged-in session.
Agents pass session.subject_patient_id; a mismatch raises AccessDenied and is recorded.
"""

import time

from app import config, db, guard
from app.auth import authorize_patient


class AccessDenied(PermissionError):
    pass


class CoreTools:
    def __init__(self, session, as_of=None):
        self.session = session
        self.as_of = as_of or config.AS_OF_DATE
        self.calls = []
        self.denied = []
        self.results = []          # raw outputs, used by the verifier as evidence (never logged)

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

    def departments(self):
        return db.query("SELECT * FROM departments")

    def doctor(self, doctor_id):
        return db.query_one("SELECT d.*, p.department_name FROM doctors d LEFT JOIN departments p "
                            "ON d.department_id = p.department_id WHERE doctor_id = ?", (doctor_id,))

    def procedure(self, code):
        return db.query_one("SELECT * FROM procedures WHERE procedure_code = ?", (code,))

    def patient(self, patient_id):
        return self._run("get_patient_profile", patient_id, {}, lambda: db.query_one(
            "SELECT patient_id, first_name, last_name, age, gender, insurance_provider, insurance_plan_id, "
            "insurance_number, policy_start_date FROM patients WHERE patient_id = ?", (patient_id,)))

