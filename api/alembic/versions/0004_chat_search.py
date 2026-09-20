"""Добавляет полнотекстовые индексы для русских и английских вопросов."""

from alembic import op

revision = "0004_chat_search"
down_revision = "0003_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_document_chunks_text_search_ru ON document_chunks USING gin (to_tsvector('russian', text))")
    op.execute("CREATE INDEX ix_document_chunks_text_search_en ON document_chunks USING gin (to_tsvector('english', text))")


def downgrade() -> None:
    op.drop_index("ix_document_chunks_text_search_en", table_name="document_chunks")
    op.drop_index("ix_document_chunks_text_search_ru", table_name="document_chunks")
