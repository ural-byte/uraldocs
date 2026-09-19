import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select

from app import worker
from app.config import Settings, settings
from app.models import Document, DocumentChunk

ORIGIN = {"origin": settings.app_origin}


def login(client, username="admin", password="admin-secret-123"):
    assert client.post("/auth/login", json={"username": username, "password": password}, headers=ORIGIN).status_code == 200


def upload(client, filename="notes.txt", content=b"First line\nSecond line"):
    return client.post("/admin/documents", files={"file": (filename, content, "application/octet-stream")}, headers=ORIGIN)


def test_document_lifecycle(client, database, users):
    assert client.get("/admin/documents").status_code == 401
    login(client, "reader", "reader-secret-123")
    assert client.get("/admin/documents").status_code == 403
    assert upload(client).status_code == 403
    client.cookies.clear()
    login(client)
    response = upload(client, "folder/notes.md", b"Alpha\nBeta")
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "pending" and not doc["index_current"]
    assert doc["filename"] == "notes.md"
    assert client.get(f"/admin/documents/{doc['id']}").json()["status"] == "pending"
    assert client.post(f"/admin/documents/{doc['id']}/reindex", headers=ORIGIN).status_code == 409

    assert worker.process_one(database)
    ready = client.get(f"/admin/documents/{doc['id']}").json()
    assert ready["status"] == "ready" and ready["index_current"]
    with database() as db:
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc["id"])).all()
        assert chunks and chunks[0].line_start == 1 and chunks[0].line_end == 2
        assert chunks[0].embedding is None

    queued = client.post(f"/admin/documents/{doc['id']}/reindex", headers=ORIGIN).json()
    assert queued["status"] == "pending" and queued["generation"] == 2
    with database() as db:
        assert db.scalars(select(DocumentChunk)).all() == []
    assert worker.process_one(database)
    assert client.post("/admin/documents/reindex", headers=ORIGIN).json() == {"queued": 1}
    assert client.post("/admin/documents/reindex", headers=ORIGIN).json() == {"queued": 1}
    assert client.get(f"/admin/documents/{doc['id']}").json()["generation"] == 4
    assert worker.process_one(database)
    assert client.delete(f"/admin/documents/{doc['id']}", headers=ORIGIN).status_code == 204
    assert client.get("/admin/documents").json() == []
    assert client.get(f"/admin/documents/{doc['id']}").status_code == 404
    with database() as db:
        assert db.scalars(select(DocumentChunk)).all() == []
        assert db.scalars(select(Document)).all() == []


def test_invalid_upload_and_limit(client, users, monkeypatch):
    login(client)
    assert upload(client, "archive.zip", b"data").status_code == 415
    assert upload(client, "fake.pdf", b"not a pdf").status_code == 415
    assert upload(client, "fake.txt", b"\x00binary").status_code == 415
    assert upload(client, "empty.txt", b"").status_code == 422
    assert client.post("/admin/documents", json={"file": "text"}, headers=ORIGIN).status_code == 415
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    assert upload(client, "large.txt", b"x" * 11).status_code == 413
    oversized = b"x" * (1024 * 1024 + 11)
    assert client.post(
        "/admin/documents",
        content=oversized,
        headers={**ORIGIN, "content-type": "multipart/form-data; boundary=test"},
    ).status_code == 413


def test_pdf_without_text_and_corrupt_text_fail(client, database, users):
    login(client)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf_id = upload(client, "scan.pdf", buffer.getvalue()).json()["id"]
    assert worker.process_one(database)
    pdf = client.get(f"/admin/documents/{pdf_id}").json()
    assert pdf["status"] == "failed" and "текстового слоя" in pdf["error"]
    corrupt_id = upload(client, "bad.txt", b"\xff\xfe").json()["id"]
    assert worker.process_one(database)
    corrupt = client.get(f"/admin/documents/{corrupt_id}").json()
    assert corrupt["status"] == "failed" and "UTF-8" in corrupt["error"]
    with database() as db:
        assert db.scalars(select(DocumentChunk)).all() == []


def test_pdf_text_is_indexed_with_page_number(client, database, users):
    login(client)
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }),
        }),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 50 Td (Hello PDF) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    document_id = upload(client, "text.pdf", buffer.getvalue()).json()["id"]
    assert worker.process_one(database)
    with database() as db:
        chunk = db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document_id))
        assert chunk.text == "Hello PDF" and chunk.page_number == 1


def test_late_worker_cannot_restore_deleted_or_reindexed_document(client, database, users, monkeypatch):
    login(client)
    first_id = upload(client, "first.txt", b"First").json()["id"]

    original_extract = worker.extract_chunks

    def delete_during_extract(content, kind):
        assert client.delete(f"/admin/documents/{first_id}", headers=ORIGIN).status_code == 204
        return original_extract(content, kind)

    monkeypatch.setattr(worker, "extract_chunks", delete_during_extract)
    assert worker.process_one(database)
    with database() as db:
        assert db.get(Document, first_id) is None
        assert db.scalars(select(DocumentChunk)).all() == []

    monkeypatch.setattr(worker, "extract_chunks", original_extract)
    second_id = upload(client, "second.txt", b"Second").json()["id"]

    def invalidate_during_extract(content, kind):
        with database() as db:
            document = db.get(Document, second_id)
            document.generation += 1
            document.lease_token = None
            db.commit()
        return original_extract(content, kind)

    monkeypatch.setattr(worker, "extract_chunks", invalidate_during_extract)
    assert worker.process_one(database)
    with database() as db:
        document = db.get(Document, second_id)
        assert document.status == "pending" and document.generation == 2
        assert db.scalars(select(DocumentChunk)).all() == []


def test_real_ai_requires_embeddings_and_never_falls_back(database, monkeypatch):
    config = Settings(
        database_url="sqlite+pysqlite://",
        kb_mode="real_ai",
        ai_base_url="https://provider.invalid/v1",
        ai_api_key="test-key",
        ai_embedding_model="test-model",
    )
    with database() as db:
        doc = Document(filename="note.txt", file_type="txt", original=b"Evidence", status="pending", generation=1)
        db.add(doc)
        db.commit()
        first_id = doc.id
    assert worker.process_one(database, config)
    with database() as db:
        assert db.get(Document, first_id).status == "failed"
        assert db.scalars(select(DocumentChunk)).all() == []

    with database() as db:
        doc = Document(filename="other.txt", file_type="txt", original=b"Evidence", status="pending", generation=1)
        db.add(doc)
        db.commit()
        second_id = doc.id
    monkeypatch.setattr(worker, "create_embeddings", lambda chunks, _config: [[0.1, 0.2] for _ in chunks])
    assert worker.process_one(database, config)
    with database() as db:
        assert db.get(Document, second_id).status == "ready"
        chunk = db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == second_id))
        assert chunk.embedding == [0.1, 0.2]

    with database() as db:
        doc = Document(filename="invalid.txt", file_type="txt", original=b"Evidence", status="pending", generation=1)
        db.add(doc)
        db.commit()
        third_id = doc.id
    monkeypatch.setattr(worker, "create_embeddings", lambda _chunks, _config: [[float("nan")]])
    assert worker.process_one(database, config)
    with database() as db:
        assert db.get(Document, third_id).status == "failed"
        assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == third_id)).all() == []
