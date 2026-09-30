"""Users table + API key / case member linkage.

Revision ID: 011_users
Revises: 010_pgvector
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_users"
down_revision: Union[str, None] = "010_pgvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.add_column(
        "api_keys",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_keys_user_id",
        "api_keys",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    op.add_column(
        "case_members",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_case_members_user_id",
        "case_members",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_case_members_user_id", "case_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_case_members_user_id", table_name="case_members")
    op.drop_constraint("fk_case_members_user_id", "case_members", type_="foreignkey")
    op.drop_column("case_members", "user_id")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_user_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "user_id")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
