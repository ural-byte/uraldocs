from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.models import Session, User
from app.security import SESSION_COOKIE

ORIGIN = {"origin": settings.app_origin}


def login(client, username="admin", password="admin-secret-123"):
    return client.post("/auth/login", json={"username": username, "password": password}, headers=ORIGIN)


def test_health_and_login_logout(client, users):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}
    assert client.get("/auth/me").status_code == 401
    assert client.get("/ui/config").status_code == 401
    assert login(client, password="incorrect").status_code == 401
    response = login(client)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert client.get("/auth/me").json()["username"] == "admin"
    assert client.get("/ui/config").json() == {
        "kb_mode": settings.kb_mode,
        "max_upload_bytes": settings.max_upload_bytes,
        "chat_max_question_chars": settings.chat_max_question_chars,
    }
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_login_cleans_expired_sessions(client, database, users):
    with database() as db:
        db.add(Session(user_id=users[1], token_hash="expired", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()

    assert login(client).status_code == 200
    with database() as db:
        sessions = db.scalars(select(Session)).all()
        assert len(sessions) == 1
        assert sessions[0].user_id == users[0]


def test_origin_is_required_for_mutations(client, users):
    payload = {"username": "admin", "password": "admin-secret-123"}
    assert client.post("/auth/login", json=payload).status_code == 403
    assert client.post("/auth/login", json=payload, headers={"origin": "https://other.example"}).status_code == 403
    assert login(client).status_code == 200
    assert client.post("/auth/logout").status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_disabled_and_expired_sessions_are_rejected(client, database, users):
    assert login(client, "reader", "reader-secret-123").status_code == 200
    assert client.get("/auth/me").status_code == 200
    with database() as db:
        db.get(User, users[1]).is_active = False
        db.commit()
    assert client.get("/auth/me").status_code == 401
    with database() as db:
        db.get(User, users[1]).is_active = True
        db.scalar(select(Session)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.get("/auth/me").status_code == 401
    with database() as db:
        assert db.scalar(select(Session)) is None


def test_user_cannot_call_admin_api(client, users):
    assert login(client, "reader", "reader-secret-123").status_code == 200
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "new", "password": "long-password-123"}).status_code == 403
    assert client.post(f"/admin/users/{users[1]}/disable", headers=ORIGIN).status_code == 403
    assert client.post(f"/admin/users/{users[1]}/reset-password", headers=ORIGIN, json={"password": "long-password-123"}).status_code == 403


def test_admin_manages_only_users_and_revokes_sessions(client, database, users):
    assert login(client, "reader", "reader-secret-123").status_code == 200
    reader_cookie = client.cookies.get(SESSION_COOKIE)
    client.cookies.clear()
    assert login(client).status_code == 200
    assert [user["username"] for user in client.get("/admin/users").json()] == ["reader"]
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "otheradmin", "role": "admin", "password": "long-password-123"}).status_code == 403
    assert client.post(f"/admin/users/{users[0]}/disable", headers=ORIGIN).status_code == 403
    assert client.post(f"/admin/users/{users[0]}/reset-password", headers=ORIGIN, json={"password": "long-password-123"}).status_code == 403
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "new", "password": "long-password-123"}).status_code == 201
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "new", "password": "long-password-123"}).status_code == 409
    assert client.post("/admin/users/999/disable", headers=ORIGIN).status_code == 404
    assert client.post(f"/admin/users/{users[1]}/reset-password", headers=ORIGIN, json={"password": "new-reader-password"}).status_code == 200
    with database() as db:
        assert db.scalar(select(Session).where(Session.user_id == users[1])) is None
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, reader_cookie)
    assert client.get("/auth/me").status_code == 401
    assert login(client, "reader", "reader-secret-123").status_code == 401
    assert login(client, "reader", "new-reader-password").status_code == 200
    client.cookies.clear()
    assert login(client).status_code == 200
    assert client.post(f"/admin/users/{users[1]}/disable", headers=ORIGIN).status_code == 200
    assert login(client, "reader", "new-reader-password").status_code == 401


def test_password_policy_and_username_validation(client, users):
    assert login(client).status_code == 200
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "bad name", "password": "long-password-123"}).status_code == 422
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "valid", "password": "short"}).status_code == 422
    assert client.post(f"/admin/users/{users[1]}/reset-password", headers=ORIGIN, json={"password": "short"}).status_code == 422


def test_auth_and_admin_body_limit(client, users):
    oversized = b"x" * (16 * 1024 + 1)
    assert client.post("/auth/login", content=oversized, headers=ORIGIN).status_code == 413
    assert client.post("/auth/login", content=iter([b"x" * 8192, b"x" * 8193]), headers=ORIGIN).status_code == 413
    assert client.post("/auth/logout", content=oversized, headers=ORIGIN).status_code == 413
    assert login(client).status_code == 200
    assert client.post("/admin/users", content=oversized, headers=ORIGIN).status_code == 413
    assert client.post(f"/admin/users/{users[1]}/disable", content=oversized, headers=ORIGIN).status_code == 413
    assert client.post(f"/admin/users/{users[1]}/reset-password", content=oversized, headers=ORIGIN).status_code == 413
    assert client.post("/admin/users", headers=ORIGIN, json={"username": "new", "password": "long-password-123"}).status_code == 201


def test_unknown_admin_paths_have_no_auth_body_limit(client):
    oversized = b"x" * (16 * 1024 + 1)
    assert client.post("/admin/unknown", content=oversized, headers=ORIGIN).status_code == 404
