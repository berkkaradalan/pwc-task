import logging

from fastapi import APIRouter, HTTPException, status

from oncall_rag.query.dependencies import VectorStoreDependency
from oncall_rag.query.schemas.health import HealthResponse, HealthStatus
from oncall_rag.shared.errors import VectorStoreUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(store: VectorStoreDependency) -> HealthResponse:
    """Healthy when the database answers. The language model is not called."""
    try:
        await store.ping()
    except VectorStoreUnavailableError as error:
        logger.warning("Health check failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=HealthStatus.FAILED
        ) from error
    return HealthResponse(status=HealthStatus.OK)
