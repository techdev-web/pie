"""pgvector embeddings (768-d) replacing JSONB vectors.

Revision ID: 010_pgvector
Revises: 009_phase8
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_pgvector"
down_revision: Union[str, None] = "009_phase8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBED_DIMS = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Prior mock embeddings were 8-d JSONB — wipe and recreate as vector(768)
    op.execute("DELETE FROM embeddings")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS embedding")
    op.execute(f"ALTER TABLE embeddings ADD COLUMN embedding vector({EMBED_DIMS}) NOT NULL")
    # Case-scoped ANN: filter by case_id in app; HNSW on vector for distance
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_hnsw_cosine "
        "ON embeddings USING hnsw (embedding vector_cosine_ops)"
    )
    op.alter_column(
        "embeddings",
        "dims",
        server_default=sa.text(str(EMBED_DIMS)),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_hnsw_cosine")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE embeddings ADD COLUMN embedding jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("DROP EXTENSION IF EXISTS vector")
