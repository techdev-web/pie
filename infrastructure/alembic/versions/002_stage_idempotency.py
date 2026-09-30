"""Allow stage retries: partial unique only on succeeded runs.

Revision ID: 002_stage_idempotency
Revises: 001_phase0_phase1
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_stage_idempotency"
down_revision: Union[str, None] = "001_phase0_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_stage_idempotency", "processing_stage_runs", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_stage_idempotency_succeeded
        ON processing_stage_runs (
            document_hash, stage_name, stage_version, model_version, prompt_version
        )
        WHERE status = 'succeeded'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_stage_idempotency_succeeded")
    op.create_unique_constraint(
        "uq_stage_idempotency",
        "processing_stage_runs",
        [
            "document_hash",
            "stage_name",
            "stage_version",
            "model_version",
            "prompt_version",
        ],
    )
