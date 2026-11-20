"""oversized run artifacts are stored gzipped and content-addressed; run_artifacts rows point at them

Revision ID: 0008_integration
Revises: 0007_integration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_integration"
down_revision: str | None = "0007_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_artifact_blobs",
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("payload_gz", postgresql.BYTEA(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_run_artifact_blobs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "sha256", name=op.f("pk_run_artifact_blobs")),
    )
    op.create_index(op.f("ix_run_artifact_blobs_expires_at"), "run_artifact_blobs", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_run_artifact_blobs_expires_at"), table_name="run_artifact_blobs")
    op.drop_table("run_artifact_blobs")
