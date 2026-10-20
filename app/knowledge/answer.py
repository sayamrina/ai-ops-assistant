"""Grounded question answering: answers only from retrieved internal documents and always cites them."""
from dataclasses import dataclass, field

from ..llm import LLM
from ..observability import PIPELINE_RESULTS
from ..privacy import redact
from .index import KnowledgeIndex

SYSTEM = """TASK: answer
You are the internal assistant of YourDoctor Health. Answer the employee's question using ONLY the numbered
excerpts from internal documents. Rules:
- Employees ask informally ("Can I ...?", "What do I answer?"). If an excerpt covers the situation, answer it,
  including yes/no answers such as "No, SMS codes are not allowed."
- If no excerpt covers the question, set "found" to false and leave "answer" empty. Never guess or use outside knowledge.
- Keep the answer short and practical (max. 4 sentences). Use the wording of the policy for numbers and deadlines.
- "citations" lists the excerpt numbers you used.
Return JSON: {"found": bool, "answer": str, "citations": [int]}"""


@dataclass
class Answer:
    found: bool
    answer: str
    sources: list[dict] = field(default_factory=list)
    redacted_fields: dict[str, int] = field(default_factory=dict)


def answer_question(question: str, index: KnowledgeIndex, llm: LLM, top_k: int, min_score: float) -> Answer:
    clean = redact(question)
    hits = [(c, s) for c, s in index.search(clean.text, llm, top_k) if s >= min_score]
    if not hits:
        PIPELINE_RESULTS.labels("ask", "no_context").inc()
        return Answer(False, "", [], clean.counts)
    context = "\n\n".join(f"[{c.id}] {c.title} – {c.section}\n{c.text}" for c, _ in hits)
    result = llm.chat_json(SYSTEM, f"Excerpts:\n{context}\n\nQuestion: {clean.text}")
    allowed = {c.id: (c, s) for c, s in hits}
    cited = [i for i in result.get("citations", []) if i in allowed]  # drop citations the model invented
    found = bool(result.get("found")) and bool(result.get("answer")) and bool(cited)
    PIPELINE_RESULTS.labels("ask", "answered" if found else "not_found").inc()
    sources = [
        {"id": i, "source": allowed[i][0].source, "title": allowed[i][0].title, "section": allowed[i][0].section,
         "owner": allowed[i][0].owner, "score": round(allowed[i][1], 3)}
        for i in cited
    ]
    return Answer(found, result.get("answer", "") if found else "", sources if found else [], clean.counts)
