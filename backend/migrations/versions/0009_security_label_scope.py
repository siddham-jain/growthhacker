"""outlier labels written through the api belong to a workspace; offline label packs carry none

Revision ID: 0009_security
Revises: 0008_integration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_security"
down_revision: str | None = "0008_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("label", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_label_workspace_id_workspaces"),
        "label",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_label_workspace_id"), "label", ["workspace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_label_workspace_id"), table_name="label")
    op.drop_constraint(op.f("fk_label_workspace_id_workspaces"), "label", type_="foreignkey")
    op.drop_column("label", "workspace_id")
