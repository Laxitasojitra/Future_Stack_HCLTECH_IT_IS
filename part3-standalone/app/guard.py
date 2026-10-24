"""Minimal PII masking (stand-in for Person 2's guard.py), used when tool calls are recorded."""

import re


def mask_pii(text):
    out = re.sub(r"(?<!\d)[6-9]\d{9}(?!\d)", "[PHONE]", text or "")
    out = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", out)
    return out, []
