from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api.v1.router import api_v1_router, HealthResponse
from app.observability import logger

app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    description="SkillMirror FastAPI Backend Service Foundation (P0)",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API v1 router
app.include_router(api_v1_router, prefix="/api")


# Top-level GET /health endpoint as required by Section 7 & Section 9
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def get_health():
    """
    Primary health endpoint for backend service verification.
    """
    logger.info("Health endpoint called")
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        version=settings.api_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
