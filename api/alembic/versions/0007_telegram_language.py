"""Хранит явный выбор языка для каждого Telegram ID."""

from alembic import op
import sqlalchemy as sa

revision = "0007_telegram_language"
down_revision = "0006_telegram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_language_preferences",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.CheckConstraint("telegram_id > 0", name="ck_telegram_language_preferences_user"),
        sa.CheckConstraint("language IN ('ru', 'en')", name="ck_telegram_language_preferences_language"),
    )


def downgrade() -> None:
    op.drop_table("telegram_language_preferences")
