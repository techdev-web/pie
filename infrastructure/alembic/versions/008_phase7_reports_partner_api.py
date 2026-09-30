"""Phase 7: report artifacts + API key expansions.

Revision ID: 008_phase7
Revises: 007_phase6
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_phase7"
down_revision: Union[str, None] = "007_phase6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default='["*"]',
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("key_metadata", postgresql.JSONB(), nullable=True),
    )

    op.create_table(
        "report_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_type", sa.String(64), nullable=False, server_default="dd_memo"),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("format", sa.String(32), nullable=False, server_default="json"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("truth_fingerprint", sa.String(64), nullable=False),
        sa.Column("case_fingerprint", sa.String(64), nullable=True),
        sa.Column("generator_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("section_fingerprints", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("reused_sections", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rebuild_reason", sa.String(128), nullable=True),
        sa.Column("storage_uri", sa.String(1024), nullable=True),
        sa.Column("pdf_storage_uri", sa.String(1024), nullable=True),
        sa.Column("triggered_by", sa.String(128), nullable=True),
        sa.Column("parent_report_id", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", name="uq_report_artifacts_report_id"),
    )
    op.create_index("ix_report_artifacts_case_id", "report_artifacts", ["case_id"])
    op.create_index(
        "ix_report_artifacts_case_created",
        "report_artifacts",
        ["case_id", "created_at"],
    )
    op.create_index(
        "ix_report_artifacts_tenant_status",
        "report_artifacts",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_report_artifacts_truth_fp",
        "report_artifacts",
        ["case_id", "truth_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_artifacts_truth_fp", table_name="report_artifacts")
    op.drop_index("ix_report_artifacts_tenant_status", table_name="report_artifacts")
    op.drop_index("ix_report_artifacts_case_created", table_name="report_artifacts")
    op.drop_index("ix_report_artifacts_case_id", table_name="report_artifacts")
    op.drop_table("report_artifacts")
    op.drop_column("api_keys", "key_metadata")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("api_keys", "scopes")
