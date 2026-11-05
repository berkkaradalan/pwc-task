"""Request-scoped access to the objects built once at startup (see main.lifespan)."""

from typing import Annotated

from fastapi import Depends, Request

from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.ports import VectorStore


def get_answer_service(request: Request) -> AnswerService:
    answer_service: AnswerService = request.app.state.answer_service
    return answer_service


def get_vector_store(request: Request) -> VectorStore:
    store: VectorStore = request.app.state.vector_store
    return store


AnswerServiceDependency = Annotated[AnswerService, Depends(get_answer_service)]
VectorStoreDependency = Annotated[VectorStore, Depends(get_vector_store)]
