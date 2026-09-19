import logging
import re
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.db import get_db
from app.models import User
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
AUTH_BODY_LIMIT_PATHS = frozenset({"/auth/login", "/auth/logout", "/admin/users"})
AUTH_BODY_LIMIT_USER_ACTION = re.compile(r"^/admin/users/[^/]+/(?:disable|reset-password)$")


class AuthBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or (path not in AUTH_BODY_LIMIT_PATHS and AUTH_BODY_LIMIT_USER_ACTION.fullmatch(path) is None)
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > AUTH_BODY_LIMIT:
                await Response(status_code=413)(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > AUTH_BODY_LIMIT:
                await Response(status_code=413)(scope, receive, send)
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


app.add_middleware(AuthBodyLimitMiddleware)


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
