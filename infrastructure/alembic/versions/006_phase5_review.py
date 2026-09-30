"""Phase 5: review tasks and decisions.

Revision ID: 006_phase5
Revises: 005_phase4
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_phase5"
down_revision: Union[str, None] = "005_phase4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="HIGH"),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_ref_id", sa.String(128), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("related_fact_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("related_conflict_id", sa.String(64), nullable=True),
        sa.Column("related_gap_id", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", name="uq_review_tasks_task_id"),
        sa.UniqueConstraint("case_id", "fingerprint", name="uq_review_task_fingerprint"),
    )
    op.create_index("ix_review_tasks_case_id", "review_tasks", ["case_id"])
    op.create_index("ix_review_tasks_status", "review_tasks", ["case_id", "status"])
    op.create_index("ix_review_tasks_severity", "review_tasks", ["case_id", "severity"])
    op.create_index("ix_review_tasks_type", "review_tasks", ["task_type"])
    op.create_index("ix_review_tasks_tenant_status", "review_tasks", ["tenant_id", "status"])

    op.create_table(
        "review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column(
            "review_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False, server_default="user"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("selected_fact_id", sa.String(64), nullable=True),
        sa.Column("prior_states", postgresql.JSONB(), nullable=True),
        sa.Column("effects", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("decision_id", name="uq_review_decisions_decision_id"),
    )
    op.create_index("ix_review_decisions_task_id", "review_decisions", ["review_task_id"])
    op.create_index("ix_review_decisions_case_id", "review_decisions", ["case_id"])

    op.add_column(
        "memory_writebacks",
        sa.Column(
            "review_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_memory_writebacks_review_decision_id",
        "memory_writebacks",
        ["review_decision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_writebacks_review_decision_id", table_name="memory_writebacks")
    op.drop_column("memory_writebacks", "review_decision_id")
    op.drop_index("ix_review_decisions_case_id", table_name="review_decisions")
    op.drop_index("ix_review_decisions_task_id", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index("ix_review_tasks_tenant_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_type", table_name="review_tasks")
    op.drop_index("ix_review_tasks_severity", table_name="review_tasks")
    op.drop_index("ix_review_tasks_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_case_id", table_name="review_tasks")
    op.drop_table("review_tasks")
