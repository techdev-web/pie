"""Phase 6: legal findings, geo findings, risk snapshots.

Revision ID: 007_phase6
Revises: 006_phase5
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_phase6"
down_revision: Union[str, None] = "006_phase5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DISCLAIMER = (
    "Risk score is a weighted checklist of unresolved diligence signals, "
    "not a legal conclusion about title."
)


def upgrade() -> None:
    op.create_table(
        "legal_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("finding_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="HIGH"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="UNRESOLVED"),
        sa.Column("layer", sa.String(32), nullable=False, server_default="extract"),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("related_fact_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("related_conflict_id", sa.String(64), nullable=True),
        sa.Column("related_gap_id", sa.String(64), nullable=True),
        sa.Column("missing_evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("finding_id", name="uq_legal_findings_finding_id"),
    )
    op.create_index("ix_legal_findings_case_id", "legal_findings", ["case_id"])
    op.create_index("ix_legal_findings_status", "legal_findings", ["case_id", "status"])
    op.create_index("ix_legal_findings_category", "legal_findings", ["case_id", "category"])

    op.create_table(
        "geo_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("finding_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parcel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parcels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finding_type", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("geometry_valid", sa.Boolean(), nullable=True),
        sa.Column("identity_match", sa.String(32), nullable=True),
        sa.Column("boundary_consistent", sa.String(32), nullable=True),
        sa.Column("related_fact_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("finding_id", name="uq_geo_findings_finding_id"),
    )
    op.create_index("ix_geo_findings_case_id", "geo_findings", ["case_id"])
    op.create_index("ix_geo_findings_type", "geo_findings", ["case_id", "finding_type"])
    op.create_index("ix_geo_findings_parcel_id", "geo_findings", ["parcel_id"])

    op.create_table(
        "risk_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_fingerprint", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weights_version", sa.String(32), nullable=False),
        sa.Column("drivers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("ownership_timeline", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("confidence_profiles", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("disclaimer", sa.Text(), nullable=False, server_default=_DISCLAIMER),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_risk_snapshots_case_id", "risk_snapshots", ["case_id"])
    op.create_index(
        "ix_risk_snapshots_case_created", "risk_snapshots", ["case_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_risk_snapshots_case_created", table_name="risk_snapshots")
    op.drop_index("ix_risk_snapshots_case_id", table_name="risk_snapshots")
    op.drop_table("risk_snapshots")
    op.drop_index("ix_geo_findings_parcel_id", table_name="geo_findings")
    op.drop_index("ix_geo_findings_type", table_name="geo_findings")
    op.drop_index("ix_geo_findings_case_id", table_name="geo_findings")
    op.drop_table("geo_findings")
    op.drop_index("ix_legal_findings_category", table_name="legal_findings")
    op.drop_index("ix_legal_findings_status", table_name="legal_findings")
    op.drop_index("ix_legal_findings_case_id", table_name="legal_findings")
    op.drop_table("legal_findings")
