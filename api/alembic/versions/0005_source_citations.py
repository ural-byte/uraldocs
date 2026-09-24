"""Сохраняет метку источника из ответа генератора."""

from alembic import op
import sqlalchemy as sa

revision = "0005_source_citations"
down_revision = "0004_chat_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message_sources", sa.Column("citation_id", sa.String(10)))


def downgrade() -> None:
    op.drop_column("message_sources", "citation_id")
