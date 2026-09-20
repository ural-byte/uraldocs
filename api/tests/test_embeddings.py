import pytest
from sqlalchemy import select

from app import worker
from app.config import Settings
from app.kb import ExtractedChunk, config_signature, extract_chunks
from app.models import Document, DocumentChunk


def real_ai_config(server, timeout=1.0):
    return Settings(
        database_url="sqlite+pysqlite://",
        kb_mode="real_ai",
        ai_base_url=server.base_url,
        ai_api_key="private-test-key",
        ai_embedding_model="test-model",
        ai_timeout_seconds=timeout,
    )


def pending_document(database, original=b"Evidence"):
    with database() as db:
        document = Document(filename="note.txt", file_type="txt", original=original, status="pending", generation=1)
        db.add(document)
        db.commit()
        return document.id


def test_real_ai_sends_chunks_and_stores_vectors_in_source_order(database, embeddings_server):
    original = b"A" * 700 + b"\n" + b"B" * 700
    document_id = pending_document(database, original)
    embeddings_server.payload = {
        "data": [
            {"index": 1, "embedding": [0.3, 0.4]},
            {"index": 0, "embedding": [0.1, 0.2]},
        ]
    }
    config = real_ai_config(embeddings_server)

    assert worker.process_one(database, config)

    assert embeddings_server.requests == [
        (
            "/v1/embeddings",
            "Bearer private-test-key",
            {"model": "test-model", "input": [chunk.text for chunk in extract_chunks(original, "txt")]},
        )
    ]
    with database() as db:
        document = db.get(Document, document_id)
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id).order_by(DocumentChunk.chunk_index)).all()
        assert document.status == "ready" and document.config_signature == config_signature(config)
        assert [chunk.embedding for chunk in chunks] == [[0.1, 0.2], [0.3, 0.4]]


def test_real_ai_splits_requests_at_batch_boundary(embeddings_server):
    chunks = [ExtractedChunk(f"fragment-{index}") for index in range(worker.EMBEDDINGS_BATCH_SIZE + 1)]

    def answer(request):
        return {
            "data": [
                {"index": index, "embedding": [float(text.rsplit("-", 1)[1])]}
                for index, text in reversed(list(enumerate(request["input"])))
            ]
        }

    embeddings_server.payload = answer

    vectors = worker.create_embeddings(chunks, real_ai_config(embeddings_server))

    assert vectors == [[float(index)] for index in range(len(chunks))]
    assert [len(request[2]["input"]) for request in embeddings_server.requests] == [worker.EMBEDDINGS_BATCH_SIZE, 1]
    assert [request[2]["input"][0] for request in embeddings_server.requests] == ["fragment-0", f"fragment-{worker.EMBEDDINGS_BATCH_SIZE}"]


def test_real_ai_rejects_dimension_change_between_batches(embeddings_server, monkeypatch):
    monkeypatch.setattr(worker, "EMBEDDINGS_BATCH_SIZE", 2)
    chunks = [ExtractedChunk(f"fragment-{index}") for index in range(3)]

    def answer(request):
        dimension = 1 if len(embeddings_server.requests) == 1 else 2
        return {"data": [{"index": index, "embedding": [0.1] * dimension} for index, _text in enumerate(request["input"])]}

    embeddings_server.payload = answer

    with pytest.raises(ValueError, match="Размерности embeddings различаются"):
        worker.create_embeddings(chunks, real_ai_config(embeddings_server))

    assert len(embeddings_server.requests) == 2


def test_real_ai_failure_in_later_batch_discards_all_chunks(database, embeddings_server, monkeypatch):
    monkeypatch.setattr(worker, "EMBEDDINGS_BATCH_SIZE", 2)
    original = b"A" * 700 + b"\n" + b"B" * 700 + b"\n" + b"C" * 700
    document_id = pending_document(database, original)

    def answer(request):
        if len(embeddings_server.requests) == 2:
            embeddings_server.status = 503
            return {"error": "private provider message"}
        return {"data": [{"index": index, "embedding": [0.1, 0.2]} for index, _text in enumerate(request["input"])]}

    embeddings_server.payload = answer

    assert worker.process_one(database, real_ai_config(embeddings_server))

    assert [len(request[2]["input"]) for request in embeddings_server.requests] == [2, 1]
    with database() as db:
        document = db.get(Document, document_id)
        assert document.status == "failed" and document.error == "API embeddings вернул HTTP 503"
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all() == []


def test_demo_never_calls_embeddings_api(database, embeddings_server):
    document_id = pending_document(database)
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo", ai_base_url=embeddings_server.base_url)

    assert worker.process_one(database, config)

    assert embeddings_server.requests == []
    with database() as db:
        assert db.get(Document, document_id).status == "ready"
        assert db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).embedding is None


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"data": []}, "неверное число векторов"),
        ({"data": [{"index": 1, "embedding": [0.1]}]}, "некорректные индексы"),
        ({"data": [{"index": 0, "embedding": []}]}, "неверную размерность"),
        ({"data": [{"index": 0, "embedding": [float("nan")]}]}, "некорректные числа"),
        ({"data": [{"index": 0, "embedding": [True]}]}, "некорректные числа"),
        ({"data": [{"index": 0, "embedding": [1e308]}]}, "некорректные числа"),
        (b"not json", "некорректный JSON"),
    ],
)
def test_real_ai_rejects_invalid_responses_without_chunks(database, embeddings_server, payload, error):
    document_id = pending_document(database)
    embeddings_server.payload = payload

    assert worker.process_one(database, real_ai_config(embeddings_server))

    with database() as db:
        document = db.get(Document, document_id)
        assert document.status == "failed" and error in document.error
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all() == []


def test_real_ai_rejects_mixed_dimensions(database, embeddings_server):
    document_id = pending_document(database, b"A" * 700 + b"\n" + b"B" * 700)
    embeddings_server.payload = {
        "data": [
            {"index": 0, "embedding": [0.1]},
            {"index": 1, "embedding": [0.2, 0.3]},
        ]
    }

    assert worker.process_one(database, real_ai_config(embeddings_server))

    with database() as db:
        document = db.get(Document, document_id)
        assert document.status == "failed" and document.error == "Размерности embeddings различаются"
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all() == []


def test_real_ai_hides_provider_error_and_key(database, embeddings_server, caplog):
    document_id = pending_document(database)
    embeddings_server.status = 503
    embeddings_server.payload = {"error": "private provider message"}

    assert worker.process_one(database, real_ai_config(embeddings_server))

    with database() as db:
        document = db.get(Document, document_id)
        assert document.status == "failed" and document.error == "API embeddings вернул HTTP 503"
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all() == []
    assert "private provider message" not in caplog.text
    assert "private-test-key" not in caplog.text
    assert "Evidence" not in caplog.text


def test_real_ai_timeout_fails_without_chunks(database, embeddings_server):
    document_id = pending_document(database)
    embeddings_server.delay = 0.2

    assert worker.process_one(database, real_ai_config(embeddings_server, timeout=0.05))

    with database() as db:
        document = db.get(Document, document_id)
        assert document.status == "failed" and document.error == "Превышено время ожидания API embeddings"
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all() == []
