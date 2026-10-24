"""Lab result agent: latest verified result from the LIS, shown as value, unit, range and lab flag.
Critical values get the SOP-LAB-002 5.2 advice. The assistant never interprets results."""

from app.agents.base import not_found, policy_citations, result
from app.fmt import SYSTEMS, nice_date, system_citation
from app.tools import find_lab_tests

LIS = system_citation(SYSTEMS["lab"])
FLAGS = {"H": "high", "L": "low", "HH": "critical high", "LL": "critical low"}


def answer(tools, patient_id, question, proxy=False):
    codes = find_lab_tests(question)
    rows = tools.get_lab_results(patient_id, codes or None) if codes else tools.get_lab_results(patient_id, ["__none__"])
    extra = policy_citations(["POL-PRIV-001#3.1"]) if proxy else []
    if not codes or not rows:
        return not_found(explanation="No verified result for the requested test in the Laboratory Information System.",
                         citations=[LIS] + extra)

    latest = {}
    for r in rows:                      # rows are newest first
        latest.setdefault(r["test_code"], r)
    whose = "The patient's" if proxy else "Your"
    lines, critical = [], []
    for code in codes:
        r = latest.get(code)
        if not r:
            continue
        flag = f", flagged {r['flag']} ({FLAGS[r['flag']]}) by the lab" if r["flag"] in FLAGS else ", within the reference range"
        unit = f" {r['unit']}" if r["unit"] else ""
        rng = f" (reference range {r['ref_range_text']}{unit})" if r["ref_range_text"] else ""
        lines.append(f"{r['test_name']}: {r['value']}{unit}{rng}{flag}. Collected {nice_date(r['collected_date'])}, "
                     f"reported {nice_date(r['reported_date'])}.")
        if r["is_critical"] == "Y":
            critical.append(r)
    if not lines:
        return not_found(citations=[LIS] + extra)

    text = f"{whose} latest result{'s' if len(lines) > 1 else ''}:\n" + "\n".join(f"- {ln}" for ln in lines) \
        if len(lines) > 1 else f"{whose} latest result - {lines[0]}"
    citations = [LIS] + extra + policy_citations(["SOP-LAB-002#4.2"])
    if critical:
        text += ("\n\nThis result is in the critical range. Please contact your care team immediately, or go to the "
                 "Emergency Department if you feel unwell. The laboratory has also informed the treating doctor. "
                 "I can't interpret the result; your doctor will explain what it means for you.")
        citations += policy_citations(["SOP-LAB-002#5.2", "SOP-LAB-002#5.1"])
    else:
        text += "\n\nYour doctor can explain what the result means for you."
    return result(text, "retrieved_fact", citations, "Latest verified result from the Laboratory Information System.",
                  data={"results": [latest[c]["result_id"] for c in codes if c in latest],
                        "critical": [r["result_id"] for r in critical]}, rephrase=not critical)


def reports(tools, patient_id):
    rows = tools.get_diagnostic_reports(patient_id)
    if not rows:
        return not_found(citations=[system_citation(SYSTEMS["radiology"])])
    lines = [f"- {r['modality']} {r['body_part']} ({nice_date(r['study_date'])}, {r['status']}): \"{r['impression']}\""
             for r in rows[:5]]
    return result("Your most recent imaging and ECG reports (the reporting doctor's impression, quoted as written):\n"
                  + "\n".join(lines) + "\n\nYour doctor will explain what these findings mean for you.",
                  "retrieved_fact", [system_citation(SYSTEMS["radiology"])], "Retrieved from the Radiology Information System.")
