"""API contract (Pydantic v2). The response shape follows section 8 of the use-case document."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

AnswerType = Literal["retrieved_fact", "calculated", "action_completed", "clarification_needed", "not_found",
                     "refused", "conflict_flagged"]


class LoginRequest(BaseModel):
    user_id: str = Field(examples=["U-P051"])
    password: str = Field(examples=["Hospital@123"])


class LoginResponse(BaseModel):
    token: str
    session: dict


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, examples=["What are the visiting hours for ICU patients?"])
    as_of_date: Optional[str] = Field(default=None, description="Optional YYYY-MM-DD to answer as of another date")


class ConfirmRequest(BaseModel):
    option: int = Field(ge=0, description="Slot option to confirm; 0 cancels")


class Citation(BaseModel):
    id: str
    type: Literal["document", "system", "upload"]
    doc_id: Optional[str] = None
    clause: Optional[str] = None
    title: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[str] = None
    authority_level: Optional[str] = None
    role: Optional[str] = None
    detail: Optional[str] = None


class ToolInvocation(BaseModel):
    tool: str
    status: str


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation]
    tools_invoked: list[ToolInvocation]
    explanation: str
    intent: str
    agents: list[str]
    pending_action: Optional[dict] = None
    latency_ms: Optional[int] = None
    model: str
    verifier: dict
    as_of_date: str


class IngestResponse(BaseModel):
    status: str
    doc_id: str
    version: str
    effective_date: str
    clauses_added: int
    supersedes: str = ""
    searchable_now: bool = True
