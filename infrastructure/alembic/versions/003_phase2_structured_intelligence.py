"""Phase 2: facts, entities, parcels, ownership events.

Revision ID: 003_phase2
Revises: 002_stage_idempotency
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_phase2"
down_revision: Union[str, None] = "002_stage_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("normalized_name", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_persons_case_id", "persons", ["case_id"])
    op.create_index("ix_persons_normalized_name", "persons", ["case_id", "normalized_name"])

    op.create_table(
        "person_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(512), nullable=False),
        sa.Column("normalized_alias", sa.String(512), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="extraction"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_person_aliases_person_id", "person_aliases", ["person_id"])

    op.create_table(
        "person_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_type", sa.String(64), nullable=False),
        sa.Column("id_value", sa.String(255), nullable=False),
        sa.Column("normalized_value", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("person_id", "id_type", "normalized_value", name="uq_person_identifier"),
    )
    op.create_index("ix_person_identifiers_person_id", "person_identifiers", ["person_id"])

    op.create_table(
        "parcels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_label", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_parcels_case_id", "parcels", ["case_id"])

    op.create_table(
        "parcel_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "parcel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parcels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_type", sa.String(64), nullable=False),
        sa.Column("id_value", sa.String(255), nullable=False),
        sa.Column("normalized_value", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("parcel_id", "id_type", "normalized_value", name="uq_parcel_identifier"),
    )
    op.create_index("ix_parcel_identifiers_parcel_id", "parcel_identifiers", ["parcel_id"])
    op.create_index("ix_parcel_identifiers_normalized", "parcel_identifiers", ["normalized_value"])

    op.create_table(
        "ownership_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parcel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parcels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_date_raw", sa.String(128), nullable=True),
        sa.Column("registration_number", sa.String(255), nullable=True),
        sa.Column("consideration_amount", sa.Float(), nullable=True),
        sa.Column("consideration_currency", sa.String(16), nullable=True),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="EXTRACTED"),
        sa.Column("model_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ownership_events_case_id", "ownership_events", ["case_id"])
    op.create_index("ix_ownership_events_document_id", "ownership_events", ["document_id"])

    op.create_table(
        "ownership_event_parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ownership_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ownership_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("ownership_event_id", "person_id", "role", name="uq_event_party_role"),
    )
    op.create_index(
        "ix_ownership_event_parties_event_id", "ownership_event_parties", ["ownership_event_id"]
    )

    op.create_table(
        "ownership_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ownership_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ownership_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("share_numerator", sa.Integer(), nullable=True),
        sa.Column("share_denominator", sa.Integer(), nullable=True),
        sa.Column("share_text", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ownership_shares_event_id", "ownership_shares", ["ownership_event_id"])

    op.create_table(
        "facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fact_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fact_type", sa.String(128), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("predicate", sa.String(128), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_normalized", sa.Text(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="EXTRACTED"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extraction_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_facts_case_id", "facts", ["case_id"])
    op.create_index("ix_facts_document_id", "facts", ["document_id"])
    op.create_index("ix_facts_fact_type", "facts", ["fact_type"])
    op.create_index("ix_facts_verification_state", "facts", ["verification_state"])

    op.create_table(
        "fact_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("link_role", sa.String(32), nullable=False, server_default="supports"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("fact_id", "evidence_item_id", name="uq_fact_evidence"),
    )
    op.create_index("ix_fact_evidence_fact_id", "fact_evidence", ["fact_id"])
    op.create_index("ix_fact_evidence_evidence_item_id", "fact_evidence", ["evidence_item_id"])


def downgrade() -> None:
    op.drop_table("fact_evidence")
    op.drop_table("facts")
    op.drop_table("ownership_shares")
    op.drop_table("ownership_event_parties")
    op.drop_table("ownership_events")
    op.drop_table("parcel_identifiers")
    op.drop_table("parcels")
    op.drop_table("person_identifiers")
    op.drop_table("person_aliases")
    op.drop_table("persons")
