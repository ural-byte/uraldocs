"""Хранит курсор и ограниченную историю Telegram отдельно от веб-бесед."""

from alembic import op
import sqlalchemy as sa

revision = "0006_telegram"
down_revision = "0005_source_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_cursor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_update_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_telegram_cursor_singleton"),
        sa.CheckConstraint("next_update_id >= 0", name="ck_telegram_cursor_offset"),
    )
    op.execute("INSERT INTO telegram_cursor (id, next_update_id) VALUES (1, 0)")
    op.create_table(
        "telegram_history",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("telegram_id > 0", name="ck_telegram_history_user"),
        sa.CheckConstraint("update_id >= 0", name="ck_telegram_history_update"),
    )


def downgrade() -> None:
    op.drop_table("telegram_history")
    op.drop_table("telegram_cursor")
