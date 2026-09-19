import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import Base, User
from app.security import hash_password


@pytest.fixture
def database():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database):
    def override_db():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def users(database):
    with database() as db:
        admin = User(username="admin", role="admin", password_hash=hash_password("admin-secret-123"), is_active=True)
        user = User(username="reader", role="user", password_hash=hash_password("reader-secret-123"), is_active=True)
        db.add_all([admin, user])
        db.commit()
        return admin.id, user.id
