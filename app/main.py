"""FastAPI application: internal AI assistant, document intake, ticket triage and human review."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import get_settings
from .observability import RequestContextMiddleware, setup_logging
from .routers import api, review, webhooks
from .services import Services, build_services, get_services

STATIC = Path(__file__).parent / "static"


def create_app(services: Services | None = None) -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services or build_services(get_settings())
        yield

    app = FastAPI(title="Internal AI Ops Assistant", version="1.0.0", lifespan=lifespan,
                  description="Knowledge search, document intake and ticket triage with human review.")
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api.router)
    app.include_router(webhooks.router)
    app.include_router(review.router)

    @app.get("/health", tags=["operations"])
    def health():
        return {"status": "ok"}

    @app.get("/ready", tags=["operations"])
    def ready(svc: Services = Depends(get_services)):
        return {"status": "ready", "chunks": len(svc.index.chunks), "db": svc.store.ping(),
                "llm_provider": svc.settings.llm_provider}

    @app.get("/metrics", tags=["operations"], include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
