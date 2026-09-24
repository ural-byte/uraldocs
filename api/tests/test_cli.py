import sys

from sqlalchemy import select

from app import cli
from app.models import User


def run_cli(monkeypatch, args, passwords=()):
    monkeypatch.setattr(sys, "argv", ["uraldocs-admin", *args])
    answers = iter(passwords)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    return cli.main()


def test_bootstrap_and_no_overwrite(monkeypatch, database):
    monkeypatch.setattr(cli, "SessionLocal", database)
    assert run_cli(monkeypatch, ["bootstrap"], ["admin-password-123", "admin-password-123", "demo-password-123", "demo-password-123"]) == 0
    with database() as db:
        assert [(u.username, u.role) for u in db.scalars(select(User).order_by(User.id))] == [("admin", "admin"), ("demo", "user")]
    assert run_cli(monkeypatch, ["bootstrap"]) == 1


def test_admin_operations(monkeypatch, database):
    monkeypatch.setattr(cli, "SessionLocal", database)
    assert run_cli(monkeypatch, ["bootstrap"], ["admin-password-123", "admin-password-123", "demo-password-123", "demo-password-123"]) == 0
    assert run_cli(monkeypatch, ["disable-admin", "admin"]) == 1
    assert run_cli(monkeypatch, ["create-admin", "second"], ["second-password-123", "second-password-123"]) == 0
    assert run_cli(monkeypatch, ["create-admin", "second"]) == 1
    assert run_cli(monkeypatch, ["reset-admin", "second"], ["new-password-123", "new-password-123"]) == 0
    assert run_cli(monkeypatch, ["disable-admin", "second"]) == 0
    with database() as db:
        assert db.scalar(select(User).where(User.username == "second")).is_active is False


def test_cli_rejects_invalid_input(monkeypatch, database):
    monkeypatch.setattr(cli, "SessionLocal", database)
    assert run_cli(monkeypatch, ["bootstrap", "--admin", "bad name"]) == 1
    assert run_cli(monkeypatch, ["bootstrap"], ["short", "short"]) == 1
    assert run_cli(monkeypatch, ["bootstrap"], ["admin-password-123", "different"]) == 1
    with database() as db:
        assert db.scalars(select(User)).all() == []
