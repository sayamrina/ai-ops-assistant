"""Wires settings, LLM, index, storage and connectors together once per process."""
from dataclasses import dataclass

from fastapi import Request

from .config import Settings
from .connectors.notify import JiraClient, TeamsNotifier
from .connectors.sources import LocalFolderSource
from .knowledge.index import KnowledgeIndex
from .llm import LLM, build_llm
from .store import Store


@dataclass
class Services:
    settings: Settings
    llm: LLM
    index: KnowledgeIndex
    store: Store
    teams: TeamsNotifier
    jira: JiraClient


def index_folder(settings: Settings):
    return settings.data_dir / "index" / settings.llm_provider


def build_index(settings: Settings, llm: LLM) -> KnowledgeIndex:
    folder = settings.data_dir / "knowledge"
    files = [folder / name for name, _ in LocalFolderSource(folder).fetch()]
    index = KnowledgeIndex.build(files, llm)
    index.save(index_folder(settings))
    return index


def build_services(settings: Settings) -> Services:
    llm = build_llm(settings)
    saved = index_folder(settings)
    index = KnowledgeIndex.load(saved) if (saved / "chunks.json").exists() else build_index(settings, llm)
    secret = lambda s: s.get_secret_value() if s else None  # noqa: E731
    return Services(
        settings=settings,
        llm=llm,
        index=index,
        store=Store(settings.db_path),
        teams=TeamsNotifier(secret(settings.teams_webhook_url)),
        jira=JiraClient(settings.jira_base_url, settings.jira_email, secret(settings.jira_api_token)),
    )


def get_services(request: Request) -> Services:
    return request.app.state.services
