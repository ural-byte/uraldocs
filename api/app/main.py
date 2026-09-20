import logging
import re
import time
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession, defer, selectinload, sessionmaker
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.chat import ChatError, answer_question
from app.db import get_db
from app.kb import config_signature
from app.models import Conversation, Document, DocumentChunk, Message, MessageSource, User
from app.security import (
    SESSION_COOKIE,
    create_session,
    find_session,
    hash_password,
    revoke_sessions,
    token_digest,
    valid_password,
    verify_password,
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("uraldocs.api")
app = FastAPI(title="UralDocs API")
Db = Annotated[DatabaseSession, Depends(get_db)]
AUTH_BODY_LIMIT = 16 * 1024
REINDEX_BATCH_SIZE = 100
AUTH_BODY_LIMIT_PATHS = frozenset({"/auth/login", "/auth/logout", "/admin/users", "/conversations"})
AUTH_BODY_LIMIT_USER_ACTION = re.compile(r"^/admin/users/[^/]+/(?:disable|reset-password)$")
CHAT_BODY_LIMIT_ACTION = re.compile(r"^/conversations/[^/]+/messages$")


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        upload = path == "/admin/documents" and scope.get("method") == "POST"
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or (path not in AUTH_BODY_LIMIT_PATHS and AUTH_BODY_LIMIT_USER_ACTION.fullmatch(path) is None
                and CHAT_BODY_LIMIT_ACTION.fullmatch(path) is None and not upload)
        ):
            await self.app(scope, receive, send)
            return

        if upload and not any(
            name == b"content-type" and value.lower().startswith(b"multipart/form-data;")
            for name, value in scope.get("headers", [])
        ):
            await JSONResponse({"detail": "Требуется multipart/form-data"}, status_code=415)(scope, receive, send)
            return

        if upload:
            limit = settings.max_upload_bytes + 1024 * 1024
        elif CHAT_BODY_LIMIT_ACTION.fullmatch(path):
            limit = 12 * settings.chat_max_question_chars + 1024
        else:
            limit = AUTH_BODY_LIMIT

        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await JSONResponse({"detail": "Превышен размер загрузки"}, status_code=413)(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await JSONResponse({"detail": "Превышен размер загрузки"}, status_code=413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


app.add_middleware(RequestBodyLimitMiddleware)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str
    role: str = "user"


class PasswordReset(BaseModel):
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool


class UiConfigResponse(BaseModel):
    kb_mode: str
    max_upload_bytes: int
    chat_max_question_chars: int


class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    error: str | None
    generation: int
    config_signature: str | None
    index_current: bool
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="Новая беседа", min_length=1, max_length=120)


class ConversationResponse(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class SourceResponse(BaseModel):
    id: int
    citation_id: str | None
    filename: str
    page_number: int | None
    line_start: int | None
    line_end: int | None
    excerpt: str | None
    deleted: bool
    anchor: str


class MessageResponse(BaseModel):
    id: int
    role: str
    kind: str
    text: str
    reply_to_id: int | None
    created_at: datetime
    sources: list[SourceResponse]


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse]


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10000)


class QuestionPairResponse(BaseModel):
    user: MessageResponse
    assistant: MessageResponse


def as_conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def as_message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        kind=message.kind,
        text=message.text,
        reply_to_id=message.reply_to_id,
        created_at=message.created_at,
        sources=[
            SourceResponse(
                id=source.id,
                citation_id=source.citation_id,
                filename=source.filename,
                page_number=source.page_number,
                line_start=source.line_start,
                line_end=source.line_end,
                excerpt=source.excerpt,
                deleted=source.deleted_at is not None or source.document_id is None,
                anchor=f"source-{source.id}",
            )
            for source in sorted(message.sources, key=lambda item: item.ordinal)
        ],
    )


def as_document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        error=document.error,
        generation=document.generation,
        config_signature=document.config_signature,
        index_current=document.status == "ready" and document.config_signature == config_signature(settings),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def as_user_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, username=user.username, role=user.role, is_active=user.is_active)


@app.middleware("http")
async def security_and_logging(request: Request, call_next):
    started = time.monotonic()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("origin") != settings.app_origin.rstrip("/"):
            logger.warning("Отклонён запрос с неверным Origin: %s %s", request.method, request.url.path)
            return Response(status_code=status.HTTP_403_FORBIDDEN)
    response = await call_next(request)
    logger.info("%s %s %s %.3fs", request.method, request.url.path, response.status_code, time.monotonic() - started)
    return response


def current_user(request: Request, db: Db) -> User:
    session = find_session(db, request.cookies.get(SESSION_COOKIE))
    if session is None or not session.user.is_active:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return session.user


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


def target_user(db: DatabaseSession, user_id: int, actor: User) -> User:
    target = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.role != "user" or target.id == actor.id:
        raise HTTPException(status_code=403, detail="Управление этим пользователем через API запрещено")
    return target


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(db: Db):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.error("База данных недоступна")
        raise HTTPException(status_code=503, detail="База данных недоступна") from None
    return {"status": "ok"}


@app.post("/auth/login", response_model=UserResponse)
def login(payload: LoginRequest, response: Response, db: Db):
    user = db.scalar(select(User).where(User.username == payload.username).with_for_update())
    if user is None or not user.is_active or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_session(db, user)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return as_user_response(user)


@app.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Db):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        from app.models import Session

        session = db.scalar(select(Session).where(Session.token_hash == token_digest(token)))
        if session is not None:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")


@app.get("/auth/me", response_model=UserResponse)
def me(user: CurrentUser):
    return as_user_response(user)


@app.get("/ui/config", response_model=UiConfigResponse)
def ui_config(_user: CurrentUser):
    return UiConfigResponse(
        kb_mode=settings.kb_mode,
        max_upload_bytes=settings.max_upload_bytes,
        chat_max_question_chars=settings.chat_max_question_chars,
    )


@app.post("/conversations", response_model=ConversationResponse, status_code=201)
def create_conversation(payload: ConversationCreate, actor: CurrentUser, db: Db):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Название беседы пусто")
    conversation = Conversation(owner_id=actor.id, title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return as_conversation_response(conversation)


@app.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(actor: CurrentUser, db: Db):
    return [
        as_conversation_response(conversation)
        for conversation in db.scalars(
            select(Conversation).where(Conversation.owner_id == actor.id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        )
    ]


@app.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(conversation_id: int, actor: CurrentUser, db: Db):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == actor.id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    messages = db.scalars(
        select(Message).options(selectinload(Message.sources))
        .where(Message.conversation_id == conversation_id).order_by(Message.id)
    ).all()
    return ConversationDetailResponse(**as_conversation_response(conversation).model_dump(), messages=[as_message_response(item) for item in messages])


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, actor: CurrentUser, db: Db):
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == actor.id).with_for_update()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    db.delete(conversation)
    db.commit()


@app.post("/conversations/{conversation_id}/messages", response_model=QuestionPairResponse, status_code=201)
def ask_conversation(conversation_id: int, payload: QuestionRequest, actor: CurrentUser, db: Db):
    owner_id = actor.id
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        user_id, assistant_id = answer_question(factory, owner_id, conversation_id, payload.question, settings)
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    user_message = db.get(Message, user_id, options=[selectinload(Message.sources)])
    assistant_message = db.get(Message, assistant_id, options=[selectinload(Message.sources)])
    return QuestionPairResponse(user=as_message_response(user_message), assistant=as_message_response(assistant_message))


@app.get("/admin/users", response_model=list[UserResponse])
def list_users(_actor: AdminUser, db: Db):
    return [as_user_response(user) for user in db.scalars(select(User).where(User.role == "user").order_by(User.id))]


@app.post("/admin/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, _actor: AdminUser, db: Db):
    if payload.role != "user":
        raise HTTPException(status_code=403, detail="Создание admin через API запрещено")
    if not valid_password(payload.password):
        raise HTTPException(status_code=422, detail="Пароль должен содержать от 12 до 1024 символов")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role="user", is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Логин занят") from None
    db.refresh(user)
    return as_user_response(user)


@app.post("/admin/users/{user_id}/disable", response_model=UserResponse)
def disable_user(user_id: int, _actor: AdminUser, db: Db):
    user = target_user(db, user_id, _actor)
    user.is_active = False
    revoke_sessions(db, user.id)
    db.commit()
    return as_user_response(user)


@app.post("/admin/users/{user_id}/reset-password", response_model=UserResponse)
def reset_password(user_id: int, payload: PasswordReset, _actor: AdminUser, db: Db):
    user = target_user(db, user_id, _actor)
    if not valid_password(payload.password):
        raise HTTPException(status_code=422, detail="Пароль должен содержать от 12 до 1024 символов")
    user.password_hash = hash_password(payload.password)
    revoke_sessions(db, user.id)
    db.commit()
    return as_user_response(user)


@app.post("/admin/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(_actor: AdminUser, db: Db, file: UploadFile = File()):
    filename = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not filename or len(filename) > 255 or "\x00" in filename:
        raise HTTPException(status_code=422, detail="Некорректное имя файла")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    file_type = {"pdf": "pdf", "txt": "txt", "md": "md", "markdown": "md"}.get(extension)
    if file_type is None:
        raise HTTPException(status_code=415, detail="Поддерживаются только PDF, TXT и Markdown")
    original = await file.read(settings.max_upload_bytes + 1)
    if len(original) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Превышен размер файла")
    if not original:
        raise HTTPException(status_code=422, detail="Файл пуст")
    if file_type == "pdf" and not original.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Файл не является PDF")
    if file_type != "pdf" and (b"\x00" in original or original.startswith(b"%PDF-")):
        raise HTTPException(status_code=415, detail="Файл не является текстовым документом")
    document = Document(filename=filename, file_type=file_type, original=original, status="pending", generation=1)
    db.add(document)
    db.commit()
    db.refresh(document, attribute_names=["created_at", "updated_at"])
    return as_document_response(document)


@app.get("/admin/documents", response_model=list[DocumentResponse])
def list_documents(_actor: AdminUser, db: Db):
    return [as_document_response(doc) for doc in db.scalars(select(Document).options(defer(Document.original)).order_by(Document.id.desc()))]


@app.get("/admin/documents/{document_id}", response_model=DocumentResponse)
def document_status(document_id: int, _actor: AdminUser, db: Db):
    document = db.get(Document, document_id, options=[defer(Document.original)])
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return as_document_response(document)


@app.delete("/admin/documents/{document_id}", status_code=204)
def delete_document(document_id: int, _actor: AdminUser, db: Db):
    document = db.scalar(select(Document).options(defer(Document.original)).where(Document.id == document_id).with_for_update())
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    db.execute(
        update(MessageSource).where(MessageSource.document_id == document_id)
        .values(document_id=None, excerpt=None, deleted_at=func.now())
    )
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    db.delete(document)
    db.commit()


def queue_reindex(document: Document, db: DatabaseSession) -> None:
    document.generation += 1
    document.status = "pending"
    document.error = None
    document.config_signature = None
    document.lease_token = None
    document.lease_deadline = None
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))


@app.post("/admin/documents/{document_id}/reindex", response_model=DocumentResponse)
def reindex_document(document_id: int, _actor: AdminUser, db: Db):
    document = db.scalar(select(Document).options(defer(Document.original)).where(Document.id == document_id).with_for_update())
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document.status == "pending":
        raise HTTPException(status_code=409, detail="Документ уже ожидает обработки")
    queue_reindex(document, db)
    db.commit()
    db.refresh(document, attribute_names=["updated_at"])
    return as_document_response(document)


@app.post("/admin/documents/reindex", response_model=dict[str, int])
def reindex_all_documents(_actor: AdminUser, db: Db):
    last_id = 0
    max_id = db.scalar(select(func.max(Document.id))) or 0
    queued = 0
    while True:
        ids = db.scalars(
            select(Document.id)
            .where(Document.id > last_id, Document.id <= max_id)
            .order_by(Document.id)
            .limit(REINDEX_BATCH_SIZE)
            .with_for_update()
        ).all()
        if not ids:
            break
        db.execute(
            update(Document)
            .where(Document.id.in_(ids))
            .values(
                generation=Document.generation + 1,
                status="pending",
                error=None,
                config_signature=None,
                lease_token=None,
                lease_deadline=None,
            )
        )
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(ids)))
        last_id = ids[-1]
        queued += len(ids)
    db.commit()
    return {"queued": queued}
