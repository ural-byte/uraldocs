"""Операторские команды для управления административными учётными записями."""

import argparse
import getpass
import re
import sys

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import User
from app.security import hash_password, revoke_sessions, valid_password

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,100}$")


def ask_password() -> str:
    password = getpass.getpass("Новый пароль: ")
    if not valid_password(password):
        raise ValueError("Пароль должен содержать от 12 до 1024 символов")
    if password != getpass.getpass("Повторите пароль: "):
        raise ValueError("Пароли не совпадают")
    return password


def ensure_username(username: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Логин: 1–100 символов, латинские буквы, цифры, _, . и -")


def main() -> int:
    parser = argparse.ArgumentParser(description="Управление администраторами UralDocs")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Создать первого admin и demo user")
    bootstrap.add_argument("--admin", default="admin")
    bootstrap.add_argument("--demo-user", default="demo")
    for name in ("create-admin", "disable-admin", "reset-admin"):
        commands.add_parser(name).add_argument("username")
    args = parser.parse_args()

    try:
        if args.command == "bootstrap":
            ensure_username(args.admin)
            ensure_username(args.demo_user)
            if args.admin == args.demo_user:
                raise ValueError("Логины должны отличаться")
            with SessionLocal() as db:
                if db.scalar(select(func.count()).select_from(User).where(User.role == "admin")):
                    raise ValueError("Первый admin уже существует; используйте create-admin")
                if db.scalar(select(User).where(User.username.in_([args.admin, args.demo_user]))):
                    raise ValueError("Один из логинов уже занят")
                print(f"Пароль администратора {args.admin}:")
                admin_password = ask_password()
                print(f"Пароль пользователя {args.demo_user}:")
                demo_password = ask_password()
                db.add_all([
                    User(username=args.admin, role="admin", password_hash=hash_password(admin_password), is_active=True),
                    User(username=args.demo_user, role="user", password_hash=hash_password(demo_password), is_active=True),
                ])
                db.commit()
            print("Учётные записи созданы")
        elif args.command == "create-admin":
            ensure_username(args.username)
            with SessionLocal() as db:
                if db.scalar(select(User).where(User.username == args.username)):
                    raise ValueError("Логин занят")
                password = ask_password()
                db.add(User(username=args.username, role="admin", password_hash=hash_password(password), is_active=True))
                db.commit()
            print("Администратор создан")
        else:
            with SessionLocal() as db:
                if args.command == "reset-admin":
                    existing = db.scalar(select(User).where(User.username == args.username))
                    if existing is None or existing.role != "admin":
                        raise ValueError("Администратор не найден")
                    db.rollback()
                    password_hash = hash_password(ask_password())
                else:
                    active_admins = db.scalars(
                        select(User)
                        .where(User.role == "admin", User.is_active.is_(True))
                        .order_by(User.id)
                        .with_for_update()
                    ).all()
                user = db.scalar(select(User).where(User.username == args.username).with_for_update())
                if user is None or user.role != "admin":
                    raise ValueError("Администратор не найден")
                if args.command == "disable-admin":
                    if user.is_active and len(active_admins) <= 1:
                        raise ValueError("Нельзя отключить последнего активного администратора")
                    user.is_active = False
                    revoke_sessions(db, user.id)
                    db.commit()
                    print("Администратор отключён")
                else:
                    user.password_hash = password_hash
                    revoke_sessions(db, user.id)
                    db.commit()
                    print("Пароль администратора обновлён")
    except (ValueError, IntegrityError) as error:
        print(f"Ошибка: {error if isinstance(error, ValueError) else 'Логин занят'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
