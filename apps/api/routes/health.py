from fastapi import APIRouter

from packages.config import get_settings
from packages.observability import get_logger

router = APIRouter(tags=["health"])
log = get_logger("health")


@router.get("/health")
async def health() -> dict:
    """Liveness + readiness for DB and Redis."""
    checks: dict[str, str] = {"api": "ok"}
    status = "ok"

    # DB
    try:
        from sqlalchemy import text

        from packages.domain.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error:{type(exc).__name__}"
        status = "degraded"
        log.warning("health_db_failed", error=str(exc))

    # Redis
    try:
        from redis.asyncio import from_url

        settings = get_settings()
        client = from_url(settings.redis_url)
        try:
            pong = await client.ping()
            checks["redis"] = "ok" if pong else "error:no_pong"
            if not pong:
                status = "degraded"
        finally:
            await client.aclose()
    except Exception as exc:
        checks["redis"] = f"error:{type(exc).__name__}"
        status = "degraded"
        log.warning("health_redis_failed", error=str(exc))

    return {"status": status, "service": "pie-api", "checks": checks}
