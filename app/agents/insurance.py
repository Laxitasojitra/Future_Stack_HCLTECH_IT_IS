"""Insurance agent: eligibility (deterministic tool), pre-authorisation status and cost estimates.
Untrusted emails or letters can never change coverage (R8)."""

import re

from app.agents.base import not_found, policy_citations, result
from app.fmt import SYSTEMS, inr, nice_date, system_citation
from app.tools import find_procedure

UNTRUSTED_CLAIM = re.compile(r"\b(?:e-?mail|message|sms|whatsapp|letter|forward(?:ed)?|text)\b.{0,40}\b(?:says?|said|claims?|told)\b",
                             re.IGNORECASE)


def _eligibility_sentence(e, plan_name, lead_yes=True):
    proc = e["procedure_name"]
    if e["status"] == "self_pay":
        return f"You are registered as a self-pay patient, so there is no insurance cover on record for {proc.lower()}."
    if e["status"] == "not_listed":
        return f"{proc} is not listed in your plan's agreement with the hospital. Please check with the TPA desk."
    if e["status"] == "not_covered":
        return f"{proc} is not covered under your {plan_name} plan."
    if e["status"] == "waiting_period":
        return (f"{proc} is not covered yet under your {plan_name} plan. It needs {e['waiting_period_months']} months "
                f"of continuous coverage, and you have {e['months_covered']} months (policy started "
                f"{nice_date(e['policy_start_date'])}). You become eligible from {nice_date(e['eligible_from'])}.")
    parts = [f"{'Yes. ' if lead_yes else ''}{proc} is covered under your {plan_name} plan."]
    if e.get("package_rate_inr"):
        parts.append(f"It is a cashless package of {inr(e['package_rate_inr'])} with no co-payment, so beneficiaries pay "
                     f"nothing for services included in the package.")
    elif e.get("annual_opd_limit_inr"):
        parts.append(f"Outpatient diagnostics and therapy are covered up to {inr(e['annual_opd_limit_inr'])} per policy "
                     f"year in total, with no co-payment.")
    else:
        pct = e.get("copay_pct") or 0
        sub = e.get("sublimit_inr")
        parts.append(f"You pay a {pct:g}% co-payment on the admissible amount" +
                     (f", which is the billed amount or the {inr(sub)} sub-limit, whichever is lower." if sub else "."))
    if e.get("preauth_required"):
        lead = (e.get("plan") or {}).get("preauth_lead_days")
        decide = (e.get("plan") or {}).get("preauth_decision_days")
        parts.append("Pre-authorisation is required" +
                     (f": the TPA desk submits it at least {lead} days before surgery and the insurer decides within "
                      f"{decide} working days." if lead and lead != "0" and decide else " before admission."))
    else:
        parts.append("No pre-authorisation is needed.")
    if e["waiting_period_months"]:
        parts.append(f"Your {e['waiting_period_months']}-month waiting period has been met.")
    return " ".join(parts)


def _preauth_sentence(pa):
    plan = pa.get("plan") or {}
    if pa["status"] == "approved":
        amt = f" for {inr(pa['approved_amount_inr'])}" if pa.get("approved_amount_inr") else ""
        return f"Pre-authorisation status: approved on {nice_date(pa['decision_date'])}{amt}. {pa['remarks']}"
    if pa["status"] == "submitted":
        return (f"Pre-authorisation status: submitted on {nice_date(pa['submitted_date'])} and awaiting the insurer's "
                f"decision (usually within {plan.get('preauth_decision_days', '?')} working days).")
    if pa["status"] == "rejected":
        return f"Pre-authorisation status: rejected. {pa['remarks']} Please contact the TPA desk."
    return f"Pre-authorisation status: not yet submitted. {pa['remarks']}"


def _citations_for(e):
    ids = list(e.get("source_sections", []))
    if e.get("package_rate_inr"):
        ids.append(ids[0].split("#")[0] + "#1.2")       # "Beneficiaries pay nothing" clause of the scheme
    return policy_citations(ids)


def answer(tools, patient_id, question, cost=False):
    code = find_procedure(question)
    if not code:
        surgeries = tools.get_surgery_details(patient_id)
        code = surgeries[0]["procedure_code"] if surgeries else None
    if not code:
        return result("Which procedure or test would you like me to check? For example: knee replacement, cataract "
                      "surgery or an outpatient MRI.", "clarification_needed", [], "Procedure not specified.",
                      rephrase=False)
    e = tools.check_insurance_eligibility(patient_id, code)
    plan_name = (e.get("plan") or {}).get("plan_name", "")
    untrusted = bool(UNTRUSTED_CLAIM.search(question))
    lines = [_eligibility_sentence(e, plan_name, lead_yes=not (cost or untrusted))]
    citations = [system_citation(SYSTEMS["insurance"])] + _citations_for(e)
    data = {"eligibility": {k: v for k, v in e.items() if k not in ("plan", "rule")}}

    if untrusted:
        lines.insert(0, "The email or message you mention is not an authorised source, so it cannot change your "
                        "coverage. Your plan's current agreement with the hospital is what applies:")

    if cost:
        est = tools.estimate_procedure_cost(patient_id, code, e)
        if est:
            data["estimate"] = est
            if est["basis"] == "package":
                lines.insert(0, f"No payment is due from you for the {est['procedure_name'].lower()} package: the "
                                f"{inr(est['package_rate'])} package is paid by the scheme.")
            elif est["basis"] == "insured":
                lines.insert(0, 
                    f"Estimate for {est['procedure_name'].lower()}: list price {inr(est['list_price'])}. Admissible amount "
                    f"{inr(est['admissible'])}; your {est['copay_pct']:g}% co-payment is {inr(est['copay_amount'])}"
                    + (f", plus {inr(est['above_sublimit'])} above the sub-limit" if est["above_sublimit"] else "")
                    + f". You would pay about {inr(est['patient_pays'])} and the insurer about {inr(est['insurer_pays'])}.")
            else:
                lines.insert(0, f"Estimate: the list price for {est['procedure_name'].lower()} is "
                                f"{inr(est['list_price'])}, payable by you as it is not covered by insurance.")
            lines.append("This is an estimate, not a final bill: the final amount may differ by up to 15% depending on "
                         "the length of stay, room category and clinical needs.")
            citations += policy_citations(["POL-BIL-003#1.2"])

    surgeries = tools.get_surgery_details(patient_id, procedure_code=code)
    if surgeries:
        pas = tools.get_preauth_status(patient_id, surgery_id=surgeries[0]["surgery_id"])
        if pas:
            lines.append(_preauth_sentence(pas[0]))
            data["preauth"] = pas[0]["status"]

    if e["status"] in ("covered", "waiting_period", "not_covered") and e.get("plan"):
        if e["plan"].get("plan_type") == "government_scheme":
            citations += policy_citations([f"{e['plan']['agreement_doc_id']}#2.1"])
            lines.append("Scheme eligibility is confirmed through the SHAS beneficiary verification system.")
    if e["status"] == "not_listed":
        return not_found("Procedure not listed in the plan's coverage rules.", citations)
    return result("\n\n".join(lines), "calculated", citations,
                  "Eligibility calculated by the insurance tool from the plan's coverage rules; no LLM calculation.",
                  data=data)
