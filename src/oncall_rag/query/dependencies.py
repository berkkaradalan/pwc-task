"""Request-scoped access to the objects built once at startup (see main.lifespan)."""

from typing import Annotated

from fastapi import Depends, Request

from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.ports import PassageSearch


def get_answer_service(request: Request) -> AnswerService:
    answer_service: AnswerService = request.app.state.answer_service
    return answer_service


def get_passage_search(request: Request) -> PassageSearch:
    passage_search: PassageSearch = request.app.state.passage_search
    return passage_search


AnswerServiceDependency = Annotated[AnswerService, Depends(get_answer_service)]
PassageSearchDependency = Annotated[PassageSearch, Depends(get_passage_search)]
