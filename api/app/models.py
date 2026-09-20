from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, func, literal_column
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="sessions")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (CheckConstraint("status IN ('pending', 'ready', 'failed')", name="ck_documents_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    original: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_signature: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan", passive_deletes=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index"),
        CheckConstraint("(page_number IS NOT NULL AND line_start IS NULL AND line_end IS NULL) OR (page_number IS NULL AND line_start IS NOT NULL AND line_end IS NOT NULL)", name="ck_document_chunks_location"),
        Index("uq_document_chunks_order", "document_id", "generation", "chunk_index", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector().with_variant(JSON(), "sqlite"))
    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        CheckConstraint("kind IN ('question', 'answer', 'demo', 'insufficient', 'index_unavailable')", name="ck_messages_kind"),
        CheckConstraint(
            "(role = 'user' AND kind = 'question' AND reply_to_id IS NULL) OR "
            "(role = 'assistant' AND kind != 'question' AND reply_to_id IS NOT NULL)",
            name="ck_messages_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True, unique=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    reply_to: Mapped["Message | None"] = relationship(remote_side="Message.id")
    sources: Mapped[list["MessageSource"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class MessageSource(Base):
    __tablename__ = "message_sources"
    __table_args__ = (CheckConstraint("(page_number IS NOT NULL AND line_start IS NULL AND line_end IS NULL) OR (page_number IS NULL AND line_start IS NOT NULL AND line_end IS NOT NULL)", name="ck_message_sources_location"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    citation_id: Mapped[str | None] = mapped_column(String(10))
    excerpt: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[Message] = relationship(back_populates="sources")


class TelegramCursor(Base):
    __tablename__ = "telegram_cursor"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_telegram_cursor_singleton"),
        CheckConstraint("next_update_id >= 0", name="ck_telegram_cursor_offset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class TelegramHistory(Base):
    __tablename__ = "telegram_history"
    __table_args__ = (
        CheckConstraint("telegram_id > 0", name="ck_telegram_history_user"),
        CheckConstraint("update_id >= 0", name="ck_telegram_history_update"),
    )

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramLanguagePreference(Base):
    __tablename__ = "telegram_language_preferences"
    __table_args__ = (
        CheckConstraint("telegram_id > 0", name="ck_telegram_language_preferences_user"),
        CheckConstraint("language IN ('ru', 'en')", name="ck_telegram_language_preferences_language"),
    )

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False)


DocumentChunk.__table__.append_constraint(
    Index(
        "ix_document_chunks_text_search",
        func.to_tsvector(literal_column("'simple'"), DocumentChunk.text),
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql")
)

for language, suffix in (("russian", "ru"), ("english", "en")):
    DocumentChunk.__table__.append_constraint(
        Index(
            f"ix_document_chunks_text_search_{suffix}",
            func.to_tsvector(literal_column(f"'{language}'"), DocumentChunk.text),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql")
    )
