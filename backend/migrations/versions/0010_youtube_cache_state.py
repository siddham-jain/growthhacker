"""cache state for empty localizations and snapshot retry backoff

Revision ID: 0010_youtube
Revises: 0009_security
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_youtube"
down_revision: str | None = "0009_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "yt_videos", sa.Column("localizations_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "yt_snapshot_schedule",
        sa.Column("failed_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("yt_snapshot_schedule", "failed_attempts")
    op.drop_column("yt_videos", "localizations_fetched_at")
