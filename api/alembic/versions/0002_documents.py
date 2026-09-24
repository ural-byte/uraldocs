"""Хранит оригиналы документов и поколения поискового индекса."""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0002_documents"
down_revision = "0001_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("original", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("config_signature", sa.String(64)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_deadline", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'ready', 'failed')", name="ck_documents_status"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("embedding", Vector()),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index"),
        sa.CheckConstraint("(page_number IS NOT NULL AND line_start IS NULL AND line_end IS NULL) OR (page_number IS NULL AND line_start IS NOT NULL AND line_end IS NOT NULL)", name="ck_document_chunks_location"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("uq_document_chunks_order", "document_chunks", ["document_id", "generation", "chunk_index"], unique=True)
    op.execute("CREATE INDEX ix_document_chunks_text_search ON document_chunks USING gin (to_tsvector('simple', text))")


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("documents")
