import logging

import psycopg
from fastapi import APIRouter, HTTPException, status

from oncall_rag.query.core.dependencies import PassageSearchDependency
from oncall_rag.query.schemas.health import HealthResponse, HealthStatus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(passage_search: PassageSearchDependency) -> HealthResponse:
    """Healthy when the database answers. The language model is not called."""
    try:
        await passage_search.ping()
    except psycopg.OperationalError as error:
        logger.warning("Health check failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=HealthStatus.FAILED
        ) from error
    return HealthResponse(status=HealthStatus.OK)
