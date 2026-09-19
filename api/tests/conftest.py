import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

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
def embeddings_server():
    state = SimpleNamespace(requests=[], status=200, payload={}, delay=0.0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
            if state.delay:
                time.sleep(state.delay)
            payload = state.payload
            if callable(payload):
                payload = payload(state.requests[-1][2])
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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
