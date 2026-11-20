"""owner-authorised per-video period metrics (youtube analytics)

Revision ID: 0006_youtube
Revises: 0005_pipeline
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_youtube"
down_revision: str | None = "0005_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "own_video_metrics",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("oauth_token_id", sa.UUID(), nullable=False),
        sa.Column("creator_content_type", sa.Text(), nullable=True),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("engaged_views", sa.BigInteger(), nullable=True),
        sa.Column("estimated_minutes_watched", sa.BigInteger(), nullable=True),
        sa.Column("average_view_duration_s", sa.REAL(), nullable=True),
        sa.Column("average_view_percentage", sa.REAL(), nullable=True),
        sa.Column("subscribers_gained", sa.Integer(), nullable=True),
        sa.Column("subscribers_lost", sa.Integer(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["oauth_token_id"],
            ["oauth_tokens.id"],
            name=op.f("fk_own_video_metrics_oauth_token_id_oauth_tokens"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_own_video_metrics_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "client_id", "video_id", "period_start", "period_end", name=op.f("pk_own_video_metrics")
        ),
    )
    op.create_index(
        op.f("ix_own_video_metrics_fetched_at"), "own_video_metrics", ["fetched_at"], unique=False
    )
    op.create_index(
        op.f("ix_own_video_metrics_oauth_token_id"), "own_video_metrics", ["oauth_token_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_own_video_metrics_oauth_token_id"), table_name="own_video_metrics")
    op.drop_index(op.f("ix_own_video_metrics_fetched_at"), table_name="own_video_metrics")
    op.drop_table("own_video_metrics")
