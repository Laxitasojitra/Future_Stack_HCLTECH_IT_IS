"""Login and the trusted session (R7: identity comes from the login token, never from the chat)."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import jwt
from passlib.hash import pbkdf2_sha256

from app import config, db

PATIENT_DATA_ROLES = {"patient", "proxy"}


class AuthError(Exception):
    pass


@dataclass
class Session:
    user_id: str
    role: str
    display_name: str
    patient_id: str = ""          # the patient's own record (role patient)
    proxy_for_patient_id: str = ""  # registered proxy access (POL-PRIV-001 3.1)
    can_upload_documents: bool = False

    @property
    def subject_patient_id(self):
        """The only patient whose records this session may read."""
        if self.role == "patient":
            return self.patient_id
        if self.role == "proxy":
            return self.proxy_for_patient_id
        return ""

    @property
    def is_proxy(self):
        return self.role == "proxy"

    def to_dict(self):
        d = asdict(self)
        d["subject_patient_id"] = self.subject_patient_id
        return d


def _session_from_user(u):
    return Session(
        user_id=u["user_id"], role=u["role"], display_name=u["display_name"],
        patient_id=u.get("patient_id", ""), proxy_for_patient_id=u.get("proxy_for_patient_id", ""),
        can_upload_documents=u.get("can_upload_documents") == "Y",
    )


def login(user_id, password):
    """Check the password and return (token, session)."""
    user = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id.strip(),))
    if not user or not pbkdf2_sha256.verify(password, user["password_hash"] or ""):
        raise AuthError("Invalid user ID or password.")
    session = _session_from_user(user)
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": session.user_id, "iat": now,
                        "exp": now + timedelta(minutes=config.JWT_TTL_MINUTES)},
                       config.JWT_SECRET, algorithm="HS256")
    return token, session


def session_from_token(token):
    """Validate the signed token and rebuild the session from the user table."""
    try:
        claims = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid or expired session: {exc}") from exc
    user = db.query_one("SELECT * FROM users WHERE user_id = ?", (claims["sub"],))
    if not user:
        raise AuthError("Unknown user.")
    return _session_from_user(user)


def session_for_user(user_id):
    """Trusted server-side helper for tests and the evaluation runner (no password)."""
    user = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        raise AuthError(f"Unknown user {user_id}")
    return _session_from_user(user)


def authorize_patient(session, patient_id):
    """R7 check used by every tool: the requested patient must be the session's subject patient."""
    return bool(patient_id) and patient_id == session.subject_patient_id
