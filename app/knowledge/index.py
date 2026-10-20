"""Knowledge index: section-wise chunking, embeddings and hybrid retrieval (vector + BM25 keywords).

The index is small, so it lives in memory and on disk as two files. For larger collections the same
interface can sit on top of pgvector or Qdrant.
"""
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..llm import LLM

WORD = re.compile(r"[a-zäöüß0-9]+", re.I)
STOP = set("a an and are as at be by can do does for from how i in is it my of on or the to what when which who with".split())


@dataclass
class Chunk:
    id: int
    source: str
    title: str
    section: str
    owner: str
    text: str


def _tokens(text: str) -> list[str]:
    return [w for w in (t.lower() for t in WORD.findall(text)) if w not in STOP]


def split_markdown(path: Path, start_id: int) -> list[Chunk]:
    """One chunk per '##' section, each prefixed with the document title so it stands on its own."""
    raw = path.read_text(encoding="utf-8")
    title = raw.splitlines()[0].lstrip("# ").strip()
    owner = (re.search(r"Owner: ([^·\n]+)", raw) or [None, ""])[1].strip()
    parts = re.split(r"(?m)^## ", raw)
    intro = "\n".join(line for line in parts[0].splitlines()[1:] if not line.startswith("Owner:")).strip()
    sections = ([("Overview", intro)] if intro else []) + [
        (p.splitlines()[0].strip(), "\n".join(p.splitlines()[1:]).strip()) for p in parts[1:]
    ]
    return [
        Chunk(id=start_id + i, source=path.name, title=title, section=name, owner=owner, text=body)
        for i, (name, body) in enumerate(sections) if body
    ]


class KnowledgeIndex:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray):
        self.chunks = chunks
        self.vectors = vectors
        self._doc_tokens = [_tokens(f"{c.title} {c.section} {c.text}") for c in chunks]
        self._df = Counter(t for toks in self._doc_tokens for t in set(toks))
        self._avg_len = sum(map(len, self._doc_tokens)) / max(len(chunks), 1)

    @classmethod
    def build(cls, documents: list[Path], llm: LLM) -> "KnowledgeIndex":
        chunks: list[Chunk] = []
        for path in sorted(documents):
            chunks += split_markdown(path, len(chunks))
        vectors = llm.embed([f"{c.title} – {c.section}\n{c.text}" for c in chunks])
        return cls(chunks, vectors)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        np.save(folder / "vectors.npy", self.vectors)
        (folder / "chunks.json").write_text(json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, folder: Path) -> "KnowledgeIndex":
        chunks = [Chunk(**c) for c in json.loads((folder / "chunks.json").read_text())]
        return cls(chunks, np.load(folder / "vectors.npy"))

    def _bm25(self, query: str, k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        n = len(self.chunks)
        scores = np.zeros(n, dtype=np.float32)
        for term in set(_tokens(query)):
            df = self._df.get(term)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, toks in enumerate(self._doc_tokens):
                tf = toks.count(term)
                if tf:
                    scores[i] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(toks) / self._avg_len))
        return scores

    def search(self, query: str, llm: LLM, top_k: int = 4) -> list[tuple[Chunk, float]]:
        """Hybrid score: 0.75 cosine similarity + 0.25 normalised BM25, so exact terms like 'MFA' count."""
        cosine = self.vectors @ llm.embed([query])[0]
        bm25 = self._bm25(query)
        if bm25.max() > 0:
            bm25 = bm25 / bm25.max()
        score = 0.75 * cosine + 0.25 * bm25
        best = np.argsort(-score)[:top_k]
        return [(self.chunks[i], float(score[i])) for i in best]
