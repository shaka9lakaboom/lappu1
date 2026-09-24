from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, BaseModel

from app.core.config import AppEnvironment, Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Mirrors packages/contracts/schemas/health-response.schema.json."""

    status: Literal["ok"]
    service: str
    environment: AppEnvironment
    version: str
    timestamp: AwareDatetime


@router.get("/health", response_model=HealthResponse)
def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        version=settings.api_version,
        timestamp=datetime.now(UTC),
    )
