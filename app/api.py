"""FastAPI service.  Run:  uvicorn app.api:app --reload   ->  docs at http://localhost:8000/docs

POST /login            -> token (identity for every later call; never taken from the question text)
POST /ask              -> answer with answer_type, citations, tools_invoked and trace_id
POST /confirm          -> confirm a proposed action (e.g. reschedule option 2)
POST /ingest           -> admin upload of a new policy (Markdown with front matter, or PDF + metadata)
GET  /health           -> status of database, knowledge base and LLM
GET  /trace/{trace_id} -> audit record (the same user, an auditor or an admin)
GET  /audit            -> recent audit records (auditor/admin)
GET  /sources          -> source register: every document, version and whether it is in force today
"""

import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from app import audit, auth, config, llm, service
from app.kb import ingest, resolver
from app.kb.store import get_kb
from app.schemas import AskRequest, AskResponse, ConfirmRequest, IngestResponse, LoginRequest, LoginResponse

@asynccontextmanager
async def lifespan(_app):
    service.startup()
    yield


app = FastAPI(title="Hospital Patient Services Assistant", version="1.0.0", lifespan=lifespan,
              description="Grounded, secure and auditable assistant - HCLTech Future Ready AI Engineer Hackathon")


def current_session(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Log in first: send 'Authorization: Bearer <token>'.")
    try:
        return auth.session_from_token(authorization.split(" ", 1)[1])
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    try:
        token, session = auth.login(body.user_id, body.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"token": token, "session": session.to_dict()}


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, session=Depends(current_session)):
    as_of = date.fromisoformat(body.as_of_date) if body.as_of_date else None
    return service.ask(session, body.question, as_of=as_of)


@app.post("/confirm", response_model=AskResponse)
def confirm(body: ConfirmRequest, session=Depends(current_session)):
    return service.ask(session, "cancel" if body.option == 0 else f"confirm option {body.option}")


@app.post("/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...), metadata: str = Form(default="{}"),
                          session=Depends(current_session)):
    if not session.can_upload_documents:
        raise HTTPException(403, "Only authorised administrators can upload knowledge-base documents.")
    raw = await file.read()
    try:
        extra = json.loads(metadata or "{}")
        text = ingest.pdf_to_text(raw) if file.filename.lower().endswith(".pdf") else raw.decode("utf-8")
        return ingest.ingest(text, file.filename, extra_meta=extra, session=session)
    except (ingest.IngestionError, ValueError) as exc:
        raise HTTPException(400, f"Rejected: {exc}") from exc


@app.get("/health")
def health():
    kb = get_kb()
    return {"status": "ok", "as_of_date": config.AS_OF_DATE.isoformat(), "knowledge_base": kb.stats(),
            "clauses_in_force": len({kb.by_id[i]["section_id"] for i in kb.usable_ids()}),
            "llm": {"provider": llm.provider() or "none (offline mode)", "model": llm.model_name(),
                    "router_mode": config.ROUTER_MODE, "rephrase": config.LLM_REPHRASE}}


@app.get("/trace/{trace_id}")
def trace(trace_id: str, session=Depends(current_session)):
    rec = audit.get(trace_id)
    if not rec:
        raise HTTPException(404, "Unknown trace ID.")
    if session.role not in ("auditor", "admin") and rec["user_id"] != session.user_id:
        raise HTTPException(403, "You can only view your own traces.")
    return rec


@app.get("/audit")
def audit_log(limit: int = 50, session=Depends(current_session)):
    if session.role not in ("auditor", "admin"):
        raise HTTPException(403, "Auditor or admin role required.")
    return audit.recent(limit)


@app.get("/sources")
def sources():
    kb = get_kb()
    live, superseded_by = resolver.usable_docs(kb.docs, config.AS_OF_DATE)
    return [{"doc_id": d["doc_id"], "title": d["title"], "version": d["version"], "authority_level": d["authority_level"],
             "effective_date": d["effective_date"], "expiry_date": d["expiry_date"], "status": d["status"],
             "in_force_today": d["doc_key"] in live and d["doc_key"] not in superseded_by,
             "superseded_by": superseded_by[d["doc_key"]]["doc_key"] if d["doc_key"] in superseded_by else "",
             "origin": d["origin"]} for d in sorted(kb.docs, key=lambda d: (d["doc_id"], d["effective_date"]))]
