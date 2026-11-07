"""The question-answering API: `uvicorn oncall_rag.query.main:app --no-access-log`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from oncall_rag.query.core.middleware import configure_api_logging, install_request_tracing
from oncall_rag.query.core.wiring import answer_service_and_search
from oncall_rag.query.routers import ask, health
from oncall_rag.shared.config import QuerySettings


def create_app() -> FastAPI:
    app = FastAPI(title="On-call RAG", lifespan=lifespan)
    app.include_router(router=ask.router)
    app.include_router(router=health.router)
    install_request_tracing(app)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Refuses to start on a missing index or one built with another model or dimension."""
    configure_api_logging()
    settings = QuerySettings()  # type: ignore[call-arg]  # values come from the environment
    async with answer_service_and_search(settings) as (answer_service, passage_search):
        await passage_search.ensure_compatible(settings.embedding_model)
        app.state.answer_service = answer_service
        app.state.passage_search = passage_search
        yield


app = create_app()
