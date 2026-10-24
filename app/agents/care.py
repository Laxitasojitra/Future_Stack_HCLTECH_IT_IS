"""PLACEHOLDER - Person 3 owns the care coordination agent (surgery readiness, pending pre-op tests,
care-plan follow-ups). Replace this file with the real agent; keep these three signatures."""

from app.agents.placeholder import placeholder


def readiness(tools, patient_id, question):
    return placeholder("care_agent", "surgery readiness")


def pending_tests(tools, patient_id, question):
    return placeholder("care_agent", "pending pre-operative tests")


def care_plan(tools, patient_id):
    return placeholder("care_agent", "care-plan follow-ups")
