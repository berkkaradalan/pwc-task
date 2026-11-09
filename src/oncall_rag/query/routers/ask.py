import logging

import openai
import psycopg
from fastapi import APIRouter, HTTPException, status

from oncall_rag.query.core.dependencies import AnswerServiceDependency
from oncall_rag.query.schemas.ask import AskRequest, AskResponse
from oncall_rag.shared.errors import ProviderResponseError
from oncall_rag.shared.logging import failure_summary

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ask")
async def ask(request: AskRequest, answer_service: AnswerServiceDependency) -> AskResponse:
    try:
        return await answer_service.answer(request.question, request.history)
    # TimeoutError: the request deadline in AnswerService expired.
    except (openai.APITimeoutError, TimeoutError) as error:
        logger.warning("Answering failed: %s", failure_summary(error))
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "A required service did not answer in time."
        ) from error
    # On OpenRouter a 404 means no provider can serve the model, e.g. the pinned one is down.
    except (
        openai.APIConnectionError,
        openai.NotFoundError,
        openai.RateLimitError,
        openai.InternalServerError,
        psycopg.OperationalError,
    ) as error:
        logger.warning("Answering failed: %s", failure_summary(error))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A required service is unavailable. Try again later.",
        ) from error
    except (openai.APIStatusError, ProviderResponseError) as error:
        logger.warning("Answering failed: %s", failure_summary(error))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "A required service rejected the request or returned an unusable answer.",
        ) from error
