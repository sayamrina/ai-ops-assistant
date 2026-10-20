from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..connectors.notify import teams_card
from ..extraction import process_document
from ..knowledge.answer import answer_question
from ..security import Principal, require_role
from ..services import Services, get_services
from ..triage import triage_ticket

router = APIRouter(prefix="/api", tags=["assistant"])
MAX_UPLOAD = 2_000_000


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class Ticket(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]+-\d+$", examples=["ITOPS-123"])
    summary: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=10_000)
    notify: bool = False


@router.post("/ask")
def ask(body: Question, who: Principal = Depends(require_role("viewer")), svc: Services = Depends(get_services)):
    s = svc.settings
    result = answer_question(body.question, svc.index, svc.llm, s.top_k, s.min_retrieval_score)
    svc.store.audit(who.name, "ask", None, found=result.found, sources=[x["source"] for x in result.sources],
                    redacted=result.redacted_fields)
    return asdict(result)


@router.post("/documents")
async def documents(file: UploadFile = File(...), who: Principal = Depends(require_role("operator")),
                    svc: Services = Depends(get_services)):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max. 2 MB)")
    if file.filename.lower().endswith(".pdf"):
        import io

        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
    else:
        text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(422, "No text found in the document")
    result = process_document(file.filename, text, svc.llm, svc.settings.review_confidence_threshold)
    review_id = None
    if result.needs_review:
        review_id = svc.store.add_review("document", file.filename, "; ".join(result.review_reasons), asdict(result))
    svc.store.audit(who.name, "document_processed", file.filename, doc_type=result.doc_type,
                    needs_review=result.needs_review, review_id=review_id)
    return asdict(result) | {"review_id": review_id}


def run_triage(ticket: Ticket, actor: str, svc: Services) -> dict:
    s = svc.settings
    result = triage_ticket(ticket.key, ticket.summary, ticket.description, svc.llm, svc.index,
                           s.top_k, s.min_retrieval_score, s.review_confidence_threshold)
    review_id = None
    delivery = {}
    if result.needs_review:
        review_id = svc.store.add_review("ticket", ticket.key, "; ".join(result.review_reasons), asdict(result))
    elif ticket.notify:
        # Only routine, non-sensitive tickets are posted automatically
        delivery["teams"] = svc.teams.send(teams_card(
            f"{ticket.key}: {result.category} → {result.team}",
            {"Priority": result.priority, "Confidence": str(result.confidence)},
            result.reply_draft or "No answer found in the knowledge base."))
        if result.reply_draft:
            sources = ", ".join(f"{x['title']} ({x['section']})" for x in result.sources)
            delivery["jira"] = svc.jira.comment(ticket.key, f"Suggested reply (AI draft, please check):\n"
                                                            f"{result.reply_draft}\nSources: {sources}")
    svc.store.audit(actor, "ticket_triaged", ticket.key, category=result.category, priority=result.priority,
                    needs_review=result.needs_review, review_id=review_id, delivery=delivery)
    return asdict(result) | {"review_id": review_id, "delivery": delivery}


@router.post("/triage")
def triage(ticket: Ticket, who: Principal = Depends(require_role("operator")), svc: Services = Depends(get_services)):
    return run_triage(ticket, who.name, svc)
