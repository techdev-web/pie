"""Auth dependency and request middleware helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.domain.auth import hash_api_key
from packages.domain.db import get_session
from packages.domain.models import ApiKey, Tenant
from packages.observability import new_request_id, request_id_ctx


@dataclass
class AuthContext:
    tenant_id: uuid.UUID
    tenant: Tenant
    api_key_id: uuid.UUID


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
    return AuthContext(
        tenant_id=api_key.tenant_id,
        tenant=api_key.tenant,
        api_key_id=api_key.id,
    )
