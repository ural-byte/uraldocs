import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, settings
from app.db import SessionLocal
from app.kb import ExtractedChunk, config_signature, extract_chunks, validate_embeddings
from app.models import Document, DocumentChunk

logger = logging.getLogger("uraldocs.worker")
EMBEDDINGS_BATCH_SIZE = 64


class _EmbeddingsHTTPError(ValueError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"API embeddings вернул HTTP {status_code}")


def _request_embeddings(client: httpx.Client, chunks: list[ExtractedChunk], config: Settings) -> list[list[float]]:
    try:
        response = client.post(
            f"{config.ai_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {config.ai_api_key}"},
            json={"model": config.ai_embedding_model, "input": [chunk.text for chunk in chunks]},
        )
    except httpx.TimeoutException as exc:
        raise ValueError("Превышено время ожидания API embeddings") from exc
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        raise ValueError("Не удалось связаться с API embeddings") from exc

    if response.status_code != 200:
        raise _EmbeddingsHTTPError(response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("API embeddings вернул некорректный JSON") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("API embeddings вернул некорректный ответ")
    data = payload["data"]
    if len(data) != len(chunks):
        raise ValueError("API embeddings вернул неверное число векторов")
    vectors_by_index: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("API embeddings вернул некорректный ответ")
        index = item.get("index")
        if type(index) is not int or not 0 <= index < len(chunks) or index in vectors_by_index:
            raise ValueError("API embeddings вернул некорректные индексы векторов")
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            raise ValueError("API embeddings вернул некорректный ответ")
        vectors_by_index[index] = embedding
    return validate_embeddings([vectors_by_index[index] for index in range(len(chunks))], len(chunks))


def _extend_embeddings(vectors: list[list[float]], batch_vectors: list[list[float]]) -> None:
    if vectors and len(batch_vectors[0]) != len(vectors[0]):
        raise ValueError("Размерности embeddings различаются")
    vectors.extend(batch_vectors)


def create_embeddings(chunks: list[ExtractedChunk], config: Settings) -> list[list[float]]:
    vectors: list[list[float]] = []
    singleton_only = False
    with httpx.Client(timeout=config.ai_timeout_seconds, follow_redirects=False, trust_env=False) as client:
        for start in range(0, len(chunks), EMBEDDINGS_BATCH_SIZE):
            batch = chunks[start:start + EMBEDDINGS_BATCH_SIZE]
            if singleton_only:
                for chunk in batch:
                    _extend_embeddings(vectors, _request_embeddings(client, [chunk], config))
                continue
            try:
                batch_vectors = _request_embeddings(client, batch, config)
            except _EmbeddingsHTTPError as exc:
                if exc.status_code != 400 or len(batch) == 1:
                    raise
                _extend_embeddings(vectors, _request_embeddings(client, batch[:1], config))
                singleton_only = True
                for chunk in batch[1:]:
                    _extend_embeddings(vectors, _request_embeddings(client, [chunk], config))
            else:
                _extend_embeddings(vectors, batch_vectors)
    return vectors


def process_one(factory: sessionmaker[Session] = SessionLocal, config: Settings = settings) -> bool:
    now = datetime.now(timezone.utc)
    token = str(uuid4())
    with factory() as db:
        document = db.scalar(
            select(Document)
            .where(Document.status == "pending", or_(Document.lease_deadline.is_(None), Document.lease_deadline < now))
            .order_by(Document.id)
            .with_for_update(skip_locked=True)
        )
        if document is None:
            return False
        document.lease_token = token
        document.lease_deadline = now + timedelta(seconds=config.worker_lease_seconds)
        document_id = document.id
        generation = document.generation
        original = document.original
        file_type = document.file_type
        db.commit()

    chunks: list[ExtractedChunk] = []
    embeddings: list[list[float] | None] = []
    error: str | None = None
    try:
        chunks = extract_chunks(original, file_type)
        embeddings = validate_embeddings(create_embeddings(chunks, config), len(chunks)) if config.kb_mode == "real_ai" else [None] * len(chunks)
    except ValueError as exc:
        error = str(exc)[:512]
        logger.warning("Документ %s, поколение %s: %s", document_id, generation, error)
    except Exception:
        error = "Внутренняя ошибка обработки документа"
        logger.exception("Ошибка обработки документа %s", document_id)

    with factory() as db:
        document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is None or document.generation != generation or document.lease_token != token:
            return True
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        if error is None:
            db.add_all(
                DocumentChunk(
                    document_id=document_id,
                    generation=generation,
                    chunk_index=index,
                    text=chunk.text,
                    page_number=chunk.page_number,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    embedding=embeddings[index],
                )
                for index, chunk in enumerate(chunks)
            )
            document.status = "ready"
            document.config_signature = config_signature(config)
        else:
            document.status = "failed"
            document.config_signature = None
        document.error = error
        document.lease_token = None
        document.lease_deadline = None
        db.commit()
    return True


def main() -> None:
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            if not process_one():
                time.sleep(2)
        except Exception:
            logger.exception("Ошибка worker; повторная попытка через 2 секунды")
            time.sleep(2)


if __name__ == "__main__":
    main()
