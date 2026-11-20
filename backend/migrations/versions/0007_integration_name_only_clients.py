"""name-only onboarding (no website or channel required) and crawl acquisitions stored beside the crawl run

Revision ID: 0007_integration
Revises: 0006_youtube
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_integration"
down_revision: str | None = "0006_youtube"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_clients_has_website_or_channel"), "clients", type_="check")
    op.add_column("crawl_runs", sa.Column("acquisition_gz", postgresql.BYTEA(), nullable=True))


def downgrade() -> None:
    op.drop_column("crawl_runs", "acquisition_gz")
    op.create_check_constraint(
        op.f("ck_clients_has_website_or_channel"),
        "clients",
        "website_url IS NOT NULL OR own_channel_id IS NOT NULL",
    )
