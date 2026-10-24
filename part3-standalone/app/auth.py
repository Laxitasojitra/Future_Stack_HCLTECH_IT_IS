"""Minimal session (stand-in for Person 2's auth.py). No passwords here: you pick a demo user."""

from dataclasses import dataclass

from app import db


@dataclass
class Session:
    user_id: str
    role: str
    display_name: str
    patient_id: str = ""
    proxy_for_patient_id: str = ""

    @property
    def subject_patient_id(self):
        return self.patient_id if self.role == "patient" else self.proxy_for_patient_id if self.role == "proxy" else ""

    @property
    def is_proxy(self):
        return self.role == "proxy"


def session_for_user(user_id):
    u = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not u:
        raise ValueError(f"Unknown user {user_id}")
    return Session(u["user_id"], u["role"], u["display_name"], u["patient_id"], u["proxy_for_patient_id"])


def authorize_patient(session, patient_id):
    """Same rule as the full app: tools may only read the logged-in patient's records."""
    return bool(patient_id) and patient_id == session.subject_patient_id
