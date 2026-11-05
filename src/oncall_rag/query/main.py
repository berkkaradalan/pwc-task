"""HTTP API of the question-answering service: `uvicorn oncall_rag.query.main:app`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from oncall_rag.query.routers import ask, health
from oncall_rag.query.wiring import answer_service_and_store
from oncall_rag.shared.config import QuerySettings, load_settings
from oncall_rag.shared.logging import configure_logging


def create_app() -> FastAPI:
    app = FastAPI(title="On-call RAG", lifespan=lifespan)
    app.include_router(ask.router)
    app.include_router(health.router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Refuses to start on an index built with another embedding model or dimension."""
    configure_logging()
    settings = load_settings(QuerySettings)
    async with answer_service_and_store(settings) as (answer_service, store):
        await store.initialize()
        await store.ensure_compatible(settings.embedding_model)
        app.state.answer_service = answer_service
        app.state.vector_store = store
        yield


app = create_app()
