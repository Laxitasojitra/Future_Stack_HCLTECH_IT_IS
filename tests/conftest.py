"""Tests run on an isolated copy of the store with the offline embedder (no API key, no model download)."""

import os
import sys
import tempfile
from pathlib import Path

os.environ["STORE_DIR"] = tempfile.mkdtemp(prefix="hospital_test_")
os.environ.setdefault("EMBED_BACKEND", "hashing")
os.environ["LLM_PROVIDER"] = "none"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import auth, db, service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def app_ready():
    db.init_db(reset=True)
    service.startup()


@pytest.fixture
def ask():
    def _ask(user_id, question):
        session = auth.session_for_user(user_id)
        service.clear_pending(user_id)
        return service.ask(session, question)
    return _ask
