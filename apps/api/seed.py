"""Seed a development tenant, user, and API key."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from packages.config import get_settings
from packages.domain.auth import generate_api_key, hash_api_key, key_prefix
from packages.domain.db import get_session_factory
from packages.domain.models import ApiKey, Tenant, User
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

        user = (
            await session.execute(
                select(User).where(User.tenant_id == tenant.id, User.email == "dev@local")
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email="dev@local",
                display_name="Dev User",
            )
            session.add(user)
            await session.flush()

        raw_key = settings.pie_api_key or generate_api_key()
        key_h = hash_api_key(raw_key)
        api_key = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == key_h))
        ).scalar_one_or_none()
        if api_key is None:
            session.add(
                ApiKey(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=user.id,
                    name="dev",
                    key_hash=key_h,
                    key_prefix=key_prefix(raw_key),
                    is_active=True,
                    scopes=["*"],
                )
            )
        else:
            if api_key.user_id is None:
                api_key.user_id = user.id
        await session.commit()
        log.info(
            "seeded",
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            api_key_prefix=key_prefix(raw_key),
        )
        print(f"Tenant: {tenant.slug} ({tenant.id})")
        print(f"User: {user.email} ({user.id})")
        print(f"API Key (X-API-Key): {raw_key}")
        return raw_key


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
