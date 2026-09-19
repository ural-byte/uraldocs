import json

import pytest
from sqlalchemy import select

from app import chat, main as api_main
from app.config import Settings
from app.kb import config_signature
from app.models import Conversation, Document, DocumentChunk, Message, MessageSource

ORIGIN = {"origin": "http://localhost:3000"}


def login(client, username="admin", password="admin-secret-123"):
    assert client.post("/auth/login", json={"username": username, "password": password}, headers=ORIGIN).status_code == 200


def create_conversation(client, title="Вопросы"):
    response = client.post("/conversations", json={"title": title}, headers=ORIGIN)
    assert response.status_code == 201
    return response.json()["id"]


def ready_document(database, config, text="alpha mountain", vector=None):
    if config.kb_mode == "real_ai" and vector is None:
        vector = [1.0, 0.0]
    with database() as db:
        document = Document(
            filename="guide.txt", file_type="txt", original=text.encode(), status="ready", generation=1,
            config_signature=config_signature(config),
        )
        db.add(document)
        db.flush()
        chunk = DocumentChunk(
            document_id=document.id, generation=1, chunk_index=0, text=text,
            line_start=4, line_end=4, embedding=vector,
        )
        db.add(chunk)
        db.commit()
        return document.id


def real_config(server):
    return Settings(
        database_url="sqlite+pysqlite://", kb_mode="real_ai", ai_base_url=server.base_url,
        ai_api_key="private-test-key", ai_embedding_model="test-embedding", ai_chat_model="test-chat",
    )


def successful_provider(server):
    def answer(_request):
        path = server.requests[-1][0]
        if path.endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": json.dumps({
            "insufficient": False, "answer": "На горе есть маршрут.", "citation_ids": ["c1"],
        }, ensure_ascii=False)}}]}

    server.payload = answer


def test_shared_answer_service_needs_no_web_user_or_conversation(database, embeddings_server):
    config = real_config(embeddings_server)
    ready_document(database, config)
    successful_provider(embeddings_server)
    history = [chat.HistoryPair(f"вопрос {index}", f"ответ {index}") for index in range(4)]

    result = chat.generate_answer(database, "alpha", history, config)

    assert isinstance(result, chat.AnswerResult)
    assert result.kind == "answer"
    assert result.text == "На горе есть маршрут."
    assert len(result.sources) == 1
    assert result.sources[0].filename == "guide.txt"
    assert result.sources[0].citation_id == "c1"
    payload = json.loads(embeddings_server.requests[-1][2]["messages"][1]["content"])
    assert payload["question"] == "alpha"
    assert payload["history"] == [
        {"question": f"вопрос {index}", "answer": f"ответ {index}"} for index in range(1, 4)
    ]
    assert payload["sources"] == [{"id": "c1", "excerpt": "alpha mountain"}]
    with database() as db:
        assert db.scalars(select(Message)).all() == []
        assert db.scalars(select(Conversation)).all() == []


def test_demo_history_isolation_and_source_deletion(client, database, users):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    document_id = ready_document(database, config)
    login(client)
    first_id = create_conversation(client)
    second_id = create_conversation(client, "Другая беседа")

    response = client.post(f"/conversations/{first_id}/messages", json={"question": "alpha"}, headers=ORIGIN)

    assert response.status_code == 201
    pair = response.json()
    assert pair["user"]["kind"] == "question" and pair["user"]["text"] == "alpha"
    assert pair["assistant"]["kind"] == "demo" and "ИИ-ответ не формируется" in pair["assistant"]["text"]
    source = pair["assistant"]["sources"][0]
    assert source["filename"] == "guide.txt" and source["line_start"] == source["line_end"] == 4
    assert source["excerpt"] == "alpha mountain" and source["anchor"] == f"source-{source['id']}"
    assert client.get(f"/conversations/{second_id}").json()["messages"] == []
    assert len(client.get(f"/conversations/{first_id}").json()["messages"]) == 2

    client.cookies.clear()
    login(client, "reader", "reader-secret-123")
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{first_id}").status_code == 404
    assert client.post(f"/conversations/{first_id}/messages", json={"question": "alpha"}, headers=ORIGIN).status_code == 404
    assert client.delete(f"/conversations/{first_id}", headers=ORIGIN).status_code == 404
    own_id = create_conversation(client, "Беседа пользователя")
    assert client.get(f"/conversations/{own_id}").status_code == 200
    client.cookies.clear()
    login(client)
    assert client.get(f"/conversations/{own_id}").status_code == 404
    assert len(client.get(f"/conversations/{first_id}").json()["messages"]) == 2

    assert client.delete(f"/admin/documents/{document_id}", headers=ORIGIN).status_code == 204
    old_source = client.get(f"/conversations/{first_id}").json()["messages"][1]["sources"][0]
    assert old_source["id"] == source["id"] and old_source["filename"] == source["filename"]
    assert old_source["line_start"] == 4 and old_source["deleted"] and old_source["excerpt"] is None
    assert client.get(f"/conversations/{first_id}").json()["messages"][1]["text"] == pair["assistant"]["text"]

    assert client.delete(f"/conversations/{first_id}", headers=ORIGIN).status_code == 204
    assert client.get(f"/conversations/{first_id}").status_code == 404
    with database() as db:
        assert db.scalars(select(Message).where(Message.conversation_id == first_id)).all() == []
        assert db.scalars(select(MessageSource)).all() == []
        assert db.get(Conversation, second_id) is not None


def test_demo_insufficient_and_index_unavailable(client, database, users, embeddings_server):
    login(client)
    conversation_id = create_conversation(client)
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 201
    assert response.json()["assistant"]["kind"] == "index_unavailable"
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    ready_document(database, config, text="beta valley")
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 201
    assert response.json()["assistant"]["kind"] == "insufficient"
    assert response.json()["assistant"]["sources"] == []
    assert embeddings_server.requests == []


def test_real_ai_validates_citations_and_history(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    ready_document(database, config)
    successful_provider(embeddings_server)
    login(client)
    conversation_id = create_conversation(client)

    for index in range(4):
        response = client.post(
            f"/conversations/{conversation_id}/messages", json={"question": f"alpha {index}"}, headers=ORIGIN,
        )
        assert response.status_code == 201
        assert response.json()["assistant"]["kind"] == "answer"
        assert len(response.json()["assistant"]["sources"]) == 1

    embedding_requests = [request for request in embeddings_server.requests if request[0].endswith("/embeddings")]
    chat_requests = [request for request in embeddings_server.requests if request[0].endswith("/chat/completions")]
    assert [request[2]["input"] for request in embedding_requests] == [[f"alpha {index}"] for index in range(4)]
    assert all(request[1] == "Bearer private-test-key" for request in embeddings_server.requests)
    assert [len(json.loads(request[2]["messages"][1]["content"])["history"]) for request in chat_requests] == [0, 1, 2, 3]
    last_payload = json.loads(chat_requests[-1][2]["messages"][1]["content"])
    assert last_payload["question"] == "alpha 3"
    assert [item["question"] for item in last_payload["history"]] == ["alpha 0", "alpha 1", "alpha 2"]
    assert last_payload["sources"] == [{"id": "c1", "excerpt": "alpha mountain"}]
    assert len(client.get(f"/conversations/{conversation_id}").json()["messages"]) == 8

    second_id = create_conversation(client)
    assert client.post(f"/conversations/{second_id}/messages", json={"question": "alpha"}, headers=ORIGIN).status_code == 201
    second_history = json.loads(embeddings_server.requests[-1][2]["messages"][1]["content"])["history"]
    assert second_history == []


def test_real_ai_citation_marker_keeps_original_context_id(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    ready_document(database, config, text="alpha first")
    ready_document(database, config, text="alpha second")
    login(client)
    conversation_id = create_conversation(client)

    def provider(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": json.dumps({
            "insufficient": False, "answer": "Второй фрагмент [c2].", "citation_ids": ["c2"],
        })}}]}

    embeddings_server.payload = provider
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 201
    answer = response.json()["assistant"]
    assert answer["text"] == "Второй фрагмент [c2]."
    assert len(answer["sources"]) == 1
    source = answer["sources"][0]
    assert source["citation_id"] == "c2"
    assert source["excerpt"] == "alpha second"
    assert source["anchor"] == f"source-{source['id']}"
    history_source = client.get(f"/conversations/{conversation_id}").json()["messages"][1]["sources"][0]
    assert history_source == source
    chat_payload = json.loads(embeddings_server.requests[-1][2]["messages"][1]["content"])
    assert chat_payload["sources"] == [
        {"id": "c1", "excerpt": "alpha first"},
        {"id": "c2", "excerpt": "alpha second"},
    ]


def test_chat_body_limit_accepts_maximum_russian_question(client, database, users, monkeypatch):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo", chat_max_question_chars=10000)
    monkeypatch.setattr(api_main, "settings", config)
    login(client)
    conversation_id = create_conversation(client)
    question = "я" * 10000
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": question}, headers=ORIGIN)
    assert response.status_code == 201
    assert response.json()["user"]["text"] == question
    too_long = client.post(f"/conversations/{conversation_id}/messages", json={"question": question + "я"}, headers=ORIGIN)
    assert too_long.status_code == 422


@pytest.mark.parametrize("reply", [
    {"insufficient": False, "answer": "Неверная ссылка", "citation_ids": ["c99"]},
    {"insufficient": False, "answer": "Без ссылки", "citation_ids": []},
    {"insufficient": False, "answer": "Ссылка [c99]", "citation_ids": ["c1"]},
    "invalid-json",
])
def test_real_ai_rejects_invalid_output_without_pair(client, database, users, embeddings_server, monkeypatch, reply):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    ready_document(database, config)
    login(client)
    conversation_id = create_conversation(client)

    def answer(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {"choices": [{"message": {"content": content}}]}

    embeddings_server.payload = answer
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 502
    assert client.get(f"/conversations/{conversation_id}").json()["messages"] == []


def test_real_ai_insufficient_and_provider_failure(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    ready_document(database, config)
    login(client)
    conversation_id = create_conversation(client)

    def answer(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": json.dumps({"insufficient": True, "citation_ids": []})}}]}

    embeddings_server.payload = answer
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 201
    assert response.json()["assistant"]["kind"] == "insufficient"
    assert response.json()["assistant"]["sources"] == []

    def fail(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        embeddings_server.status = 503
        return {"error": "secret provider error"}

    embeddings_server.payload = fail
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 502
    assert "secret provider error" not in response.text
    assert len(client.get(f"/conversations/{conversation_id}").json()["messages"]) == 2


def test_real_ai_reindex_race_does_not_save_stale_excerpt(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    document_id = ready_document(database, config)
    successful_provider(embeddings_server)
    login(client)
    conversation_id = create_conversation(client)
    original_completion = chat._chat_completion

    def reindex_during_generation(question, history, candidates, settings):
        result = original_completion(question, history, candidates, settings)
        with database() as db:
            document = db.get(Document, document_id)
            document.generation += 1
            document.status = "pending"
            db.commit()
        return result

    monkeypatch.setattr(chat, "_chat_completion", reindex_during_generation)
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 409
    assert client.get(f"/conversations/{conversation_id}").json()["messages"] == []


def test_real_ai_unavailable_index_makes_no_provider_calls(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    login(client)
    conversation_id = create_conversation(client)
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 201
    assert response.json()["assistant"]["kind"] == "index_unavailable"
    assert embeddings_server.requests == []


def test_real_ai_delete_race_does_not_save_stale_excerpt(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    monkeypatch.setattr(api_main, "settings", config)
    document_id = ready_document(database, config)
    successful_provider(embeddings_server)
    login(client)
    conversation_id = create_conversation(client)
    original_completion = chat._chat_completion

    def delete_during_generation(question, history, candidates, settings):
        result = original_completion(question, history, candidates, settings)
        with database() as db:
            db.delete(db.get(Document, document_id))
            db.commit()
        return result

    monkeypatch.setattr(chat, "_chat_completion", delete_during_generation)
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 409
    assert client.get(f"/conversations/{conversation_id}").json()["messages"] == []


def test_real_ai_generation_timeout_keeps_pair_atomic(client, database, users, embeddings_server, monkeypatch):
    config = real_config(embeddings_server)
    config.ai_timeout_seconds = 0.05
    monkeypatch.setattr(api_main, "settings", config)
    ready_document(database, config)
    login(client)
    conversation_id = create_conversation(client)

    def provider(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            embeddings_server.delay = 0.2
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": "{}"}}]}

    embeddings_server.payload = provider
    response = client.post(f"/conversations/{conversation_id}/messages", json={"question": "alpha"}, headers=ORIGIN)
    assert response.status_code == 504
    assert client.get(f"/conversations/{conversation_id}").json()["messages"] == []
