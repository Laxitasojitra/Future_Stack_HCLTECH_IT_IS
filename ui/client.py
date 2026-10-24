"""UI client: talks to the FastAPI service when API_URL is set, otherwise calls the same code in-process."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_URL = os.getenv("API_URL", "").rstrip("/")


class ClientError(Exception):
    pass


class Client:
    def __init__(self):
        self.token = None
        self.session = None
        if not API_URL:
            from app import service

            service.startup()

    # ------------------------------------------------------------ HTTP mode
    def _http(self, method, path, **kw):
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=60, **kw)
        if r.status_code >= 400:
            raise ClientError(r.json().get("detail", r.text))
        return r.json()

    # ------------------------------------------------------------ API
    def login(self, user_id, password):
        if API_URL:
            out = self._http("POST", "/login", json={"user_id": user_id, "password": password})
            self.token, self.session = out["token"], out["session"]
            return self.session
        from app import auth

        try:
            self.token, s = auth.login(user_id, password)
        except auth.AuthError as exc:
            raise ClientError(str(exc)) from exc
        self.session = s.to_dict()
        return self.session

    def _session_obj(self):
        from app import auth

        return auth.session_from_token(self.token)

    def ask(self, question):
        if API_URL:
            return self._http("POST", "/ask", json={"question": question})
        from app import service

        return service.ask(self._session_obj(), question)

    def confirm(self, option):
        if API_URL:
            return self._http("POST", "/confirm", json={"option": option})
        from app import service

        return service.ask(self._session_obj(), "cancel" if option == 0 else f"confirm option {option}")

    def ingest(self, file_name, data, metadata=None):
        if API_URL:
            return self._http("POST", "/ingest", files={"file": (file_name, data)},
                              data={"metadata": json.dumps(metadata or {})})
        from app.kb import ingest

        try:
            text = ingest.pdf_to_text(data) if file_name.lower().endswith(".pdf") else data.decode("utf-8")
            return ingest.ingest(text, file_name, extra_meta=metadata, session=self._session_obj())
        except ingest.IngestionError as exc:
            raise ClientError(f"Rejected: {exc}") from exc

    def trace(self, trace_id):
        if API_URL:
            return self._http("GET", f"/trace/{trace_id}")
        from app import audit

        return audit.get(trace_id)

    def audit(self, limit=50):
        if API_URL:
            return self._http("GET", f"/audit?limit={limit}")
        from app import audit

        return audit.recent(limit)

    def sources(self):
        if API_URL:
            return self._http("GET", "/sources")
        from app.api import sources

        return sources()

    def health(self):
        if API_URL:
            return self._http("GET", "/health")
        from app.api import health

        return health()


def demo_users():
    import pandas as pd

    df = pd.read_csv(ROOT / "data" / "raw" / "users.csv", dtype=str, keep_default_na=False)
    patients = pd.read_csv(ROOT / "data" / "raw" / "patients.csv", dtype=str, keep_default_na=False)
    notes = dict(zip(patients.patient_id, patients.scenario_note))
    out = []
    for r in df.to_dict("records"):
        if r["role"] != "patient" or r["patient_id"] >= "P051":
            note = notes.get(r["patient_id"], "") if r["role"] == "patient" else r["display_name"]
            out.append({"user_id": r["user_id"], "role": r["role"], "name": r["display_name"], "note": note})
    return out
