from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from app.config import settings

api_v1_router = APIRouter(prefix="/v1")


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    version: str
    timestamp: str


@api_v1_router.get("/health", response_model=HealthResponse, tags=["Health"])
async def get_health_v1():
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        version=settings.api_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
