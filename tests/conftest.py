import json

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.connectors.notify import JiraClient, TeamsNotifier
from app.knowledge.index import KnowledgeIndex
from app.llm import FakeLLM
from app.main import create_app
from app.security import hash_key
from app.services import Services
from app.store import Store

KEYS = {"viewer": "key-viewer", "operator": "key-operator", "reviewer": "key-reviewer", "admin": "key-admin"}
WEBHOOK_SECRET = "test-secret"


class ScriptedLLM(FakeLLM):
    """FakeLLM whose chat answers can be overridden per task in a test."""

    def __init__(self):
        self.responses: dict[str, dict] = {}

    def chat_json(self, system: str, user: str) -> dict:
        for task, response in self.responses.items():
            if f"TASK: {task}" in system:
                return dict(response)
        return super().chat_json(system, user)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        llm_provider="fake", data_dir=ROOT / "data", db_path=tmp_path / "test.sqlite3",
        api_keys=json.dumps({hash_key(k): {"name": f"{role}-user", "role": role} for role, k in KEYS.items()}),
        webhook_secret=WEBHOOK_SECRET,
    )


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def index(llm):
    folder = ROOT / "data" / "knowledge"
    return KnowledgeIndex.build(sorted(folder.glob("*.md")), llm)


@pytest.fixture
def services(settings, llm, index):
    return Services(settings, llm, index, Store(settings.db_path), TeamsNotifier(None), JiraClient(None, None, None))


@pytest.fixture
def client(services, settings):
    from app.config import get_settings

    app = create_app(services)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


def auth(role: str) -> dict:
    return {"X-API-Key": KEYS[role]}
