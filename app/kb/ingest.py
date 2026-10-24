"""Live ingestion of new policies, protocols and advisories (R11) - no restart needed.

Accepted: Markdown/text with a YAML front-matter block, or PDF (text extracted with PyMuPDF)
with the metadata supplied separately. A document is rejected when required metadata is
missing or invalid, when it has no numbered clauses, or when it contains instructions aimed at
the assistant (R8). Rejected documents never become searchable.
"""

import json
import re
from datetime import date

import yaml

from app import config, guard
from app.kb.resolver import AUTHORITY_RANK
from app.kb.store import get_kb

REQUIRED = ["doc_id", "title", "policy_key", "doc_type", "authority_level", "version", "effective_date", "status"]
ALLOWED_STATUS = {"approved", "draft"}


class IngestionError(ValueError):
    pass


def split_front_matter(raw):
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
    if not m:
        return {}, raw
    meta = yaml.safe_load(m.group(1)) or {}
    return {k: ("" if v is None else str(v)) for k, v in meta.items()}, m.group(2)


def validate_metadata(meta):
    missing = [k for k in REQUIRED if not str(meta.get(k, "")).strip()]
    if missing:
        raise IngestionError(f"Missing required metadata: {', '.join(missing)}.")
    for key in ("effective_date", "expiry_date"):
        if meta.get(key):
            try:
                date.fromisoformat(str(meta[key]))
            except ValueError as exc:
                raise IngestionError(f"{key} must be YYYY-MM-DD.") from exc
    if meta["authority_level"] not in AUTHORITY_RANK:
        raise IngestionError(f"authority_level must be one of {', '.join(AUTHORITY_RANK)}.")
    if meta["status"] not in ALLOWED_STATUS:
        raise IngestionError(f"status must be one of {', '.join(sorted(ALLOWED_STATUS))}.")
    meta.setdefault("authority_rank", str(AUTHORITY_RANK[meta["authority_level"]]))
    if not meta.get("authority_rank"):
        meta["authority_rank"] = str(AUTHORITY_RANK[meta["authority_level"]])
    return meta


def parse_clauses(body):
    """'## 3. Title' starts a section; '3.1 text' starts a clause. Unnumbered paragraphs under a
    section become clause '<section>'."""
    clauses, section_no, section_title, loose = [], "", "", []

    def flush_loose():
        if loose and section_no:
            clauses.append({"clause": section_no, "section_number": section_no, "section_title": section_title,
                            "text": " ".join(loose).strip()})
        loose.clear()

    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("# "):
            continue
        h = re.match(r"^#{2,3}\s*(\d+)\.?\s+(.*)$", s)
        if h:
            flush_loose()
            section_no, section_title = h.group(1), h.group(2).strip()
            continue
        c = re.match(r"^(\d+\.\d+)\s+(.*)$", s)
        if c:
            flush_loose()
            num = c.group(1)
            clauses.append({"clause": num, "section_number": num.split(".")[0],
                            "section_title": section_title, "text": s})
        elif clauses and clauses[-1]["section_number"] == section_no and not loose and not s.startswith("|"):
            clauses[-1]["text"] += " " + s
        else:
            loose.append(s)
    flush_loose()
    return clauses


def pdf_to_text(data):
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        return "\n".join(page.get_text() for page in pdf)


def ingest(raw_text, file_name="upload.md", extra_meta=None, session=None):
    """Validate and add a document. Returns a summary dict; raises IngestionError when rejected."""
    if session is not None and not session.can_upload_documents:
        raise IngestionError("Only authorised administrators can upload knowledge-base documents.")
    meta, body = split_front_matter(raw_text)
    meta.update({k: str(v) for k, v in (extra_meta or {}).items() if v not in (None, "")})
    meta = validate_metadata(meta)
    injected = guard.detect_injection(body)
    if injected:
        raise IngestionError(f"Document contains instructions aimed at the assistant: {injected[0]!r}.")
    clauses = parse_clauses(body)
    if not clauses:
        raise IngestionError("No numbered clauses found (expected lines like '2.1 ...').")

    doc, chunks = get_kb().add_document(meta, clauses)
    _save(meta, clauses, raw_text, file_name)
    return {"status": "ok", "doc_id": doc["doc_id"], "version": doc["version"],
            "effective_date": doc["effective_date"], "clauses_added": len(chunks),
            "supersedes": doc["supersedes"], "searchable_now": True}


def _save(meta, clauses, raw_text, file_name):
    config.INGESTED_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{meta['doc_id']}_v{meta['version']}_{file_name}")
    (config.INGESTED_DIR / safe).write_text(raw_text, encoding="utf-8")
    registry = config.INGESTED_DIR / "registry.json"
    entries = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else []
    entries.append({"meta": meta, "clauses": clauses, "file": safe})
    registry.write_text(json.dumps(entries, indent=2), encoding="utf-8")
