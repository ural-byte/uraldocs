import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, local
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import cli, main as auth
from app.models import Base, Session, User
from app.security import hash_password, verify_password


@pytest.fixture
def postgres_database():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Для проверки блокировки строк задайте TEST_POSTGRES_URL")

    schema = f"uraldocs_test_{uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema},public"})
    try:
        Base.metadata.create_all(engine, checkfirst=False)
        yield engine, sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


@pytest.mark.parametrize("reset_kind", ["api", "cli"])
def test_login_and_reset_are_serialized(postgres_database, monkeypatch, reset_kind):
    engine, factory = postgres_database
    with factory() as db:
        admin = User(username="admin", role="admin", password_hash=hash_password("admin-old-password"), is_active=True)
        reader = User(username="reader", role="user", password_hash=hash_password("reader-old-password"), is_active=True)
        db.add_all([admin, reader])
        db.commit()
        admin_id, reader_id = admin.id, reader.id

    target_id = reader_id if reset_kind == "api" else admin_id
    username = "reader" if reset_kind == "api" else "admin"
    old_password = f"{username}-old-password"
    new_password = f"{username}-new-password"
    login_paused = Event()
    allow_login = Event()
    reset_started = Event()
    reset_at_lock = Event()
    original_verify = auth.verify_password

    def pause_after_verification(password_hash, password):
        valid = original_verify(password_hash, password)
        if password == old_password and valid:
            login_paused.set()
            if not allow_login.wait(10):
                raise TimeoutError("Вход не был освобождён")
        return valid

    def observe_reset_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
        if reset_started.is_set() and "FOR UPDATE" in statement.upper():
            reset_at_lock.set()

    monkeypatch.setattr(auth, "verify_password", pause_after_verification)
    event.listen(engine, "before_cursor_execute", observe_reset_lock)

    def do_login():
        with factory() as db:
            response = Response()
            auth.login(auth.LoginRequest(username=username, password=old_password), response, db)
            assert "uraldocs_session" in response.headers["set-cookie"]

    def do_reset():
        reset_started.set()
        if reset_kind == "api":
            with factory() as db:
                actor = db.get(User, admin_id)
                auth.reset_password(target_id, auth.PasswordReset(password=new_password), actor, db)
        else:
            monkeypatch.setattr(cli, "SessionLocal", factory)
            monkeypatch.setattr(sys, "argv", ["uraldocs-admin", "reset-admin", username])
            monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: new_password)
            assert cli.main() == 0

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                login_future = executor.submit(do_login)
                assert login_paused.wait(10)
                reset_future = executor.submit(do_reset)
                assert reset_at_lock.wait(10)
                with pytest.raises(TimeoutError):
                    reset_future.result(timeout=0.5)
                allow_login.set()
                login_future.result(timeout=10)
                reset_future.result(timeout=10)
            finally:
                allow_login.set()
    finally:
        allow_login.set()
        event.remove(engine, "before_cursor_execute", observe_reset_lock)

    with factory() as db:
        user = db.get(User, target_id)
        assert verify_password(user.password_hash, new_password)
        assert not verify_password(user.password_hash, old_password)
        assert db.scalars(select(Session).where(Session.user_id == target_id)).all() == []


def test_parallel_disable_keeps_one_active_admin(postgres_database, monkeypatch):
    engine, factory = postgres_database
    with factory() as db:
        first = User(username="first", role="admin", password_hash=hash_password("first-password-123"), is_active=True)
        second = User(username="second", role="admin", password_hash=hash_password("second-password-123"), is_active=True)
        db.add_all([first, second])
        db.commit()
        first_id = first.id

    first_paused = Event()
    allow_first = Event()
    second_at_lock = Event()
    second_thread = local()
    original_revoke = cli.revoke_sessions

    def pause_first(db, user_id):
        original_revoke(db, user_id)
        if user_id == first_id:
            first_paused.set()
            if not allow_first.wait(10):
                raise TimeoutError("Отключение не было освобождено")

    def observe_second_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
        if getattr(second_thread, "active", False) and "FOR UPDATE" in statement.upper():
            second_at_lock.set()

    monkeypatch.setattr(cli, "SessionLocal", factory)
    monkeypatch.setattr(cli, "revoke_sessions", pause_first)
    event.listen(engine, "before_cursor_execute", observe_second_lock)

    def do_first():
        return cli.main()

    def do_second():
        second_thread.active = True
        return cli.main()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                monkeypatch.setattr(sys, "argv", ["uraldocs-admin", "disable-admin", "first"])
                first_future = executor.submit(do_first)
                assert first_paused.wait(10)
                monkeypatch.setattr(sys, "argv", ["uraldocs-admin", "disable-admin", "second"])
                second_future = executor.submit(do_second)
                assert second_at_lock.wait(10)
                with pytest.raises(TimeoutError):
                    second_future.result(timeout=0.5)
                allow_first.set()
                assert first_future.result(timeout=10) == 0
                assert second_future.result(timeout=10) == 1
            finally:
                allow_first.set()
    finally:
        event.remove(engine, "before_cursor_execute", observe_second_lock)

    with factory() as db:
        assert [(user.username, user.is_active) for user in db.scalars(select(User).order_by(User.id))] == [
            ("first", False),
            ("second", True),
        ]
