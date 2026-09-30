"""Seed a development tenant and API key."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from packages.config import get_settings
from packages.domain.auth import generate_api_key, hash_api_key, key_prefix
from packages.domain.db import get_session_factory
from packages.domain.models import ApiKey, Tenant
from packages.observability import configure_logging, get_logger

log = get_logger("seed")


async def seed() -> str:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == "dev"))
        ).scalar_one_or_none()
        if existing is None:
            tenant = Tenant(id=uuid.uuid4(), name="Dev Tenant", slug="dev")
            session.add(tenant)
            await session.flush()
        else:
            tenant = existing

        raw_key = settings.pie_api_key or generate_api_key()
        key_h = hash_api_key(raw_key)
        api_key = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == key_h))
        ).scalar_one_or_none()
        if api_key is None:
            # deactivate prior keys for clean seed? keep them; add this one
            session.add(
                ApiKey(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    name="dev",
                    key_hash=key_h,
                    key_prefix=key_prefix(raw_key),
                    is_active=True,
                    scopes=["*"],
                )
            )
        await session.commit()
        log.info("seeded", tenant_id=str(tenant.id), api_key_prefix=key_prefix(raw_key))
        print(f"Tenant: {tenant.slug} ({tenant.id})")
        print(f"API Key (X-API-Key): {raw_key}")
        return raw_key


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
