# Future Stack — HCLTech Hackathon

A collaborative project by **Team Future Stack** for the HCLTech Future Ready AI Engineer Hackathon:
the **AI-Powered Hospital Patient Services Assistant**.

## Team split

Each person owns two agents. The shared core (workflow, router, guard, tools, knowledge base, verifier,
audit, API, UI, data) is the same on every branch.

| Person | Agent 1 | Agent 2 | Files |
|---|---|---|---|
| Person 1 | Policy agent: policy and procedure answers, citations, `not_found`, conflicts, uploaded-document summaries | Billing agent: balance due, refund status | `app/agents/policy.py`, `documents.py`, `billing.py` |
| Person 2 | Insurance agent: eligibility, waiting periods, co-pay, pre-auth status, cost estimates, untrusted email claims | Lab agent: latest results, critical values, imaging reports, medication interaction checks | `app/agents/insurance.py`, `lab.py`, `medication.py` |
| Person 3 | Care coordination agent: surgery readiness, pending pre-op tests, care-plan follow-ups | Appointment agent: view, reschedule, book, confirm before action | `app/agents/care.py`, `appointment.py`, `directory.py` |

## This branch: `laxita-dev` (Person 2)

* **Done here:** the insurance, lab and medication agents, with `tests/test_person2_agents.py` (20 end-to-end tests).
* **Placeholders:** Person 1's and Person 3's agent files keep the real function names and arguments but answer
  "not available in this build yet", so the app runs end to end. Shared helper: `app/agents/placeholder.py`.
* **Merging a teammate's agent:** replace their placeholder file with the real one and keep the function signatures
  used in `app/graph.py`. Their tests in `tests/test_flows.py` are marked `@requires_agent(...)` and switch back on
  automatically once the file is no longer a placeholder.
* **Status:** `pytest` gives 32 passed, 7 skipped (the skipped tests need teammates' agents). In the golden set, all
  13 questions answered by Person 2's agents pass (lab G015-G018 and G041, eligibility G019-G023, cost G033,
  medication G034-G035, untrusted email G043).

---

HCLTech Future Ready AI Engineer Hackathon · AI-Powered Hospital Patient Services Assistant

A grounded, secure and auditable assistant for patients of Sunrise Multispeciality Hospital. It answers policy
questions from approved documents with citations, reads the patient's own records through deterministic tools,
performs confirmed actions (reschedule, book), and refuses anything unauthorised or unsafe. Every request gets a
trace ID and an audit record.

**Design principle: code decides, the LLM explains.** Identity, access, eligibility, numbers, version resolution and
the answer type are decided by plain Python. The LLM only helps route unclear questions, summarise uploaded documents
and rephrase verified answers, and a verifier rejects any rephrasing that changes a number or a citation.

## Architecture

```
 Patient / proxy / admin / auditor
        │  Streamlit UI (ui/streamlit_app.py)       FastAPI (app/api.py): /login /ask /confirm /ingest /health /trace /audit /sources
        ▼
 Login (JWT) ── session patient_id + role       ← identity never comes from the question text (R7)
        ▼
 LangGraph workflow (app/graph.py)
   guard ─────► PII masking · emergency red flags · prompt injection · other-patient requests · medication changes
     │            (emergency → ER guidance; unsafe/unauthorised → refused; nothing reaches the agents)
     ▼
   router ────► rules first, LLM only when unsure (ROUTER_MODE)
     ▼
   agents ────► policy · appointment · lab · insurance · billing · medication · care coordination · document
     │            └─ deterministic tools (app/tools): every patient-data tool checks patient_id == session
     │            └─ knowledge base (app/kb): hybrid search + version resolver
     ▼
   verifier ──► answer_type valid · citations present and in force · every number found in a source ·
     │            no other patient mentioned · identifiers masked · LLM rephrasing kept only if facts unchanged
     ▼
   audit ─────► trace_id, masked question, sources, tools, privacy checks, verifier result, latency, model (SQLite)
```

### Hybrid search and version resolution

* **Dense**: `all-MiniLM-L6-v2` embeddings stored in **ChromaDB** with clause metadata (doc, version, effective date).
* **Keyword**: **BM25** over the same clauses (catches exact terms such as "HbA1c" or "ICU").
* **Fusion**: Reciprocal Rank Fusion, then re-ranked by how much of the question each clause covers.
  Markdown tables are indexed one row per chunk, so "HbA1c turnaround" finds its row.
* **Resolver (R4)**: only clauses in force on the as-of date are searched: drafts, future-dated, expired and
  superseded versions are excluded (computed from dates, not trusted from the CSV). A partial amendment replaces only
  the clauses it restates. When two clauses state different quantities for the same thing, the higher authority wins
  (government regulation > national guideline > hospital policy > department SOP), then the superseding/newer
  document; otherwise the answer is `conflict_flagged`.
* **Relevance gate (R3)**: if no clause in force covers the question, the answer is `not_found`.

### Answer types (R9)

`retrieved_fact` · `calculated` · `action_completed` · `clarification_needed` · `not_found` · `refused` · `conflict_flagged`

### Tools (R5)

`find_department` · `get_appointments` · `get_available_slots` · `reschedule_appointment` · `book_appointment` ·
`get_lab_results` · `get_diagnostic_reports` · `check_insurance_eligibility` · `get_preauth_status` ·
`estimate_procedure_cost` · `get_surgery_details` · `get_preop_orders` · `check_care_plan_compliance` ·
`get_billing_summary` · `get_refund_status` · `get_medications` · `drug_interaction_check` · `read_uploaded_document`

### Requirements coverage

| Requirement | Where |
|---|---|
| R1 Knowledge retrieval from approved documents | `app/agents/policy.py`, `app/kb/store.py` |
| R2 Citations with document, section, version, effective date | `app/fmt.py` `doc_citation`, every answer |
| R3 No hallucinations: `not_found` | relevance gate in `policy.py`, number check in `verifier.py` |
| R4 Version and policy resolution | `app/kb/resolver.py` |
| R5 Deterministic healthcare tools | `app/tools/` |
| R6 Multi-step reasoning | `app/agents/care.py` (surgery readiness, pending pre-op tests) |
| R7 Authorisation and privacy | `app/auth.py`, tool-level `authorize_patient`, `guard.py`, masking |
| R8 Untrusted content protection | `guard.sanitize_untrusted`, `agents/documents.py`, ingestion checks |
| R9 Answer classification | `agents/base.py` (7 types), checked in `verifier.py` |
| R10 Auditability | `app/audit.py`, `GET /trace/{trace_id}` |
| R11 Live ingestion | `app/kb/ingest.py`, `POST /ingest`, admin page in the UI |

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env ; then add GROQ_API_KEY (optional)

python -m streamlit run ui/streamlit_app.py # UI at http://localhost:8501  (password: Hospital@123)
python -m uvicorn app.api:app --reload      # API at http://localhost:8000/docs
python -m eval.run_golden         # evaluation report -> eval/report.md
python -m pytest -q               # no API key needed
```

Use `python -m streamlit` rather than bare `streamlit`: if the venv is missing Streamlit, the bare command silently
runs a global install that doesn't have the venv's packages (the cause of `No module named 'chromadb'`).

**Slow network?** `torch` and `chromadb` are large downloads. If your global Python already has most packages, create
the venv with `python -m venv --system-site-packages .venv` so it reuses them, then install with
`pip install --default-timeout=600 --resume-retries 50 -r requirements.txt` (pip 25.1+ resumes interrupted downloads).

The first run builds `store/` (SQLite database, Chroma index) and downloads the MiniLM model (~90 MB). Without an API
key everything still works in offline mode. To go through the API from the UI, start uvicorn and set
`API_URL=http://localhost:8000` before `streamlit run`.

Demo users (password `Hospital@123`): `U-P051` (knee replacement, not ready), `U-P052` (privacy target),
`U-P053` (waiting period), `U-P054` (ready for surgery), `U-P055` (labs need review, warfarin), `U-P056` (critical
potassium), `U-P057` (balance due), `U-P058` (SHAS cataract), `U-P059` (two cardiology appointments, conflicting
policy), `U-P060` (missed physiotherapy, refund), `U-PROXY-01` (registered proxy for P056), `U-ADMIN-01` (uploads),
`U-AUDIT-01` (audit trail).

### API example

```bash
curl -X POST localhost:8000/login -H "Content-Type: application/json" -d '{"user_id":"U-P051","password":"Hospital@123"}'
curl -X POST localhost:8000/ask -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"question":"When is my MRI appointment?"}'
```

```json
{
  "trace_id": "HSP-3F9A1C2B",
  "answer": "You have 3 upcoming appointments: ...",
  "answer_type": "retrieved_fact",
  "citations": [{"id": "Appointment system", "type": "system"}],
  "tools_invoked": [{"tool": "get_appointments", "status": "ok"}],
  "explanation": "Retrieved from the patient appointment management system."
}
```

## Live ingestion (R11)

An admin uploads a Markdown file with front matter (see `data/ingestion_samples/SOP-RAD-001_v1.3.md`) or a PDF with
metadata. Required metadata: `doc_id, title, policy_key, doc_type, authority_level, version, effective_date, status`.
Documents with missing metadata or instructions aimed at the assistant are rejected
(`data/ingestion_samples/bad_missing_metadata.md`). Accepted documents are searchable immediately, without a restart.

## Evaluation

`python -m eval.run_golden` runs the 47-question golden set on a throw-away copy of the data, auto-confirming
proposed actions, plus four live-ingestion checks. Metrics follow section 9 of the use case: answer accuracy,
citation accuracy, privacy compliance, tool accuracy, retrieval hit rate, action success rate, latency p50/p95 and
safety compliance. Results are in `eval/report.md` and on the Evaluation page of the UI.

Offline mode (no LLM, hashing embedder): 47/47 golden questions and 4/4 ingestion checks pass. The thresholds and
routing rules were tuned on this same golden set, so expect lower scores on new questions; re-run the evaluation with
your API key and the MiniLM model on your machine.

## Data

`data/raw/` holds the 30 CSVs. Kaggle provenance columns were removed by `scripts/clean_data.py`
(`source` in patients, doctors, appointments, treatments and billing; `appointments.kaggle_doctor_id`;
`procedures.kaggle_treatment_type`). Rows are kept. `drug_interactions.source` is kept because it is a citation.
`data/untrusted_uploads/` and `data/ingestion_samples/` hold the test files referenced by the golden set
(written for this build). All patient data is synthetic.

## Project layout

| Path | What it does |
|---|---|
| `app/config.py` | Settings from `.env` (as-of date, LLM, embeddings) |
| `app/db.py` | Loads the CSVs into SQLite; demo password hashes; audit table |
| `app/auth.py` | Login, JWT session, `authorize_patient` check |
| `app/guard.py` | PII masking, injection, emergency, other-patient and medication-change detection |
| `app/llm.py` | Groq / Gemini / OpenAI wrapper (OpenAI-compatible) with offline fallback |
| `app/kb/` | Embedder, ChromaDB + BM25 hybrid store, version resolver, live ingestion |
| `app/tools/` | Deterministic healthcare tools with per-call authorisation and audit |
| `app/agents/` | Router and the specialist agents |
| `app/verifier.py` | Response checks and safe LLM rephrasing |
| `app/graph.py` | LangGraph workflow |
| `app/service.py` | `ask(session, question)` entry point and confirm-before-action state |
| `app/api.py`, `app/schemas.py` | FastAPI service and Pydantic contract |
| `ui/` | Streamlit UI |
| `eval/run_golden.py` | Golden-set evaluation and report |
| `tests/` | Pytest suite |

## Responsible AI

* Never diagnoses, interprets results or changes medicines; critical results trigger "contact your care team".
* Emergency warning signs stop routine tasks and give ER guidance first (SOP-EMR-001 4.1).
* Readiness is reported, never granted: final clearance stays with the surgeon and anaesthetist (PROT-PREOP-004 7.2).
* Actions need explicit confirmation; nothing is written before the patient chooses a slot.
* Uploaded files and emails are untrusted data; hidden instructions are removed and logged.
* Logs store masked questions only (POL-PRIV-001 4.4).

## Limitations

* Single-process demo: pending confirmations are kept in memory; use Redis or the database for several workers.
* Rule-based routing covers the use-case question types; unusual phrasings fall back to the LLM (if configured)
  or to policy search.
* Claim-level conflict detection compares quantities with the same unit and context (for example "hours before the
  test"); conflicts expressed differently are not detected automatically.
* Passwords are demo passwords for every user; production needs real identity integration.
