import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Base, TelegramCursor, TelegramHistory, TelegramLanguagePreference
from app.telegram_bot import advance, read_history, read_offset, read_preference


@pytest.fixture
def postgres_telegram_database():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Для проверки Telegram в PostgreSQL задайте TEST_POSTGRES_URL")
    schema = f"uraldocs_telegram_{uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema},public"})
    try:
        Base.metadata.create_all(engine, checkfirst=False)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def test_postgres_bigint_cursor_and_isolated_history(postgres_telegram_database):
    factory = postgres_telegram_database
    large_id = 5_000_000_000
    advance(factory, large_id, (large_id, "вопрос", "ответ"))
    advance(factory, large_id + 1, (large_id + 1, "question", "answer"))

    assert read_offset(factory) == large_id + 2
    assert [pair.question for pair in read_history(factory, large_id)] == ["вопрос"]
    assert [pair.question for pair in read_history(factory, large_id + 1)] == ["question"]
    with factory() as db:
        assert db.get(TelegramCursor, 1).next_update_id == large_id + 2
        assert len(db.scalars(select(TelegramHistory)).all()) == 2
        columns = dict(db.execute(text("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'telegram_history'
        """)).all())
        assert columns["telegram_id"] == "bigint"
        assert columns["update_id"] == "bigint"


def test_postgres_preference_isolated_and_updated_with_cursor(postgres_telegram_database):
    factory = postgres_telegram_database
    advance(factory, 1, preference=(101, "ru"))
    advance(factory, 2, preference=(202, "en"))
    assert read_preference(factory, 101) == "ru"
    assert read_preference(factory, 202) == "en"
    advance(factory, 3, preference=(101, "en"))
    assert read_preference(factory, 101) == "en"
    assert read_preference(factory, 202) == "en"
    with factory() as db:
        assert len(db.scalars(select(TelegramLanguagePreference)).all()) == 2
