"""Ticket triage: category, priority, routing and a cited reply draft from the knowledge base."""
from dataclasses import dataclass, field

from .knowledge.answer import answer_question
from .knowledge.index import KnowledgeIndex
from .llm import LLM
from .observability import PIPELINE_RESULTS
from .privacy import redact

CATEGORIES = {
    "it_access": "IT Operations",
    "security_incident": "Information Security",
    "data_protection": "Data Protection Officer",
    "billing": "Patient Support",
    "prescription": "Medical Operations",
    "platform_bug": "Engineering",
    "other": "Service Desk",
}
ALWAYS_HUMAN = {"security_incident", "data_protection"}  # sensitive categories are never automated end to end

SYSTEM = """TASK: triage
You triage internal tickets of a telemedicine company. Categories: """ + ", ".join(CATEGORIES) + """.
Priorities: "critical" (patient data at risk, platform down, lost or stolen device), "high" (blocks a patient or
many users today), "normal", "low". Personal data is replaced by placeholders. Return JSON:
{"category": str, "priority": str, "confidence": number 0-1, "rationale": "one short sentence"}"""


@dataclass
class TriageResult:
    ticket: str
    category: str
    team: str
    priority: str
    confidence: float
    rationale: str
    reply_draft: str
    sources: list[dict] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


def triage_ticket(key: str, summary: str, description: str, llm: LLM, index: KnowledgeIndex,
                  top_k: int, min_score: float, threshold: float) -> TriageResult:
    clean = redact(f"{summary}\n\n{description}")
    result = llm.chat_json(SYSTEM, clean.text[:6000])
    category = result.get("category") if result.get("category") in CATEGORIES else "other"
    priority = result.get("priority") if result.get("priority") in {"critical", "high", "normal", "low"} else "normal"
    confidence = float(result.get("confidence", 0))
    answer = answer_question(f"{summary}. {description}", index, llm, top_k, min_score)
    reasons = []
    if category in ALWAYS_HUMAN:
        reasons.append(f"sensitive category: {category}")
    if priority == "critical":
        reasons.append("critical priority")
    if confidence < threshold:
        reasons.append(f"low confidence ({confidence:.2f})")
    triaged = TriageResult(key, category, CATEGORIES[category], priority, round(confidence, 2),
                           result.get("rationale", ""), answer.answer, answer.sources, bool(reasons), reasons)
    PIPELINE_RESULTS.labels("triage", "review" if triaged.needs_review else "auto").inc()
    return triaged
