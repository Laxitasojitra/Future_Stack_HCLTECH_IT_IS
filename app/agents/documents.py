"""PLACEHOLDER - Person 1 owns the uploaded-document agent (summaries of untrusted uploads, R8).
Replace this file with the real agent; keep the summarise() signature."""

from app.agents.placeholder import placeholder


def summarise(tools, patient_id, question, stats=None):
    return placeholder("document_agent", "uploaded documents")
