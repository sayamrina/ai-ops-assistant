"""Live demo of the Internal AI Ops Assistant.

It runs the same pipelines as the API (app/), with a per-visitor call budget. Review queue and audit log
live only in the visitor's session, so nothing one visitor enters is shown to anyone else.
"""
import html
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Internal AI Ops Assistant", page_icon="🩺", layout="centered",
                   initial_sidebar_state="collapsed")

try:
    api_key = st.secrets.get("OPENAI_API_KEY")
except FileNotFoundError:  # no secrets file: run offline with the fake model
    api_key = None
if api_key:
    os.environ["OPS_OPENAI_API_KEY"] = api_key
    os.environ["OPS_LLM_PROVIDER"] = "openai"
os.environ.setdefault("OPS_LLM_PROVIDER", "fake")

from app.config import get_settings  # noqa: E402
from app.connectors.notify import teams_card  # noqa: E402
from app.extraction import process_document  # noqa: E402
from app.knowledge.answer import answer_question  # noqa: E402
from app.knowledge.index import KnowledgeIndex  # noqa: E402
from app.llm import build_llm  # noqa: E402
from app.privacy import redact  # noqa: E402
from app.triage import triage_ticket  # noqa: E402

MAX_ACTIONS = 15  # model-backed actions per visitor session, to keep API costs bounded
DATA = ROOT / "data"
settings = get_settings()

# Same visual language as the web UI of the API (app/static/index.html)
st.markdown("""
<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { visibility: hidden; }
header[data-testid="stHeader"] { display: none; }
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
.block-container, [data-testid="stMainBlockContainer"] { padding-top: 0 !important; max-width: 980px; }
.ops-header { background: #0a5555; color: #fff; padding: 22px 24px 18px; border-radius: 0 0 12px 12px; margin-top: 8px; }
.ops-header h1 { color: #fff; font-size: 26px; margin: 0; padding: 0; }
.ops-header p { margin: 4px 0 0; opacity: .85; font-size: 14px; }
.ops-header a { color: #bff0ea; }
.ops-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 14px 0 6px; font-size: 13px; color: #5a6976; }
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid #dbe3e9; }
.stTabs [data-baseweb="tab"] { font-weight: 600; padding: 10px 12px; }
.ops-card { background: #fff; border: 1px solid #dbe3e9; border-radius: 10px; padding: 16px 18px; margin: 12px 0; }
.ops-answer { font-size: 16.5px; line-height: 1.55; color: #17212b; }
.ops-src, .ops-src li { font-size: 13px !important; color: #5a6976; margin: 10px 0 0; padding-left: 18px; }
.ops-src li { margin: 2px 0; }
.ops-hint { font-size: 13px; color: #5a6976; margin-top: 8px; }
.pill { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 12.5px; font-weight: 700; margin: 0 6px 6px 0; }
.pill.ok { background: #e6f4ea; color: #1d6b3a; } .pill.warn { background: #fff4e0; color: #9a5b00; }
.pill.neutral { background: #e8eef3; color: #44535f; }
.ops-table { width: 100%; border-collapse: collapse; font-size: 14.5px; margin-top: 6px; }
.ops-table th, .ops-table td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #dbe3e9; vertical-align: top; }
.ops-table th { width: 38%; color: #44535f; font-weight: 600; }
.ops-pre { background: #0f1b24; color: #d7e6ef; padding: 12px; border-radius: 8px; font-size: 12.5px;
           white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace; margin: 0; }
div.stButton > button[kind="secondary"] { border-radius: 99px; font-size: 13px; padding: 2px 12px; }
</style>
""", unsafe_allow_html=True)

esc = html.escape


@st.cache_resource(show_spinner="Indexing the internal handbook…")
def resources():
    llm = build_llm(settings)
    index = KnowledgeIndex.build(sorted((DATA / "knowledge").glob("*.md")), llm)
    return llm, index


llm, index = resources()
state = st.session_state
state.setdefault("actions", 0)
state.setdefault("queue", [])
state.setdefault("audit", [])
state.setdefault("question", "I lost my phone. How do I get access to my accounts again?")


def budget_ok() -> bool:
    if state.actions >= MAX_ACTIONS:
        st.warning("This demo allows a limited number of model calls per visit. Reload the page later to try again.")
        return False
    state.actions += 1
    return True


def audit(action: str, reference: str, **details) -> None:
    state.audit.insert(0, {"time (UTC)": datetime.now(UTC).strftime("%H:%M:%S"), "actor": "demo-user",
                           "action": action, "reference": reference, "details": json.dumps(details, ensure_ascii=False)})


def card(body: str) -> None:
    st.markdown(f'<div class="ops-card">{body}</div>', unsafe_allow_html=True)


def pill(text: str, kind: str = "neutral") -> str:
    return f'<span class="pill {kind}">{esc(text)}</span>'


def sources_html(sources: list[dict]) -> str:
    if not sources:
        return ""
    items = "".join(f"<li>{esc(s['title'])} → {esc(s['section'])} "
                    f"<span>({esc(s['source'])}, owner: {esc(s['owner'])})</span></li>" for s in sources)
    return f'<ul class="ops-src">{items}</ul>'


def redaction_html(counts: dict) -> str:
    if not counts:
        return ""
    removed = ", ".join(f"{v}× {esc(k)}" for k, v in counts.items())
    return f'<div class="ops-hint">Removed before the model call: {removed}</div>'


def review_pill(needs_review: bool, reasons: list[str]) -> str:
    return pill("Human review: " + "; ".join(reasons), "warn") if needs_review else pill("Automated", "ok")


def table_html(rows: dict) -> str:
    cells = "".join(f"<tr><th>{esc(str(k))}</th><td>{esc(', '.join(v) if isinstance(v, list) else str(v)) or '–'}</td></tr>"
                    for k, v in rows.items())
    return f'<table class="ops-table">{cells}</table>'


open_reviews = sum(i["status"] == "open" for i in state.queue)
mode = "gpt-4o-mini" if settings.llm_provider == "openai" else "offline rules (no API key)"
st.markdown(f"""
<div class="ops-header">
  <h1>Internal AI Ops Assistant</h1>
  <p>Cutora Health (fictional) · knowledge search, document intake, ticket triage, human review ·
     <a href="https://sayamrina.github.io/ai-ops-assistant/">Project page</a> ·
     <a href="https://github.com/sayamrina/ai-ops-assistant">Code</a> ·
     by Amrina (<a href="https://sayamrina.github.io/">portfolio</a>)</p>
</div>
<div class="ops-meta"><span>🔒 Personal data is removed before every model call</span>
<span>🧑‍⚖️ Sensitive cases go to a human</span><span>⚙️ Model: {esc(mode)}</span>
<span>Model calls left: <b>{MAX_ACTIONS - state.actions}</b></span></div>
""", unsafe_allow_html=True)

tab_ask, tab_docs, tab_triage, tab_review = st.tabs(
    ["Ask the handbook", "Document intake", "Ticket triage", f"Review queue ({open_reviews})"])

with tab_ask:
    st.markdown('<div class="ops-hint">Try an example:</div>', unsafe_allow_html=True)
    examples = ["Can I get MFA codes by SMS?", "My laptop was stolen. What do I do?",
                "Who has to approve a refund of 200 EUR?", "What is the parental leave policy?"]
    cols = st.columns(2) + st.columns(2)
    for col, ex in zip(cols, examples, strict=True):
        if col.button(ex, key=f"ex_{ex}", width="stretch"):
            state.question = ex
    question = st.text_input("Question about an internal policy", key="question", max_chars=500)
    ask = st.button("Ask", type="primary")
    st.markdown('<div class="ops-hint">Answers come only from internal documents and always show their sources.</div>',
                unsafe_allow_html=True)
    if ask and question.strip() and budget_ok():
        with st.spinner("Searching the handbook…"):
            result = answer_question(question, index, llm, settings.top_k, settings.min_retrieval_score)
        audit("ask", "-", found=result.found)
        if result.found:
            card(f'<div class="ops-answer">{esc(result.answer)}</div>{sources_html(result.sources)}'
                 f'{redaction_html(result.redacted_fields)}')
        else:
            card(pill("Not in the documents") + " The handbook does not answer this. The assistant does not guess; "
                 "please ask the document owner." + redaction_html(result.redacted_fields))
        with st.expander("Under the hood: what the model saw and which sections were retrieved"):
            clean = redact(question).text
            st.markdown(f'<pre class="ops-pre">{esc(clean)}</pre>', unsafe_allow_html=True)
            rows = {f"{c.title} → {c.section}": f"{s:.2f}" + ("" if s >= settings.min_retrieval_score else " (below threshold)")
                    for c, s in index.search(clean, llm, settings.top_k)}
            st.markdown(table_html(rows) + '<div class="ops-hint">Hybrid score: 0.75 vector similarity + 0.25 keyword match '
                        "(BM25).</div>", unsafe_allow_html=True)

with tab_docs:
    samples = sorted(p.name for p in (DATA / "inbox").glob("*.txt"))
    choice = st.selectbox("Sample document (fictional)", samples, index=samples.index("009_dsr_access.txt"))
    text = st.text_area("Document text (edit it or paste your own; please no real personal data)",
                        (DATA / "inbox" / choice).read_text(), height=200, key=f"doc_{choice}")
    if st.button("Process document", type="primary") and text.strip() and budget_ok():
        with st.spinner("Pseudonymising, classifying and extracting…"):
            result = process_document(choice, text, llm, settings.review_confidence_threshold)
        audit("document_processed", choice, doc_type=result.doc_type, needs_review=result.needs_review)
        if result.needs_review:
            state.queue.append({"id": len(state.queue) + 1, "kind": "document", "reference": choice,
                                "reason": "; ".join(result.review_reasons), "result": asdict(result), "status": "open"})
        card(pill(result.doc_type) + review_pill(result.needs_review, result.review_reasons)
             + f'<span class="ops-hint">confidence {result.confidence}</span>'
             + table_html(result.fields or {"fields": "–"}) + redaction_html(result.redacted_fields))
        with st.expander("Text the model saw (after pseudonymisation)"):
            st.markdown(f'<pre class="ops-pre">{esc(redact(text).text)}</pre>', unsafe_allow_html=True)

with tab_triage:
    tickets = [json.loads(line) for line in (DATA / "eval" / "tickets.jsonl").read_text().splitlines() if line]
    labels = [f"{t['key']}: {t['summary']}" for t in tickets]
    ticket = tickets[labels.index(st.selectbox("Sample ticket", labels))]
    key = st.text_input("Ticket key", ticket["key"], key=f"k_{ticket['key']}")
    summary = st.text_input("Summary", ticket["summary"], key=f"s_{ticket['key']}", max_chars=300)
    description = st.text_area("Description", ticket["description"], key=f"d_{ticket['key']}", max_chars=3000)
    if st.button("Triage ticket", type="primary") and summary.strip() and budget_ok():
        with st.spinner("Classifying and drafting a reply…"):
            result = triage_ticket(key, summary, description, llm, index, settings.top_k,
                                   settings.min_retrieval_score, settings.review_confidence_threshold)
        audit("ticket_triaged", key, category=result.category, priority=result.priority, needs_review=result.needs_review)
        if result.needs_review:
            state.queue.append({"id": len(state.queue) + 1, "kind": "ticket", "reference": key,
                                "reason": "; ".join(result.review_reasons), "result": asdict(result), "status": "open"})
        routing = ("Not sent automatically: waiting for a person." if result.needs_review
                   else "Routine ticket: would be posted to the team's Teams channel and commented in Jira.")
        reply = ('<p style="margin:12px 0 4px"><b>Suggested reply (draft)</b></p>'
                 f'<div class="ops-answer">{esc(result.reply_draft)}</div>{sources_html(result.sources)}'
                 if result.reply_draft else "")
        card(pill(f"{result.category} → {result.team}") + pill(f"priority: {result.priority}")
             + review_pill(result.needs_review, result.review_reasons)
             + f'<div class="ops-hint">{esc(result.rationale)} · {esc(routing)}</div>{reply}')
        with st.expander("Teams message that would be sent (Adaptive Card)"):
            st.json(teams_card(f"{key}: {result.category} → {result.team}",
                               {"Priority": result.priority, "Confidence": str(result.confidence)},
                               result.reply_draft or "No answer found in the knowledge base."))

with tab_review:
    open_items = [i for i in state.queue if i["status"] == "open"]
    if not open_items:
        card('<span class="ops-hint">Nothing to review yet. Process a GDPR request in <b>Document intake</b> or triage the '
             "<b>Laptop stolen</b> ticket to see one here.</span>")
    for item in open_items:
        shown = ({"doc_type": item["result"]["doc_type"], **item["result"]["fields"]} if item["kind"] == "document"
                 else {k: item["result"][k] for k in ("category", "team", "priority", "reply_draft")})
        card(f"<b>#{item['id']} {esc(item['kind'])}: {esc(item['reference'])}</b><br>{pill(item['reason'], 'warn')}"
             f'<pre class="ops-pre">{esc(json.dumps(shown, indent=2, ensure_ascii=False))}</pre>')
        a, r, _ = st.columns([1, 1, 5])
        if a.button("Approve", key=f"a{item['id']}"):
            item["status"] = "approved"
            audit("review_approved", item["reference"])
            st.rerun()
        if r.button("Reject", key=f"r{item['id']}"):
            item["status"] = "rejected"
            audit("review_rejected", item["reference"])
            st.rerun()
    if state.audit:
        st.markdown("**Audit log (this session)**")
        st.dataframe(state.audit, width="stretch", hide_index=True)

st.markdown('<div class="ops-hint" style="margin:28px 0 10px">All companies, people and documents are fictional. Your inputs '
            "stay in your session only. Built by Amrina, with AI as a supporting tool for coding.</div>",
            unsafe_allow_html=True)
