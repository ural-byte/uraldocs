import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.models import Base, Document, DocumentChunk


@pytest.fixture
def postgres_documents():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Для проверки worker задайте TEST_POSTGRES_URL")
    schema = f"uraldocs_test_{uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
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


def test_expired_lease_is_recovered(postgres_documents):
    factory = postgres_documents
    with factory() as db:
        document = Document(filename="note.txt", file_type="txt", original=b"Recovered", status="pending", generation=1)
        document.lease_token = "abandoned"
        document.lease_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.add(document)
        db.commit()
        document_id = document.id

    assert worker.process_one(factory)
    with factory() as db:
        document = db.get(Document, document_id)
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all()
        assert document.status == "ready" and document.lease_token is None
        assert len(chunks) == 1 and chunks[0].text == "Recovered"


def test_claim_and_late_result_are_safe(postgres_documents, monkeypatch):
    factory = postgres_documents
    with factory() as db:
        document = Document(filename="note.txt", file_type="txt", original=b"Current", status="pending", generation=1)
        db.add(document)
        db.commit()
        document_id = document.id

    paused = Event()
    resume = Event()
    original_extract = worker.extract_chunks

    def pause_first(original, kind):
        if current_thread().name.startswith("old-worker"):
            paused.set()
            assert resume.wait(10)
        return original_extract(original, kind)

    monkeypatch.setattr(worker, "extract_chunks", pause_first)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-worker") as pool:
        old = pool.submit(worker.process_one, factory)
        assert paused.wait(10)
        assert not worker.process_one(factory)
        with factory() as db:
            document = db.get(Document, document_id)
            document.lease_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        assert worker.process_one(factory)
        resume.set()
        assert old.result(timeout=10)

    with factory() as db:
        document = db.get(Document, document_id)
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all()
        assert document.status == "ready" and len(chunks) == 1
        assert chunks[0].generation == document.generation
