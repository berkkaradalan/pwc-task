from enum import StrEnum

from pydantic import BaseModel


class HealthStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class HealthResponse(BaseModel):
    status: HealthStatus
