"""HTTP API of the question-answering service: `uvicorn oncall_rag.query.main:app`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from oncall_rag.query.routers import ask, health
from oncall_rag.query.wiring import answer_service_and_search
from oncall_rag.shared.config import QuerySettings
from oncall_rag.shared.logging import configure_logging


def create_app() -> FastAPI:
    app = FastAPI(title="On-call RAG", lifespan=lifespan)
    app.include_router(router=ask.router)
    app.include_router(router=health.router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Refuses to start on a missing index or one built with another model or dimension."""
    configure_logging()
    settings = QuerySettings()  # type: ignore[call-arg]  # values come from the environment
    async with answer_service_and_search(settings) as (answer_service, passage_search):
        await passage_search.ensure_compatible(settings.embedding_model)
        app.state.answer_service = answer_service
        app.state.passage_search = passage_search
        yield


app = create_app()
