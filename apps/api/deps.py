"""Auth dependency and request middleware helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.domain.auth import hash_api_key
from packages.domain.db import get_session
from packages.domain.models import ApiKey, Tenant
from packages.observability import new_request_id, request_id_ctx

# Canonical partner scopes (Phase 7). "*" grants all.
ALL_SCOPES = {
    "cases:read",
    "cases:write",
    "documents:write",
    "jobs:read",
    "intelligence:read",
    "chat",
    "review",
    "analyze",
    "reports:read",
    "reports:export",
}


@dataclass
class AuthContext:
    tenant_id: uuid.UUID
    tenant: Tenant
    api_key_id: uuid.UUID
    scopes: list[str] = field(default_factory=lambda: ["*"])

    def has_scope(self, scope: str) -> bool:
        if "*" in self.scopes:
            return True
        return scope in self.scopes


async def bind_request_id(request: Request) -> str:
    rid = request.headers.get("X-Request-ID") or new_request_id()
    request_id_ctx.set(rid)
    return rid


async def require_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
    _rid: str = Depends(bind_request_id),
) -> AuthContext:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")
    key_hash = hash_api_key(x_api_key)
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
        .options(selectinload(ApiKey.tenant))
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if api_key.expires_at is not None and api_key.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API key expired")
    api_key.last_used_at = datetime.now(timezone.utc)
    scopes = list(api_key.scopes or ["*"])
    return AuthContext(
        tenant_id=api_key.tenant_id,
        tenant=api_key.tenant,
        api_key_id=api_key.id,
        scopes=scopes,
    )


def require_scopes(*needed: str) -> Callable:
    """FastAPI dependency factory: require one or more scopes (OR within call = all required)."""

    async def _dep(auth: AuthContext = Depends(require_auth)) -> AuthContext:
        missing = [s for s in needed if not auth.has_scope(s)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"API key missing required scope(s): {', '.join(missing)}",
            )
        return auth

    return _dep
