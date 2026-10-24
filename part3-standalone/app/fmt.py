"""Formatting helpers (Indian rupee grouping, dates, times) and citation builders."""

import re
from datetime import date, datetime


def inr(amount):
    """1234567.5 -> '₹12,34,568' (Indian digit grouping)."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return f"{sign}₹{s}"


def to_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def nice_date(value, weekday=False):
    d = to_date(value)
    if not d:
        return ""
    text = f"{d.day} {d.strftime('%b %Y')}"          # no %-d: it does not work on Windows
    return f"{d.strftime('%a')}, {text}" if weekday else text


def nice_time(value):
    if not value:
        return ""
    t = datetime.strptime(value, "%H:%M")
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def months_between(start, end):
    start, end = to_date(start), to_date(end)
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def add_months(d, months):
    d = to_date(d)
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_clause(text):
    """Drop the leading clause number ('4.2 ICU visiting ...' -> 'ICU visiting ...')."""
    return re.sub(r"^\s*\d+(?:\.\d+)*\s+", "", text or "").strip()


# ---------------------------------------------------------------- citations

def doc_citation(chunk, role="supporting"):
    return {
        "id": chunk["section_id"], "type": "document", "doc_id": chunk["doc_id"], "clause": chunk["clause"],
        "title": chunk["title"], "section_title": chunk.get("section_title", ""), "version": chunk["version"],
        "effective_date": chunk["effective_date"], "expiry_date": chunk.get("expiry_date", ""),
        "authority_level": chunk["authority_level"], "role": role,
    }


def system_citation(name, detail=""):
    return {"id": name, "type": "system", "detail": detail, "role": "supporting"}


def upload_citation(upload_id, file_name):
    return {"id": f"Uploaded document {upload_id}", "type": "upload", "detail": file_name,
            "trust": "untrusted", "role": "supporting"}


def cite_label(chunk):
    return (f"[{chunk['doc_id']} §{chunk['clause']}, v{chunk['version']}, "
            f"effective {nice_date(chunk['effective_date'])}]")


SYSTEMS = {
    "appointments": "Appointment system",
    "lab": "Laboratory Information System",
    "billing": "Billing system",
    "emr": "Electronic Medical Record system",
    "care_plan": "EMR care plan",
    "pharmacy_ref": "Pharmacy drug interaction reference",
    "pharmacy": "Pharmacy system",
    "insurance": "Insurance and TPA system",
    "radiology": "Radiology Information System",
    "directory": "Department directory",
}
