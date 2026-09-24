"""PostgreSQL connection pool (psycopg 3).

All queries use server-side parameters; SQL text is never built from input.
Prepared statements are disabled so the pool also works through Supabase's
transaction pooler (PgBouncer).
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from psycopg_pool import ConnectionPool

from app.core.config import Settings, get_settings


def create_pool(database_url: str, *, max_size: int = 5) -> ConnectionPool:
    return ConnectionPool(
        conninfo=database_url,
        min_size=0,
        max_size=max_size,
        kwargs={"prepare_threshold": None, "application_name": "skillmirror-backend"},
        open=True,
        timeout=10,
    )


@lru_cache
def _pool_for(database_url: str) -> ConnectionPool:
    return create_pool(database_url)


def get_db_pool(settings: Annotated[Settings, Depends(get_settings)]) -> ConnectionPool:
    if settings.database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is not configured: DATABASE_URL is not set.",
        )
    return _pool_for(settings.database_url.get_secret_value())


DbPool = Annotated[ConnectionPool, Depends(get_db_pool)]
