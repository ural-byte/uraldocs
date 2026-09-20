import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.chat import answer_question, generate_answer
from app.config import Settings
from app.kb import config_signature
from app.models import Base, Conversation, Document, DocumentChunk, Message, MessageSource, User
from app.security import hash_password


@pytest.fixture
def postgres_chat_database():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Для проверки поиска PostgreSQL задайте TEST_POSTGRES_URL")
    schema = f"uraldocs_chat_{uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema},public"})
    try:
        Base.metadata.create_all(engine, checkfirst=False)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def add_document(db, config, filename, text, embedding=None, *, stale=False):
    document = Document(
        filename=filename, file_type="txt", original=text.encode(), status="ready", generation=1,
        config_signature="stale" if stale else config_signature(config),
    )
    db.add(document)
    db.flush()
    db.add(DocumentChunk(
        document_id=document.id, generation=1, chunk_index=0, text=text,
        line_start=1, line_end=1, embedding=embedding,
    ))


def test_postgres_demo_fts_and_current_index(postgres_chat_database, embeddings_server):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    with postgres_chat_database() as db:
        user = User(username="reader", role="user", password_hash=hash_password("reader-password-123"), is_active=True)
        db.add(user)
        db.flush()
        conversation = Conversation(owner_id=user.id, title="Поиск")
        db.add(conversation)
        db.flush()
        add_document(db, config, "current.txt", "alpha mountain")
        add_document(db, config, "stale.txt", "alpha valley", stale=True)
        db.commit()
        owner_id, conversation_id = user.id, conversation.id

    answer_question(postgres_chat_database, owner_id, conversation_id, "alpha", config)
    answer_question(postgres_chat_database, owner_id, conversation_id, "gamma", config)
    with postgres_chat_database() as db:
        answers = db.scalars(select(Message).where(Message.role == "assistant").order_by(Message.id)).all()
        assert [message.kind for message in answers] == ["demo", "insufficient"]
        assert [source.filename for source in db.scalars(select(MessageSource))] == ["current.txt"]
    assert embeddings_server.requests == []


def test_postgres_demo_natural_questions_require_all_meaningful_terms(postgres_chat_database, embeddings_server):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    with postgres_chat_database() as db:
        user = User(username="reader", role="user", password_hash=hash_password("reader-password-123"), is_active=True)
        db.add(user)
        db.flush()
        conversation = Conversation(owner_id=user.id, title="Естественные вопросы")
        db.add(conversation)
        db.flush()
        add_document(db, config, "ru.txt", "Уралдокс поддерживает импорт PDF")
        add_document(db, config, "en.txt", "UralDocs supports PDF import")
        db.commit()
        owner_id, conversation_id = user.id, conversation.id

    questions = [
        "Поддерживает ли Уралдокс импорт PDF?",
        "Поддерживает ли Уралдокс импорт DOCX?",
        "Does UralDocs support PDF import?",
        "Does UralDocs support DOCX import?",
        "Поддерживает ли Уралдокс шифрование PDF?",
    ]
    for question in questions:
        answer_question(postgres_chat_database, owner_id, conversation_id, question, config)

    with postgres_chat_database() as db:
        answers = db.scalars(select(Message).where(Message.role == "assistant").order_by(Message.id)).all()
        assert [message.kind for message in answers] == ["demo", "insufficient", "demo", "insufficient", "insufficient"]
        sources = [
            [source.filename for source in db.scalars(select(MessageSource).where(MessageSource.message_id == message.id))]
            for message in answers
        ]
        assert sources == [["ru.txt"], [], ["en.txt"], [], []]
    assert embeddings_server.requests == []


def test_postgres_demo_search_uses_question_language_with_manual_answer_language(postgres_chat_database):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    with postgres_chat_database.begin() as db:
        add_document(db, config, "ru.txt", "Уралдокс поддерживает импорт PDF")
        add_document(db, config, "en.txt", "UralDocs supports PDF import")

    english_question = generate_answer(
        postgres_chat_database, "Does UralDocs support PDF import?", [], config, language="ru",
    )
    russian_question = generate_answer(
        postgres_chat_database, "Поддерживает ли Уралдокс импорт PDF?", [], config, language="en",
    )
    assert english_question.kind == "demo" and english_question.text.startswith("Демо-режим")
    assert [source.filename for source in english_question.sources] == ["en.txt"]
    assert russian_question.kind == "demo" and russian_question.text.startswith("Demo mode")
    assert [source.filename for source in russian_question.sources] == ["ru.txt"]


def test_postgres_pgvector_filters_dimension_and_similarity(postgres_chat_database, embeddings_server):
    config = Settings(
        database_url="sqlite+pysqlite://", kb_mode="real_ai", ai_base_url=embeddings_server.base_url,
        ai_api_key="private-test-key", ai_embedding_model="test-embedding", ai_chat_model="test-chat",
        chat_min_similarity=0.7,
    )
    with postgres_chat_database() as db:
        user = User(username="reader", role="user", password_hash=hash_password("reader-password-123"), is_active=True)
        db.add(user)
        db.flush()
        conversation = Conversation(owner_id=user.id, title="Векторный поиск")
        db.add(conversation)
        db.flush()
        add_document(db, config, "current.txt", "alpha mountain", [1.0, 0.0])
        add_document(db, config, "distant.txt", "alpha sea", [0.0, 1.0])
        add_document(db, config, "stale.txt", "alpha valley", [1.0, 0.0], stale=True)
        add_document(db, config, "wrong-dimension.txt", "alpha forest", [1.0, 0.0, 0.0])
        db.commit()
        owner_id, conversation_id = user.id, conversation.id

    def provider(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": json.dumps({
            "insufficient": False, "answer": "Маршрут по горе.", "citation_ids": ["c1"],
        })}}]}

    embeddings_server.payload = provider
    answer_question(postgres_chat_database, owner_id, conversation_id, "alpha", config)
    with postgres_chat_database() as db:
        answer = db.scalar(select(Message).where(Message.role == "assistant"))
        sources = db.scalars(select(MessageSource).where(MessageSource.message_id == answer.id)).all()
        assert answer.kind == "answer"
        assert [source.filename for source in sources] == ["current.txt"]
        db.delete(db.get(Conversation, conversation_id))
        db.commit()
        assert db.scalars(select(Message)).all() == []
        assert db.scalars(select(MessageSource)).all() == []
    chat_requests = [request for request in embeddings_server.requests if request[0].endswith("/chat/completions")]
    assert len(chat_requests) == 1
    context = json.loads(chat_requests[0][2]["messages"][1]["content"])
    assert context["sources"] == [{"id": "c1", "excerpt": "alpha mountain"}]
