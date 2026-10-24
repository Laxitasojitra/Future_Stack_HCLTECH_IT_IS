"""PLACEHOLDER - Person 1 owns the billing agent (balance due, refund status).
Replace this file with the real agent; keep the balance() and refund() signatures."""

from app.agents.placeholder import placeholder


def balance(tools, patient_id):
    return placeholder("billing_agent", "bills and balances")


def refund(tools, patient_id):
    return placeholder("billing_agent", "refunds")
