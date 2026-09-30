"""Phase 8: eval runs, cost budgets, model_run cost/latency.

Revision ID: 009_phase8
Revises: 008_phase7
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_phase8"
down_revision: Union[str, None] = "008_phase7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_runs",
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
    )
    op.add_column(
        "model_runs",
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "model_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_model_runs_case_id", "model_runs", ["case_id"])
    op.create_index("ix_model_runs_case_created", "model_runs", ["case_id", "created_at"])

    op.create_table(
        "cost_budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("budget_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "alert_threshold_pct",
            sa.Integer(),
            nullable=False,
            server_default="80",
        ),
        sa.Column(
            "spent_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="OK"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "period_start", name="uq_cost_budgets_tenant_period"),
    )
    op.create_index("ix_cost_budgets_tenant_id", "cost_budgets", ["tenant_id"])
    op.create_index(
        "ix_cost_budgets_tenant_status",
        "cost_budgets",
        ["tenant_id", "status"],
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("eval_id", sa.String(64), nullable=False),
        sa.Column("pack_name", sa.String(128), nullable=False),
        sa.Column("processing_version", sa.String(32), nullable=False),
        sa.Column("git_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("eval_id", name="uq_eval_runs_eval_id"),
    )
    op.create_index("ix_eval_runs_pack_name", "eval_runs", ["pack_name"])
    op.create_index("ix_eval_runs_created", "eval_runs", ["created_at"])

    op.create_table(
        "eval_case_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "eval_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fixture_id", sa.String(128), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expected", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("actual", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("diffs", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_eval_case_results_run_id", "eval_case_results", ["eval_run_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_case_results_run_id", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("ix_eval_runs_created", table_name="eval_runs")
    op.drop_index("ix_eval_runs_pack_name", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_index("ix_cost_budgets_tenant_status", table_name="cost_budgets")
    op.drop_index("ix_cost_budgets_tenant_id", table_name="cost_budgets")
    op.drop_table("cost_budgets")
    op.drop_index("ix_model_runs_case_created", table_name="model_runs")
    op.drop_index("ix_model_runs_case_id", table_name="model_runs")
    op.drop_column("model_runs", "retry_count")
    op.drop_column("model_runs", "latency_ms")
    op.drop_column("model_runs", "cost_usd")
