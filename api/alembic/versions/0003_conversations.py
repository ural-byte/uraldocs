"""Хранит беседы и снимки источников ответов."""

from alembic import op
import sqlalchemy as sa

revision = "0003_conversations"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reply_to_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint("kind IN ('question', 'answer', 'demo', 'insufficient', 'index_unavailable')", name="ck_messages_kind"),
        sa.CheckConstraint(
            "(role = 'user' AND kind = 'question' AND reply_to_id IS NULL) OR "
            "(role = 'assistant' AND kind != 'question' AND reply_to_id IS NOT NULL)",
            name="ck_messages_pair",
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_reply_to_id", "messages", ["reply_to_id"], unique=True)
    op.create_table(
        "message_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("excerpt", sa.Text()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(page_number IS NOT NULL AND line_start IS NULL AND line_end IS NULL) OR "
            "(page_number IS NULL AND line_start IS NOT NULL AND line_end IS NOT NULL)",
            name="ck_message_sources_location",
        ),
    )
    op.create_index("ix_message_sources_message_id", "message_sources", ["message_id"])
    op.create_index("ix_message_sources_document_id", "message_sources", ["document_id"])


def downgrade() -> None:
    op.drop_table("message_sources")
    op.drop_table("messages")
    op.drop_table("conversations")
