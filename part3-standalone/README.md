# Person 3 agents - standalone test build

Runs only the **Care coordination agent** (surgery readiness, pending pre-op tests, care-plan follow-ups,
child vaccinations) and the **Appointment agent** (view, reschedule, book, confirm-before-action), plus the
department finder, from a command-line chat.

## Run (Windows PowerShell)

```powershell
cd part3-standalone
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_agent.py --demo                 # scripted demo for all scenario patients
python run_agent.py --demo --toy-vaccines  # also the vaccination demo (TEST-ONLY made-up vaccines)
python run_agent.py --user U-P051          # chat; type quit to exit
```

Every run starts from a fresh copy of the data in `store/standalone.db`.

Users to try: `U-P051` (surgery not ready, reschedule), `U-P054` (ready), `U-P055` (labs need review),
`U-P059` (two cardiology appointments), `U-P060` (missed physiotherapy, booking), `U-G900` (needs `--toy-vaccines`).

## What is real and what is a stand-in

| Real (your code, same as the full project) | Stand-in (only so it runs alone) |
|---|---|
| `app/agents/care.py`, `appointment.py`, `directory.py`, `base.py` | `app/kb/store.py`: keyword lookup instead of hybrid search |
| `app/tools/__init__.py`, `core.py`, `care_appointments.py` | `app/auth.py`: pick a user, no password |
| `app/fmt.py` | `app/db.py`, `app/config.py`, `app/guard.py`: minimal versions |
| | `app/tools/insurance_lab.py`, `records_billing.py`: teammates' files, needed for lab and pre-auth lookups |
| | `run_agent.py`: simple keyword router (no LLM, no verifier, no audit) |

Don't commit the stand-in files to your branch; commit the files from `hospital-assistant-part3.zip`.
