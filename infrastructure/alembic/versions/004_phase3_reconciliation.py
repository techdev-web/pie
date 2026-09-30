"""Phase 3: conflicts, missing evidence, graphs, completeness.

Revision ID: 004_phase3
Revises: 003_phase2
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_phase3"
down_revision: Union[str, None] = "003_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conflict_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conflict_type", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="HIGH"),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("reconciliation_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conflicts_case_id", "conflicts", ["case_id"])
    op.create_index("ix_conflicts_status", "conflicts", ["case_id", "status"])
    op.create_index("ix_conflicts_type", "conflicts", ["conflict_type"])

    op.create_table(
        "conflict_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conflict_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conflicts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("conflict_id", "fact_id", name="uq_conflict_fact"),
    )
    op.create_index("ix_conflict_facts_conflict_id", "conflict_facts", ["conflict_id"])
    op.create_index("ix_conflict_facts_fact_id", "conflict_facts", ["fact_id"])

    op.create_table(
        "missing_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("gap_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gap_type", sa.String(128), nullable=False),
        sa.Column(
            "referenced_from_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("referenced_label", sa.String(512), nullable=False),
        sa.Column("required_doc_type", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="NOT_PROVIDED"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("reconciliation_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_missing_evidence_case_id", "missing_evidence", ["case_id"])
    op.create_index("ix_missing_evidence_status", "missing_evidence", ["case_id", "status"])

    op.create_table(
        "graph_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("graph_type", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("label", sa.String(512), nullable=False),
        sa.Column("ref_table", sa.String(64), nullable=True),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("properties", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "graph_type", "node_id", name="uq_graph_node"),
    )
    op.create_index("ix_graph_nodes_case_id", "graph_nodes", ["case_id"])
    op.create_index("ix_graph_nodes_graph_type", "graph_nodes", ["case_id", "graph_type"])

    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("edge_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("graph_type", sa.String(64), nullable=False),
        sa.Column("from_node_id", sa.String(128), nullable=False),
        sa.Column("to_node_id", sa.String(128), nullable=False),
        sa.Column("edge_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("properties", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "graph_type", "edge_id", name="uq_graph_edge"),
    )
    op.create_index("ix_graph_edges_case_id", "graph_edges", ["case_id"])
    op.create_index("ix_graph_edges_from", "graph_edges", ["case_id", "from_node_id"])
    op.create_index("ix_graph_edges_to", "graph_edges", ["case_id", "to_node_id"])

    op.create_table(
        "case_completeness_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_fingerprint", sa.String(64), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("open_conflicts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reconciliation_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_completeness_case_id", "case_completeness_snapshots", ["case_id"])
    op.create_index(
        "ix_completeness_case_created",
        "case_completeness_snapshots",
        ["case_id", "created_at"],
    )

    op.create_table(
        "case_stage_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_fingerprint", sa.String(64), nullable=False),
        sa.Column("stage_name", sa.String(64), nullable=False),
        sa.Column("stage_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_case_stage_runs_case_id", "case_stage_runs", ["case_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX uq_case_stage_idempotency_succeeded
        ON case_stage_runs (case_id, case_fingerprint, stage_name, stage_version)
        WHERE status = 'succeeded'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_case_stage_idempotency_succeeded")
    op.drop_table("case_stage_runs")
    op.drop_table("case_completeness_snapshots")
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
    op.drop_table("missing_evidence")
    op.drop_table("conflict_facts")
    op.drop_table("conflicts")
