import logging

from fastapi import APIRouter, HTTPException, status

from oncall_rag.query.dependencies import AnswerServiceDependency
from oncall_rag.query.schemas.ask import AskRequest, AskResponse
from oncall_rag.shared.errors import (
    EmbedderUnavailableError,
    EmbeddingError,
    LLMError,
    LLMTimeoutError,
    LLMUnavailableError,
    OncallRagError,
    VectorStoreUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

UNAVAILABLE_ERRORS = (EmbedderUnavailableError, VectorStoreUnavailableError, LLMUnavailableError)
REJECTED_ERRORS = (EmbeddingError, LLMError)

TIMEOUT_DETAIL = "The language model did not answer in time."
UNAVAILABLE_DETAIL = "A service needed to answer is unavailable. Try again later."
REJECTED_DETAIL = "A service needed to answer rejected the request."


@router.post("/ask")
async def ask(request: AskRequest, answer_service: AnswerServiceDependency) -> AskResponse:
    try:
        return await answer_service.answer(request.question)
    except LLMTimeoutError as error:
        raise _logged_http_error(status.HTTP_504_GATEWAY_TIMEOUT, TIMEOUT_DETAIL, error) from error
    except UNAVAILABLE_ERRORS as error:
        raise _logged_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL, error
        ) from error
    except REJECTED_ERRORS as error:
        raise _logged_http_error(status.HTTP_502_BAD_GATEWAY, REJECTED_DETAIL, error) from error


def _logged_http_error(status_code: int, detail: str, error: OncallRagError) -> HTTPException:
    # The exception text may name internal hosts, so it is logged, never sent to the client.
    logger.warning("Answering failed: %s", error)
    return HTTPException(status_code=status_code, detail=detail)
