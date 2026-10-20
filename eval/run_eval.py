"""Offline evaluation of the three pipelines against the labelled sets in data/eval.

Usage: OPS_LLM_PROVIDER=openai python -m eval.run_eval   (writes results/eval.json and results/eval.md)
"""
import json
import time

from app.config import ROOT, get_settings
from app.extraction import process_document
from app.knowledge.answer import answer_question
from app.llm import build_llm
from app.services import build_index
from app.triage import triage_ticket

EVAL = ROOT / "data" / "eval"


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (EVAL / name).read_text().splitlines() if line.strip()]


def same(expected, got) -> bool:
    if isinstance(expected, float):
        return isinstance(got, int | float) and abs(expected - got) < 0.01
    return expected == got


def main() -> None:
    s = get_settings()
    llm = build_llm(s)
    index = build_index(s, llm)
    report: dict = {"provider": s.llm_provider, "chat_model": s.chat_model, "embedding_model": s.embedding_model}

    # 1. Knowledge search
    qa = load("qa.jsonl")
    rows, start = [], time.perf_counter()
    for item in qa:
        hits = index.search(item["q"], llm, s.top_k)
        ans = answer_question(item["q"], index, llm, s.top_k, s.min_retrieval_score)
        expected = item["source"] if isinstance(item["source"], list) or item["source"] is None else [item["source"]]
        rows.append({"q": item["q"], "expected": expected, "top1": hits[0][0].source,
                     "top3": [c.source for c, _ in hits[:3]], "found": ans.found,
                     "cited": sorted({x["source"] for x in ans.sources}), "answer": ans.answer})
    answerable = [r for r in rows if r["expected"]]
    unanswerable = [r for r in rows if not r["expected"]]
    report["knowledge"] = {
        "questions": len(rows),
        "retrieval_hit_at_1": sum(r["top1"] in r["expected"] for r in answerable) / len(answerable),
        "retrieval_hit_at_3": sum(bool(set(r["expected"]) & set(r["top3"])) for r in answerable) / len(answerable),
        "answered_with_correct_source": sum(r["found"] and bool(set(r["expected"]) & set(r["cited"]))
                                            for r in answerable) / len(answerable),
        "correct_refusals": sum(not r["found"] for r in unanswerable) / len(unanswerable),
        "seconds_per_question": round((time.perf_counter() - start) / len(rows), 2),
        "rows": rows,
    }

    # 2. Document intake
    labels = load("extraction_labels.jsonl")
    docs, field_total, field_ok = [], 0, 0
    for item in labels:
        text = (ROOT / "data" / "inbox" / item["file"]).read_text()
        res = process_document(item["file"], text, llm, s.review_confidence_threshold)
        checks = {k: same(v, res.fields.get(k)) for k, v in item["fields"].items()}
        field_total += len(checks)
        field_ok += sum(checks.values())
        docs.append({"file": item["file"], "expected_type": item["doc_type"], "type": res.doc_type,
                     "fields_ok": checks, "needs_review": res.needs_review, "reasons": res.review_reasons,
                     "redacted": res.redacted_fields})
    report["documents"] = {
        "documents": len(docs),
        "type_accuracy": sum(d["type"] == d["expected_type"] for d in docs) / len(docs),
        "field_accuracy": field_ok / field_total,
        "sent_to_review": sum(d["needs_review"] for d in docs),
        "rows": docs,
    }

    # 3. Ticket triage
    tickets = load("tickets.jsonl")
    tri = []
    for t in tickets:
        r = triage_ticket(t["key"], t["summary"], t["description"], llm, index, s.top_k, s.min_retrieval_score,
                          s.review_confidence_threshold)
        tri.append({"key": t["key"], "expected": [t["category"], t["priority"]], "got": [r.category, r.priority],
                    "needs_review": r.needs_review, "reply_has_sources": bool(r.sources)})
    report["triage"] = {
        "tickets": len(tri),
        "category_accuracy": sum(x["expected"][0] == x["got"][0] for x in tri) / len(tri),
        "priority_accuracy": sum(x["expected"][1] == x["got"][1] for x in tri) / len(tri),
        "sensitive_tickets_reviewed": all(x["needs_review"] for x in tri if x["expected"][0] in
                                          {"security_incident", "data_protection"}),
        "rows": tri,
    }

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "eval.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    k, d, t = report["knowledge"], report["documents"], report["triage"]
    pct = lambda x: f"{x:.0%}"  # noqa: E731
    md = [
        f"# Evaluation ({report['provider']}: {report['chat_model']}, {report['embedding_model']})", "",
        "| Pipeline | Metric | Result |", "|---|---|---|",
        f"| Knowledge search | Right document in top 1 / top 3 ({len(answerable)} questions) | "
        f"{pct(k['retrieval_hit_at_1'])} / {pct(k['retrieval_hit_at_3'])} |",
        f"| Knowledge search | Answered and cited the right document | {pct(k['answered_with_correct_source'])} |",
        f"| Knowledge search | Refused questions outside the documents ({len(unanswerable)}) | {pct(k['correct_refusals'])} |",
        f"| Document intake | Document type correct ({d['documents']} documents) | {pct(d['type_accuracy'])} |",
        f"| Document intake | Extracted fields correct ({field_total} fields) | {pct(d['field_accuracy'])} |",
        f"| Document intake | Sent to human review | {d['sent_to_review']} of {d['documents']} |",
        f"| Ticket triage | Category / priority correct ({t['tickets']} tickets) | "
        f"{pct(t['category_accuracy'])} / {pct(t['priority_accuracy'])} |",
        f"| Ticket triage | All sensitive tickets routed to a human | {'yes' if t['sensitive_tickets_reviewed'] else 'no'} |",
    ]
    (out / "eval.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
