import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal, Sequence

import httpx
from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.orm import Session, aliased, defer, sessionmaker

from app.config import Settings
from app.kb import ExtractedChunk, config_signature
from app.models import Conversation, Document, DocumentChunk, Message, MessageSource
from app.worker import create_embeddings

INSUFFICIENT_TEXT = "Недостаточно информации в базе знаний для ответа на вопрос."
INDEX_UNAVAILABLE_TEXT = "Индекс документов недоступен для текущей конфигурации."
DEMO_TEXT = "Демо-режим: ИИ-ответ не формируется. Ниже приведены найденные выдержки."
EN_INSUFFICIENT_TEXT = "The knowledge base does not contain enough information to answer this question."
EN_INDEX_UNAVAILABLE_TEXT = "The document index is unavailable for the current configuration."
EN_DEMO_TEXT = "Demo mode: no AI answer is generated. Matching excerpts are shown below."
PROJECT_OVERVIEW_INTENT = "про что проект"
JSON_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```", re.IGNORECASE | re.DOTALL)


def _service_text(kind: Literal["demo", "insufficient", "index_unavailable"], language: Literal["ru", "en"] | None) -> str:
    texts = (
        {"demo": EN_DEMO_TEXT, "insufficient": EN_INSUFFICIENT_TEXT, "index_unavailable": EN_INDEX_UNAVAILABLE_TEXT}
        if language == "en" else
        {"demo": DEMO_TEXT, "insufficient": INSUFFICIENT_TEXT, "index_unavailable": INDEX_UNAVAILABLE_TEXT}
    )
    return texts[kind]


class ChatError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _parse_completion_content(content: object) -> object:
    if not isinstance(content, str):
        raise TypeError("completion content is not a string")
    stripped = content.strip()
    match = JSON_FENCE_RE.fullmatch(stripped)
    if match is not None:
        stripped = match.group("body").strip()
    return json.loads(stripped)


@dataclass(frozen=True)
class Candidate:
    chunk_id: int
    document_id: int
    generation: int
    filename: str
    text: str
    page_number: int | None
    line_start: int | None
    line_end: int | None
    citation_id: str | None = None


@dataclass(frozen=True)
class HistoryPair:
    question: str
    answer: str


@dataclass(frozen=True)
class AnswerResult:
    kind: Literal["answer", "demo", "insufficient", "index_unavailable"]
    text: str
    sources: tuple[Candidate, ...] = ()


def _validate_question(question: str, config: Settings) -> str:
    question = question.strip()
    if not question or len(question) > config.chat_max_question_chars:
        raise ChatError(422, "Вопрос пуст или превышает допустимую длину")
    return question


def _require_conversation(db: Session, conversation_id: int, owner_id: int, *, lock: bool = False) -> Conversation:
    statement = select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    conversation = db.scalar(statement)
    if conversation is None:
        raise ChatError(404, "Беседа не найдена")
    return conversation


def _current_conditions(config: Settings):
    conditions = (
        Document.status == "ready",
        Document.config_signature == config_signature(config),
        DocumentChunk.generation == Document.generation,
    )
    if config.kb_mode == "real_ai":
        return (*conditions, DocumentChunk.embedding.is_not(None))
    return conditions


def _index_available(db: Session, config: Settings, dimension: int | None = None) -> bool:
    statement = select(DocumentChunk.id).join(Document).where(*_current_conditions(config))
    if config.kb_mode == "real_ai" and db.bind.dialect.name == "sqlite":
        vectors = db.scalars(select(DocumentChunk.embedding).join(Document).where(*_current_conditions(config)))
        return any(vector is not None and (dimension is None or len(vector) == dimension) for vector in vectors)
    if dimension is not None and db.bind.dialect.name == "postgresql":
        statement = statement.where(func.vector_dims(DocumentChunk.embedding) == dimension)
    return db.scalar(statement.limit(1)) is not None


def _candidate(chunk: DocumentChunk, filename: str) -> Candidate:
    return Candidate(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        generation=chunk.generation,
        filename=filename,
        text=chunk.text,
        page_number=chunk.page_number,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
    )


def _normalized_intent(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return " ".join(re.findall(r"\w+", normalized))


def _project_overview(db: Session, config: Settings) -> Candidate | None:
    row = db.execute(
        select(DocumentChunk, Document.filename)
        .join(Document)
        .where(
            *_current_conditions(config),
            func.lower(Document.filename) == "readme.md",
            DocumentChunk.chunk_index == 0,
        )
        .order_by(Document.id.desc())
        .limit(1)
    ).first()
    return _candidate(*row) if row is not None else None


def _demo_search(db: Session, question: str, config: Settings) -> list[Candidate]:
    if _normalized_intent(question) == PROJECT_OVERVIEW_INTENT:
        overview = _project_overview(db, config)
        if overview is not None:
            return [overview]
    statement = select(DocumentChunk, Document.filename).join(Document).where(
        *_current_conditions(config),
        or_(func.lower(Document.filename) != "readme.md", DocumentChunk.chunk_index == 0),
    )
    if db.bind.dialect.name == "postgresql":
        search_language = "'russian'" if re.search(r"[А-Яа-яЁё]", question) else "'english'"
        vector = func.to_tsvector(literal_column(search_language), DocumentChunk.text)
        query = func.plainto_tsquery(literal_column(search_language), question)
        statement = statement.where(vector.op("@@")(query)).order_by(func.ts_rank_cd(vector, query).desc(), DocumentChunk.id)
        rows = db.execute(statement.limit(config.chat_top_k)).all()
    else:
        terms = re.findall(r"\w+", question.casefold())
        if not terms:
            return []
        rows = [
            row for row in db.execute(statement)
            if all(term in row[0].text.casefold() for term in terms)
        ]
        rows.sort(key=lambda row: (-sum(row[0].text.casefold().count(term) for term in terms), row[0].id))
        rows = rows[:config.chat_top_k]
    return [_candidate(chunk, filename) for chunk, filename in rows]


def _real_search(db: Session, vector: list[float], config: Settings) -> list[Candidate]:
    statement = select(DocumentChunk, Document.filename).join(Document).where(*_current_conditions(config))
    if db.bind.dialect.name == "postgresql":
        distance = case(
            (func.vector_dims(DocumentChunk.embedding) == len(vector), DocumentChunk.embedding.cosine_distance(vector)),
            else_=None,
        )
        statement = statement.where(
            distance <= 1 - config.chat_min_similarity,
        ).order_by(distance, DocumentChunk.id)
        rows = db.execute(statement.limit(config.chat_top_k)).all()
    else:
        ranked = []
        vector_norm = math.sqrt(sum(value * value for value in vector))
        for chunk, filename in db.execute(statement):
            embedding = chunk.embedding
            if embedding is None or len(embedding) != len(vector):
                continue
            embedding_norm = math.sqrt(sum(value * value for value in embedding))
            if not vector_norm or not embedding_norm:
                continue
            similarity = sum(left * right for left, right in zip(vector, embedding)) / (vector_norm * embedding_norm)
            if similarity >= config.chat_min_similarity:
                ranked.append((similarity, chunk, filename))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        rows = [(chunk, filename) for _, chunk, filename in ranked[:config.chat_top_k]]
    return [_candidate(chunk, filename) for chunk, filename in rows]


def _history(db: Session, conversation_id: int) -> list[HistoryPair]:
    question = aliased(Message)
    assistant = aliased(Message)
    rows = db.execute(
        select(question.text, assistant.text)
        .join(question, assistant.reply_to_id == question.id)
        .where(assistant.conversation_id == conversation_id, question.conversation_id == conversation_id, assistant.role == "assistant")
        .order_by(assistant.id.desc())
        .limit(3)
    ).all()
    return [HistoryPair(user_text, answer_text) for user_text, answer_text in reversed(rows)]


def _chat_completion(question: str, history: Sequence[HistoryPair], candidates: list[Candidate], config: Settings,
                     language: Literal["ru", "en"] | None = None) -> AnswerResult:
    if not config.ai_chat_model:
        raise ChatError(503, "AI_CHAT_MODEL не настроена")
    by_citation = {f"c{number}": candidate for number, candidate in enumerate(candidates, start=1)}
    context = [
        {
            "id": citation_id,
            "excerpt": candidate.text[:config.chat_max_excerpt_chars],
        }
        for citation_id, candidate in by_citation.items()
    ]
    system = (
        "Answer the current question in English using only excerpts in sources. History helps interpret the question "
        "but is not evidence. Return only JSON: "
        '{"insufficient":false,"answer":"text","citation_ids":["c1"]} '
        'or {"insufficient":true,"citation_ids":[]}. Do not cite IDs outside sources.'
        if language == "en" else
        "Ответь на текущий вопрос только по выдержкам sources. История помогает понять контекст, "
        "но не служит источником фактов. Верни только JSON: "
        '{"insufficient":false,"answer":"текст","citation_ids":["c1"]} '
        'или {"insufficient":true,"citation_ids":[]}. '
        "Не ссылайся на ID вне sources."
    )
    payload = {
        "model": config.ai_chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "question": question,
                "sources": context,
                "history": [{"question": pair.question, "answer": pair.answer} for pair in history],
            }, ensure_ascii=False)},
        ],
    }
    try:
        with httpx.Client(timeout=config.ai_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{config.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.ai_api_key}"},
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise ChatError(504, "Превышено время ожидания API генерации") from exc
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        raise ChatError(502, "Не удалось связаться с API генерации") from exc
    if response.status_code != 200:
        raise ChatError(502, f"API генерации вернул HTTP {response.status_code}")
    try:
        envelope = response.json()
        content = envelope["choices"][0]["message"]["content"]
        result = _parse_completion_content(content)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ChatError(502, "API генерации вернул некорректный ответ") from exc
    if not isinstance(result, dict) or type(result.get("insufficient")) is not bool:
        raise ChatError(502, "API генерации вернул некорректный ответ")
    citation_ids = result.get("citation_ids")
    if not isinstance(citation_ids, list) or any(type(value) is not str for value in citation_ids):
        raise ChatError(502, "API генерации вернул некорректные ссылки на источники")
    if result["insufficient"]:
        if citation_ids:
            raise ChatError(502, "API генерации вернул некорректные ссылки на источники")
        return AnswerResult("insufficient", _service_text("insufficient", language))
    answer = result.get("answer")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > config.chat_max_answer_chars:
        raise ChatError(502, "API генерации вернул некорректный текст ответа")
    if not citation_ids or len(set(citation_ids)) != len(citation_ids) or any(value not in by_citation for value in citation_ids):
        raise ChatError(502, "API генерации вернул некорректные ссылки на источники")
    if any(value not in citation_ids for value in re.findall(r"\[(c\d+)\]", answer)):
        raise ChatError(502, "API генерации вернул некорректные ссылки на источники")
    return AnswerResult(
        "answer", answer.strip(),
        tuple(replace(by_citation[value], citation_id=value) for value in citation_ids),
    )


def _save_pair(factory: sessionmaker[Session], conversation_id: int, owner_id: int, question: str,
               result: AnswerResult, config: Settings) -> tuple[int, int]:
    with factory() as db:
        conversation = _require_conversation(db, conversation_id, owner_id, lock=True)
        if result.sources:
            document_ids = sorted({candidate.document_id for candidate in result.sources})
            documents = {
                document.id: document for document in db.scalars(
                    select(Document).options(defer(Document.original))
                    .where(Document.id.in_(document_ids)).order_by(Document.id).with_for_update()
                )
            }
            chunks = {
                chunk.id: chunk for chunk in db.scalars(
                    select(DocumentChunk).where(DocumentChunk.id.in_([candidate.chunk_id for candidate in result.sources]))
                )
            }
            signature = config_signature(config)
            for candidate in result.sources:
                document = documents.get(candidate.document_id)
                chunk = chunks.get(candidate.chunk_id)
                if (
                    document is None or chunk is None or document.status != "ready"
                    or document.config_signature != signature or document.generation != candidate.generation
                    or chunk.document_id != document.id or chunk.generation != document.generation
                    or chunk.text != candidate.text or (config.kb_mode == "real_ai" and chunk.embedding is None)
                ):
                    raise ChatError(409, "Источник изменился во время подготовки ответа; повторите вопрос")
        user_message = Message(conversation_id=conversation_id, role="user", kind="question", text=question)
        db.add(user_message)
        db.flush()
        assistant_message = Message(conversation_id=conversation_id, reply_to_id=user_message.id, role="assistant", kind=result.kind, text=result.text)
        db.add(assistant_message)
        db.flush()
        db.add_all(
            MessageSource(
                message_id=assistant_message.id,
                document_id=candidate.document_id,
                ordinal=ordinal,
                filename=candidate.filename,
                page_number=candidate.page_number,
                line_start=candidate.line_start,
                line_end=candidate.line_end,
                citation_id=candidate.citation_id,
                excerpt=candidate.text[:config.chat_max_excerpt_chars],
            )
            for ordinal, candidate in enumerate(result.sources, start=1)
        )
        conversation.updated_at = func.now()
        db.commit()
        return user_message.id, assistant_message.id


def generate_answer(factory: sessionmaker[Session], question: str,
                    history: Sequence[HistoryPair], config: Settings,
                    language: Literal["ru", "en"] | None = None) -> AnswerResult:
    question = _validate_question(question, config)
    recent_history = history[-3:]
    overview: Candidate | None = None
    with factory() as db:
        available = _index_available(db, config)
        candidates = []
        if available:
            if config.kb_mode == "demo":
                candidates = _demo_search(db, question, config)
            elif _normalized_intent(question) == PROJECT_OVERVIEW_INTENT:
                overview = _project_overview(db, config)

    if not available:
        return AnswerResult("index_unavailable", _service_text("index_unavailable", language))
    if config.kb_mode == "demo":
        return AnswerResult("demo", _service_text("demo", language), tuple(candidates)) if candidates else AnswerResult("insufficient", _service_text("insufficient", language))
    if not config.ai_chat_model:
        raise ChatError(503, "AI_CHAT_MODEL не настроена")
    if overview is not None:
        return (
            _chat_completion(question, recent_history, [overview], config)
            if language is None else _chat_completion(question, recent_history, [overview], config, language)
        )
    try:
        vector = create_embeddings([ExtractedChunk(question)], config)[0]
    except ValueError as exc:
        status_code = 504 if "время ожидания" in str(exc) else 502
        raise ChatError(status_code, "Ошибка API embeddings для вопроса") from exc
    with factory() as db:
        compatible = _index_available(db, config, len(vector))
        candidates = _real_search(db, vector, config) if compatible else []
    if not compatible:
        return AnswerResult("index_unavailable", _service_text("index_unavailable", language))
    if not candidates:
        return AnswerResult("insufficient", _service_text("insufficient", language))
    return (
        _chat_completion(question, recent_history, candidates, config)
        if language is None else _chat_completion(question, recent_history, candidates, config, language)
    )


def answer_question(factory: sessionmaker[Session], owner_id: int, conversation_id: int,
                    question: str, config: Settings) -> tuple[int, int]:
    question = _validate_question(question, config)
    with factory() as db:
        _require_conversation(db, conversation_id, owner_id)
        history = _history(db, conversation_id)
    result = generate_answer(factory, question, history, config)
    return _save_pair(factory, conversation_id, owner_id, question, result, config)
