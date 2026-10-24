"""Central settings, read from environment variables / .env."""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name, default=""):
    return os.getenv(name, default).strip()


# Paths
DATA_DIR = Path(_env("DATA_DIR", str(ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"
UPLOADS_DIR = DATA_DIR / "untrusted_uploads"
STORE_DIR = Path(_env("STORE_DIR", str(ROOT / "store")))   # SQLite DB, Chroma, ingested docs
DB_PATH = STORE_DIR / "hospital.db"
CHROMA_DIR = STORE_DIR / "chroma"
INGESTED_DIR = STORE_DIR / "ingested_docs"

# Business clock: the dataset is built around 24 Oct 2026. Override with AS_OF_DATE=YYYY-MM-DD
# or AS_OF_DATE=today.
_as_of = _env("AS_OF_DATE", "2026-10-24")
AS_OF_DATE = date.today() if _as_of.lower() == "today" else date.fromisoformat(_as_of)

# Auth
JWT_SECRET = _env("JWT_SECRET", "dev-only-secret-change-me-before-any-real-deployment")
JWT_TTL_MINUTES = int(_env("JWT_TTL_MINUTES", "240"))
DEMO_PASSWORD = _env("DEMO_PASSWORD", "Hospital@123")

# LLM (Groq first; OpenAI-compatible). LLM_PROVIDER = groq | gemini | openai | none
LLM_PROVIDER = _env("LLM_PROVIDER", "groq").lower()
LLM_MODEL = _env("LLM_MODEL")
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "20"))
# How the router uses the LLM: hybrid (rules first, LLM when rules are unsure) | llm | rules
ROUTER_MODE = _env("ROUTER_MODE", "hybrid").lower()
# Let the LLM rephrase verified answers (the verifier still checks every number and citation)
LLM_REPHRASE = _env("LLM_REPHRASE", "true").lower() == "true"

# Retrieval
EMBED_MODEL = _env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_BACKEND = _env("EMBED_BACKEND", "auto").lower()   # auto | minilm | hashing
TOP_K = int(_env("TOP_K", "6"))
RRF_K = 60
