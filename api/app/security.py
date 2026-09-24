import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DatabaseSession

from app.config import settings
from app.models import Session, User

SESSION_COOKIE = "uraldocs_session"
password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: DatabaseSession, user: User) -> str:
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    db.execute(delete(Session).where(Session.expires_at <= now))
    expires_at = now + timedelta(hours=settings.session_hours)
    db.add(Session(user_id=user.id, token_hash=token_digest(token), expires_at=expires_at))
    db.commit()
    return token


def find_session(db: DatabaseSession, token: str | None) -> Session | None:
    if not token:
        return None
    session = db.scalar(select(Session).where(Session.token_hash == token_digest(token)))
    if session is None:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    return session


def revoke_sessions(db: DatabaseSession, user_id: int) -> None:
    db.execute(delete(Session).where(Session.user_id == user_id))


def valid_password(password: str) -> bool:
    return 12 <= len(password) <= 1024
