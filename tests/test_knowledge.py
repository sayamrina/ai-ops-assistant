from app.config import ROOT
from app.knowledge.answer import answer_question
from app.knowledge.index import split_markdown


def test_chunks_follow_sections():
    chunks = split_markdown(ROOT / "data" / "knowledge" / "it-access-and-mfa.md", 0)
    assert [c.section for c in chunks] == ["Requesting access", "Multi-factor authentication",
                                           "Lost or new phone", "Leaving the company"]
    assert all(c.owner == "IT Operations" for c in chunks)


def test_search_finds_the_right_policy(index, llm):
    top = index.search("I lost my phone and need an MFA reset", llm, top_k=3)
    assert top[0][0].source == "it-access-and-mfa.md"


def test_answers_are_cited(index, llm):
    result = answer_question("What do I do if my laptop was stolen?", index, llm, top_k=4, min_score=0.1)
    assert result.found and result.sources
    assert all(s["source"].endswith(".md") for s in result.sources)


def test_refuses_without_context(index, llm):
    llm.responses["answer"] = {"found": False, "answer": "", "citations": []}
    result = answer_question("Who won the football match yesterday?", index, llm, top_k=4, min_score=0.3)
    assert not result.found and result.answer == "" and result.sources == []


def test_invented_citations_are_dropped(index, llm):
    llm.responses["answer"] = {"found": True, "answer": "Something", "citations": [999]}
    result = answer_question("How long must a password be?", index, llm, top_k=4, min_score=0.1)
    assert not result.found
