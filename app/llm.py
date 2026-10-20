"""LLM access behind one small interface.

OpenAI and Ollama both speak the OpenAI API, so one client covers cloud and self-hosted models.
FakeLLM is deterministic and needs no network: it is used in tests, CI and the offline demo.
"""
import hashlib
import json
import logging
import re
import time
from typing import Protocol

import numpy as np

from .config import Settings
from .observability import LLM_CALLS, LLM_LATENCY

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def chat_json(self, system: str, user: str) -> dict: ...
    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings):
        from openai import OpenAI

        if settings.llm_provider == "ollama":
            client = OpenAI(base_url=settings.ollama_base_url, api_key="ollama", timeout=settings.llm_timeout_s)
        else:
            if settings.openai_api_key is None:
                raise LLMError("OPS_OPENAI_API_KEY is not set")
            client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.llm_timeout_s)
        self.client = client
        self.chat_model = settings.chat_model
        self.embedding_model = settings.embedding_model
        self.max_retries = settings.llm_max_retries

    def _with_retries(self, kind: str, fn):
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            start = time.perf_counter()
            try:
                result = fn()
                LLM_CALLS.labels(kind, "ok").inc()
                return result
            except Exception as exc:  # network errors, rate limits, timeouts
                LLM_CALLS.labels(kind, "error").inc()
                log.warning("llm call failed", extra={"extra_fields": {"kind": kind, "attempt": attempt,
                                                                       "error": type(exc).__name__}})
                if attempt == self.max_retries:
                    raise LLMError(f"{kind} failed after {attempt} attempts") from exc
                time.sleep(delay)
                delay *= 2
            finally:
                LLM_LATENCY.labels(kind).observe(time.perf_counter() - start)

    def chat_json(self, system: str, user: str) -> dict:
        def call():
            response = self.client.chat.completions.create(
                model=self.chat_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return json.loads(response.choices[0].message.content)

        return self._with_retries("chat", call)

    def embed(self, texts: list[str]) -> np.ndarray:
        def call():
            response = self.client.embeddings.create(model=self.embedding_model, input=texts)
            return np.array([item.embedding for item in response.data], dtype=np.float32)

        return _normalise(self._with_retries("embed", call))


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


TOKEN = re.compile(r"[a-zäöüß0-9]+", re.I)


class FakeLLM:
    """Offline stand-in: hashed bag-of-words embeddings and rule-based JSON answers."""

    dim = 512

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            words = [w.lower() for w in TOKEN.findall(text)]
            for gram in words + [a + "_" + b for a, b in zip(words, words[1:], strict=False)]:
                h = int(hashlib.blake2b(gram.encode(), digest_size=8).hexdigest(), 16)
                out[i, h % self.dim] += 1.0 if h & 1 else -1.0
        return _normalise(out)

    def chat_json(self, system: str, user: str) -> dict:
        task = re.search(r"TASK: (\w+)", system)
        task = task.group(1) if task else ""
        text = user.lower()
        if task == "answer":
            first = re.search(r"\[(\d+)\][^\n]*\n(.+)", user)
            if not first:
                return {"found": False, "answer": "", "citations": []}
            return {"found": True, "answer": first.group(2).strip()[:300], "citations": [int(first.group(1))]}
        if task == "classify_document":
            for doc_type, words in [
                ("invoice", ["invoice", "rechnung", "receipt"]),
                ("data_subject_request", ["gdpr", "art. 15", "article 15", "erasure", "delete my", "art. 16"]),
                ("doctor_application", ["approbation", "facharzt", "application as"]),
                ("support_request", ["case ct-", "refund", "prescription", "upload"]),
            ]:
                if any(w in text for w in words):
                    return {"doc_type": doc_type, "confidence": 0.9}
            return {"doc_type": "other", "confidence": 0.6}
        if task == "triage":
            category = "other"
            rules = [("security_incident", ["stolen", "phishing", "breach"]), ("it_access", ["mfa", "access", "password"]),
                     ("data_protection", ["gdpr", "delete", "personal data"]), ("billing", ["refund", "invoice"]),
                     ("platform_bug", ["error", "fails", "failed", "broken", "bug"])]
            for cat, words in rules:
                if any(w in text for w in words):
                    category = cat
                    break
            priority = "critical" if category == "security_incident" else "high" if "urgent" in text else "normal"
            return {"category": category, "priority": priority, "confidence": 0.85, "rationale": "keyword rules (offline mode)"}
        return {}


def build_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "fake":
        return FakeLLM()
    return OpenAICompatibleLLM(settings)
