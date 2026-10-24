"""PLACEHOLDER - Person 3 owns the appointment agent (view, reschedule, book, confirm before action).
Replace this file with the real agent; keep these five signatures."""

from app.agents.placeholder import placeholder


def view(tools, patient_id):
    return placeholder("appointment_agent", "appointments")


def reschedule(tools, patient_id, question, department=None):
    return placeholder("appointment_agent", "rescheduling appointments")


def book_missed_care_item(tools, patient_id, question):
    return placeholder("appointment_agent", "booking appointments")


def execute(tools, pending, option):
    return placeholder("appointment_agent", "confirming appointment changes")


def parse_confirmation(message):
    """No pending actions exist until the real agent lands, so nothing is ever a confirmation."""
    return None
