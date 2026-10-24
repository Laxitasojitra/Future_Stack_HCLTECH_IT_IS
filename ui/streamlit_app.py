"""Streamlit UI.  Run:  streamlit run ui/streamlit_app.py

Pages: Chat (patients and proxies), Knowledge base (source register + admin upload),
Audit trail (auditors and admins), Evaluation (golden-set results).
Set API_URL=http://localhost:8000 to go through the FastAPI service instead of in-process calls.
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from client import ROOT, Client, ClientError, demo_users

st.set_page_config(page_title="Sunrise Patient Services", page_icon="🏥", layout="wide")

TYPE_STYLE = {   # answer_type -> (label shown to the patient, colour of the left rule)
    "retrieved_fact": ("From hospital sources", "#2F6F73"),
    "calculated": ("Calculated from your records", "#3D5A80"),
    "action_completed": ("Done", "#2E7D32"),
    "clarification_needed": ("Needs your reply", "#A86B00"),
    "not_found": ("Not in authorised sources", "#6B7280"),
    "refused": ("Can't share this", "#8E2C48"),
    "conflict_flagged": ("Sources disagree", "#B45309"),
}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stTextInput input, .stChatInput textarea {
  font-family: 'Atkinson Hyperlegible', sans-serif; }
.block-container { max-width: 980px; padding-top: 2rem; }
.answer { border-left: 5px solid var(--rule); padding: 0.2rem 0 0.2rem 1rem; margin: 0.2rem 0 0.6rem 0; }
.answer .kind { color: var(--rule); font-weight: 700; font-size: 0.85rem; margin-bottom: 0.35rem; }
.answer .body { line-height: 1.55; font-size: 1.02rem; }
.cite { font-size: 0.85rem; color: #4B5563; line-height: 1.5; }
.trace { font-size: 0.78rem; color: #6B7280; }
.who { font-size: 0.9rem; color: #374151; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Starting the assistant and loading the knowledge base...")
def warm_up():
    Client()          # loads the database, knowledge base and workflow once per server
    return True


def get_client():
    warm_up()
    if "client" not in st.session_state:      # one client (and login token) per browser session
        st.session_state.client = Client()
    return st.session_state.client


def render_answer(r):
    label, colour = TYPE_STYLE.get(r["answer_type"], (r["answer_type"], "#374151"))
    body = (r["answer"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
    st.markdown(f'<div class="answer" style="--rule:{colour}"><div class="kind">{label}</div>'
                f'<div class="body">{body}</div></div>', unsafe_allow_html=True)
    docs = [c for c in r["citations"] if c["type"] == "document"]
    other = [c for c in r["citations"] if c["type"] != "document"]
    with st.expander(f"Sources and checks · trace {r['trace_id']}"):
        if docs:
            st.markdown("**Documents**")
            for c in docs:
                role = "" if c.get("role") in (None, "supporting") else f" ({c['role']})"
                st.markdown(f'<div class="cite">{c["title"]}, {c["doc_id"]} §{c["clause"]} · version {c["version"]} · '
                            f'effective {c["effective_date"]}{role}</div>', unsafe_allow_html=True)
        if other:
            st.markdown("**Systems**")
            st.markdown('<div class="cite">' + "<br>".join(c["id"] for c in other) + "</div>", unsafe_allow_html=True)
        if r["tools_invoked"]:
            st.markdown("**Tools used**: " + ", ".join(f"`{t['tool']}`" for t in r["tools_invoked"]))
        st.markdown(f'<div class="trace">{r["explanation"]}<br>Route: {" → ".join(r["agents"])} · '
                    f'{r["latency_ms"]} ms · model: {r["model"]} · verifier: '
                    f'{"passed" if r["verifier"]["passed"] else "blocked: " + "; ".join(r["verifier"]["problems"])}</div>',
                    unsafe_allow_html=True)


# ---------------------------------------------------------------- login

def login_page(client):
    st.title("Sunrise Multispeciality Hospital")
    st.write("Patient services assistant. Answers come only from hospital policies and your own records, "
             "with the source shown for every fact.")
    users = demo_users()
    labels = {f"{u['user_id']} - {u['name']} ({u['role']})": u for u in users}
    choice = st.selectbox("Demo login", list(labels))
    u = labels[choice]
    if u["note"]:
        st.caption(u["note"])
    password = st.text_input("Password", type="password", help="Demo password is set by DEMO_PASSWORD (default Hospital@123)")
    if st.button("Log in", type="primary"):
        try:
            st.session_state.session = client.login(u["user_id"], password)
            st.session_state.history = []
            st.rerun()
        except ClientError as exc:
            st.error(str(exc))


# ---------------------------------------------------------------- pages

SUGGESTIONS = {
    "patient": ["What are my upcoming appointments?", "Can I proceed with surgery next week?",
                "Am I covered for knee replacement under my insurance plan?", "What is my latest blood sugar reading?",
                "What are the visiting hours for ICU patients?", "Reschedule my cardiology appointment."],
    "proxy": ["What is my mother's latest potassium result?", "What are the visiting hours for ICU patients?"],
}


def chat_page(client):
    s = st.session_state.session
    if s["role"] not in ("patient", "proxy"):
        st.info("Your role can ask policy questions here. Patient records are only shared with the patient or a "
                "registered proxy.")
    for turn in st.session_state.history:
        with st.chat_message("user", avatar="🙂"):
            st.write(turn["q"])
        with st.chat_message("assistant", avatar="🏥"):
            render_answer(turn["r"])

    last = st.session_state.history[-1]["r"] if st.session_state.history else None
    if last and last.get("pending_action"):
        st.markdown("**Choose a slot to confirm:**")
        for opt in last["pending_action"]["options"]:
            if st.button(f"Confirm {opt['label']}", key=f"opt{opt['option']}"):
                send(client, f"Confirm option {opt['option']}", lambda o=opt["option"]: client.confirm(o))
        if st.button("Keep my current booking", key="cancel"):
            send(client, "Cancel", lambda: client.confirm(0))

    if not st.session_state.history:
        st.markdown("Try asking:")
        cols = st.columns(2)
        for i, q in enumerate(SUGGESTIONS.get(s["role"], SUGGESTIONS["patient"])):
            if cols[i % 2].button(q, key=f"sug{i}"):
                send(client, q, lambda q=q: client.ask(q))

    q = st.chat_input("Ask about appointments, results, insurance, bills or hospital policies")
    if q:
        send(client, q, lambda: client.ask(q))


def send(client, label, fn):
    try:
        r = fn()
    except ClientError as exc:
        st.error(str(exc))
        return
    st.session_state.history.append({"q": label, "r": r})
    st.rerun()


def sources_page(client):
    st.subheader("Knowledge base")
    h = client.health()
    kb = h["knowledge_base"]
    st.write(f"{kb['documents']} documents, {h['clauses_in_force']} clauses in force on {h['as_of_date']}. "
             f"Search: {kb['vector_store']} ({kb['embedder']}) + {kb['keyword_index']}, fused with {kb['fusion']}.")
    df = pd.DataFrame(client.sources())
    st.dataframe(df[["doc_id", "title", "version", "authority_level", "effective_date", "expiry_date", "status",
                     "in_force_today", "superseded_by", "origin"]], hide_index=True, use_container_width=True)
    s = st.session_state.session
    if s.get("can_upload_documents"):
        st.subheader("Upload a new document")
        st.write("Markdown with a front-matter block (doc_id, title, policy_key, doc_type, authority_level, version, "
                 "effective_date, status), or a PDF plus the metadata below. It becomes searchable immediately.")
        f = st.file_uploader("Document", type=["md", "txt", "pdf"])
        meta = st.text_area("Metadata as JSON (needed for PDFs)", "{}")
        if f and st.button("Upload", type="primary"):
            try:
                out = client.ingest(f.name, f.getvalue(), json.loads(meta or "{}"))
                st.success(f"Added {out['doc_id']} v{out['version']} ({out['clauses_added']} clauses), effective "
                           f"{out['effective_date']}. It is searchable now.")
            except (ClientError, json.JSONDecodeError) as exc:
                st.error(str(exc))


def audit_page(client):
    st.subheader("Audit trail")
    try:
        rows = client.audit(100)
    except ClientError as exc:
        st.error(str(exc))
        return
    if not rows:
        st.write("No requests recorded yet.")
        return
    df = pd.DataFrame([{"trace_id": r["trace_id"], "time": r["timestamp"], "user": r["user_id"], "intent": r["intent"],
                        "answer_type": r["answer_type"], "latency_ms": r["latency_ms"], "llm_calls": r["llm_calls"],
                        "question (masked)": r["question_masked"]} for r in rows])
    st.dataframe(df, hide_index=True, use_container_width=True)
    pick = st.selectbox("Open a trace", df["trace_id"])
    st.json(client.trace(pick))


def eval_page():
    st.subheader("Evaluation on the golden set")
    path = ROOT / "eval" / "summary.json"
    if not path.exists():
        st.write("Run `python -m eval.run_golden` to create the report.")
        return
    summary = json.loads(path.read_text(encoding="utf-8"))
    st.table(pd.DataFrame(summary["metrics"].items(), columns=["Metric", "Result"]))
    cats = pd.DataFrame([{"category": k, "passed": v["passed"], "failed": v["total"] - v["passed"]}
                         for k, v in summary["by_category"].items()])
    fig = px.bar(cats.melt(id_vars="category", var_name="result", value_name="questions"), y="category",
                 x="questions", color="result", orientation="h",
                 color_discrete_map={"passed": "#2F6F73", "failed": "#8E2C48"}, height=420)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)
    if summary.get("ingestion"):
        st.markdown("**Live ingestion checks**")
        st.table(pd.DataFrame(summary["ingestion"]))
    report = ROOT / "eval" / "report.md"
    if report.exists():
        with st.expander("Full report"):
            st.markdown(report.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- main

def main():
    client = get_client()
    if "session" not in st.session_state:
        login_page(client)
        return
    s = st.session_state.session
    with st.sidebar:
        st.markdown(f'<div class="who"><b>{s["display_name"]}</b><br>{s["role"]}'
                    f'{" · patient " + s["subject_patient_id"] if s["subject_patient_id"] else ""}</div>',
                    unsafe_allow_html=True)
        pages = ["Chat", "Knowledge base"]
        if s["role"] in ("auditor", "admin"):
            pages.append("Audit trail")
        pages.append("Evaluation")
        page = st.radio("Go to", pages, label_visibility="collapsed")
        if st.button("Log out"):
            for k in ("session", "history", "client"):
                st.session_state.pop(k, None)
            st.rerun()
    {"Chat": lambda: chat_page(client), "Knowledge base": lambda: sources_page(client),
     "Audit trail": lambda: audit_page(client), "Evaluation": eval_page}[page]()


main()
