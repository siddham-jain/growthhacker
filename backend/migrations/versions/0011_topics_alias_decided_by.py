"""marks topic aliases a reviewer confirmed or rejected, so rebuilds keep the decision for every origin

Revision ID: 0011_topics
Revises: 0010_youtube
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_topics"
down_revision: str | None = "0010_youtube"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("topic_aliases", sa.Column("decided_by", sa.Text(), nullable=True))
    op.execute(
        "UPDATE topic_aliases SET decided_by = 'user' "
        "WHERE rejected_reason = 'user' OR (origin = 'llm_proposed' AND status = 'confirmed')"
    )


def downgrade() -> None:
    op.drop_column("topic_aliases", "decided_by")
