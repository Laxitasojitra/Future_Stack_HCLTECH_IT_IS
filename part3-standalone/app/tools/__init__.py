"""Deterministic healthcare tools (R5). The LLM never calculates eligibility, results or bills.

ToolBox combines one tools module per team member, so each person edits only their own file:
    core.py               - authorisation and audit recording (Person 3)
    records_billing.py    - billing, refunds, uploaded documents (Person 1)
    insurance_lab.py      - insurance, cost estimates, labs, medications (Person 2)
    care_appointments.py  - appointments, surgery, pre-op, care plans, immunizations (Person 3)
"""

from app.tools.care_appointments import CareAppointmentTools
from app.tools.core import AccessDenied, CoreTools
from app.tools.insurance_lab import InsuranceLabTools, find_lab_tests, find_procedure
from app.tools.records_billing import RecordsBillingTools

__all__ = ["AccessDenied", "ToolBox", "find_lab_tests", "find_procedure"]


class ToolBox(CoreTools, RecordsBillingTools, InsuranceLabTools, CareAppointmentTools):
    """One object per request, bound to the logged-in session."""
