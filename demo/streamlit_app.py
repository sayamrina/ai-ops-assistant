"""Live demo of the Internal AI Ops Assistant.

It runs the same pipelines as the API (app/), with a per-visitor call budget. Review queue and audit log
live only in the visitor's session, so nothing one visitor enters is shown to anyone else.
"""
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Internal AI Ops Assistant", page_icon="🩺", layout="wide")

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


def budget_ok() -> bool:
    if state.actions >= MAX_ACTIONS:
        st.warning("This demo allows a limited number of model calls per visit. Reload the page later to try again.")
        return False
    state.actions += 1
    return True


def audit(action: str, reference: str, **details) -> None:
    state.audit.insert(0, {"time (UTC)": datetime.now(UTC).strftime("%H:%M:%S"), "actor": "demo-user",
                           "action": action, "reference": reference, "details": json.dumps(details, ensure_ascii=False)})


def show_sources(sources: list[dict]) -> None:
    for s in sources:
        st.caption(f"📄 {s['title']} → {s['section']}  ·  {s['source']}  ·  owner: {s['owner']}  ·  score {s['score']}")


def show_redaction(counts: dict) -> None:
    if counts:
        st.caption("Removed before the model call: " + ", ".join(f"{v}× {k}" for k, v in counts.items()))


st.title("Internal AI Ops Assistant")
st.markdown("An internal AI service for **Cutora Health**, a fictional telemedicine company: knowledge search with sources, "
            "document intake and ticket triage. Personal data is removed before every model call, and sensitive cases go to "
            "a human review queue.  \n[Project page](https://sayamrina.github.io/ai-ops-assistant/) · "
            "[Code](https://github.com/sayamrina/ai-ops-assistant) · by Amrina ([portfolio](https://sayamrina.github.io/))")
if settings.llm_provider == "fake":
    st.info("Offline mode: no API key configured, so a simple rule-based stand-in replaces the language model.")

tab_ask, tab_docs, tab_triage, tab_review = st.tabs(["Ask the handbook", "Document intake", "Ticket triage",
                                                     f"Review queue ({sum(i['status'] == 'open' for i in state.queue)})"])

with tab_ask:
    examples = ["I lost my phone. How do I get access to my accounts again?", "Can I get MFA codes by SMS?",
                "A patient asks which dose of the cream to use. What do I answer?", "Who has to approve a refund of 200 EUR?",
                "What is the company's parental leave policy?"]
    question = st.selectbox("Example questions (or write your own below)", examples)
    question = st.text_input("Question about an internal policy", question, max_chars=500)
    if st.button("Ask", type="primary") and question.strip() and budget_ok():
        with st.spinner("Searching the handbook…"):
            result = answer_question(question, index, llm, settings.top_k, settings.min_retrieval_score)
        audit("ask", "-", found=result.found)
        if result.found:
            st.success(result.answer)
            show_sources(result.sources)
        else:
            st.warning("The handbook does not answer this question. The assistant does not guess; ask the document owner.")
        show_redaction(result.redacted_fields)
        with st.expander("Under the hood"):
            st.markdown("**Text sent to the model (after pseudonymisation)**")
            st.code(redact(question).text)
            st.markdown("**Retrieved sections (hybrid score: 0.75 vector similarity + 0.25 keyword BM25)**")
            for chunk, score in index.search(redact(question).text, llm, settings.top_k):
                flag = "" if score >= settings.min_retrieval_score else " (below threshold, not used)"
                st.caption(f"{score:.2f} · {chunk.title} → {chunk.section}{flag}")

with tab_docs:
    samples = sorted(p.name for p in (DATA / "inbox").glob("*.txt"))
    choice = st.selectbox("Sample document (fictional)", samples, index=1)
    text = st.text_area("Document text (edit it or paste your own; please no real personal data)",
                        (DATA / "inbox" / choice).read_text(), height=220, key=f"doc_{choice}")
    if st.button("Process document", type="primary") and text.strip() and budget_ok():
        with st.spinner("Pseudonymising, classifying and extracting…"):
            result = process_document(choice, text, llm, settings.review_confidence_threshold)
        audit("document_processed", choice, doc_type=result.doc_type, needs_review=result.needs_review)
        if result.needs_review:
            state.queue.append({"id": len(state.queue) + 1, "kind": "document", "reference": choice,
                                "reason": "; ".join(result.review_reasons), "result": asdict(result), "status": "open"})
        left, right = st.columns(2)
        with left:
            st.markdown(f"**Type:** `{result.doc_type}` · confidence {result.confidence}")
            if result.needs_review:
                st.warning("Sent to human review: " + "; ".join(result.review_reasons))
            else:
                st.success("Processed automatically")
            st.table({k: [", ".join(v) if isinstance(v, list) else v] for k, v in result.fields.items()} or {"fields": ["–"]})
            show_redaction(result.redacted_fields)
        with right:
            st.markdown("**Text the model saw**")
            st.code(redact(text).text, language=None)

with tab_triage:
    tickets = [json.loads(line) for line in (DATA / "eval" / "tickets.jsonl").read_text().splitlines() if line]
    pick = st.selectbox("Sample ticket", [f"{t['key']}: {t['summary']}" for t in tickets])
    ticket = tickets[[f"{t['key']}: {t['summary']}" for t in tickets].index(pick)]
    key = st.text_input("Ticket key", ticket["key"], key=f"k_{pick}")
    summary = st.text_input("Summary", ticket["summary"], key=f"s_{pick}", max_chars=300)
    description = st.text_area("Description", ticket["description"], key=f"d_{pick}", max_chars=3000)
    if st.button("Triage ticket", type="primary") and summary.strip() and budget_ok():
        with st.spinner("Classifying and drafting a reply…"):
            result = triage_ticket(key, summary, description, llm, index, settings.top_k,
                                   settings.min_retrieval_score, settings.review_confidence_threshold)
        audit("ticket_triaged", key, category=result.category, priority=result.priority, needs_review=result.needs_review)
        c1, c2, c3 = st.columns(3)
        c1.metric("Category", result.category)
        c2.metric("Team", result.team)
        c3.metric("Priority", result.priority)
        st.caption(result.rationale)
        if result.needs_review:
            state.queue.append({"id": len(state.queue) + 1, "kind": "ticket", "reference": key,
                                "reason": "; ".join(result.review_reasons), "result": asdict(result), "status": "open"})
            st.warning("Not sent automatically. Human review needed: " + "; ".join(result.review_reasons))
        else:
            st.success("Routine ticket: would be posted to the team's Teams channel and commented in Jira.")
        if result.reply_draft:
            st.markdown("**Suggested reply (draft)**")
            st.info(result.reply_draft)
            show_sources(result.sources)
        with st.expander("Teams message that would be sent (Adaptive Card)"):
            st.json(teams_card(f"{key}: {result.category} → {result.team}",
                               {"Priority": result.priority, "Confidence": str(result.confidence)},
                               result.reply_draft or "No answer found in the knowledge base."))

with tab_review:
    open_items = [i for i in state.queue if i["status"] == "open"]
    if not open_items:
        st.info("Nothing to review yet. Process a GDPR request or a security ticket to see one here.")
    for item in open_items:
        with st.container(border=True):
            st.markdown(f"**#{item['id']} {item['kind']}: {item['reference']}**")
            st.warning(item["reason"])
            st.json(item["result"].get("fields") or {k: item["result"][k] for k in ("category", "priority", "team")})
            a, r, _ = st.columns([1, 1, 6])
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
        st.dataframe(state.audit, use_container_width=True, hide_index=True)

with st.sidebar:
    st.markdown("### About this demo")
    st.markdown("Same pipelines as the FastAPI service in the repository. The full service adds role-based API keys, "
                "signed Jira webhooks, Teams and Jira delivery, Prometheus metrics and a persistent review queue.")
    st.markdown(f"Model calls left this visit: **{MAX_ACTIONS - state.actions}**")
    st.caption("All companies, people and documents are fictional. Your inputs stay in your session only.")
