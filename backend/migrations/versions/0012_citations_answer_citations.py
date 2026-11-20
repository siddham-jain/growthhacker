"""web answer citations per buyer question: one check row per search, one row per cited youtube video

Revision ID: 0012_citations
Revises: 0011_topics
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_citations"
down_revision: str | None = "0011_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns(table: str) -> list[Any]:
    return [
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f(f"fk_{table}_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "answer_citation_checks",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("buyer_question_id", sa.UUID(), nullable=False),
        sa.Column("checked_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("result_count", sa.SmallInteger(), nullable=False),
        sa.Column("youtube_url_count", sa.SmallInteger(), nullable=False),
        sa.Column("unresolved_count", sa.SmallInteger(), nullable=False),
        sa.Column("answer_returned", sa.Boolean(), nullable=False),
        sa.Column("credits", sa.Numeric(12, 6), nullable=False),
        sa.Column("from_cache", sa.Boolean(), nullable=False),
        *_tenant_columns("answer_citation_checks"),
        sa.ForeignKeyConstraint(
            ["buyer_question_id"],
            ["buyer_questions.id"],
            name=op.f("fk_answer_citation_checks_buyer_question_id_buyer_questions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_citation_checks")),
    )
    op.create_index(
        "ix_answer_citation_checks_client_id_checked_at",
        "answer_citation_checks",
        ["client_id", "checked_at"],
    )
    op.create_index(
        "ix_answer_citation_checks_buyer_question_id_checked_at",
        "answer_citation_checks",
        ["buyer_question_id", "checked_at"],
    )
    op.create_table(
        "answer_citations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("check_id", sa.UUID(), nullable=False),
        sa.Column("buyer_question_id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("cited_url", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("cited_in", sa.Text(), nullable=False),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        *_tenant_columns("answer_citations"),
        sa.CheckConstraint("cited_in IN ('result', 'answer')", name=op.f("ck_answer_citations_cited_in")),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["answer_citation_checks.id"],
            name=op.f("fk_answer_citations_check_id_answer_citation_checks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_question_id"],
            ["buyer_questions.id"],
            name=op.f("fk_answer_citations_buyer_question_id_buyer_questions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_citations")),
        sa.UniqueConstraint("check_id", "video_id", name=op.f("uq_answer_citations_check_id_video_id")),
    )
    op.create_index(op.f("ix_answer_citations_check_id"), "answer_citations", ["check_id"])
    op.create_index(
        "ix_answer_citations_client_id_observed_at", "answer_citations", ["client_id", "observed_at"]
    )


def downgrade() -> None:
    op.drop_table("answer_citations")
    op.drop_table("answer_citation_checks")
