import logging

import openai
import psycopg
from fastapi import APIRouter, HTTPException, status

from oncall_rag.query.dependencies import AnswerServiceDependency
from oncall_rag.query.schemas.ask import AskRequest, AskResponse
from oncall_rag.shared.errors import ProviderResponseError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ask")
async def ask(request: AskRequest, answer_service: AnswerServiceDependency) -> AskResponse:
    try:
        return await answer_service.answer(request.question)
    except openai.APITimeoutError as error:
        logger.warning("Answering failed: %s", error)
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "A required service did not answer in time."
        ) from error
    except (
        openai.APIConnectionError,
        openai.RateLimitError,
        openai.InternalServerError,
        psycopg.OperationalError,
    ) as error:
        logger.warning("Answering failed: %s", error)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A required service is unavailable. Try again later.",
        ) from error
    except (openai.APIStatusError, ProviderResponseError) as error:
        logger.warning("Answering failed: %s", error)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "A required service rejected the request or returned an unusable answer.",
        ) from error
