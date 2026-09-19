from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import language_id, telegram_bot
from app.config import Settings
from app.models import TelegramHistory


class FakeModel:
    def __init__(self, labels=("__label__en", "__label__de"), probabilities=(0.8, 0.1)):
        self.labels = labels
        self.probabilities = probabilities
        self.calls = []

    def predict(self, text, *, k):
        self.calls.append((text, k))
        return self.labels, self.probabilities


@pytest.mark.parametrize("text,labels,probabilities,expected", [
    ("What documents support PDF?", ("__label__en", "__label__de"), (0.70, 0.20), "en"),
    ("What documents support PDF?", ("__label__en", "__label__de"), (0.59, 0.01), "other"),
    ("What documents support PDF?", ("__label__en", "__label__de"), (0.80, 0.56), "other"),
    ("Что такое альфа?", ("__label__ru", "__label__uk"), (0.65, 0.30), "ru"),
    ("Show me the PDF documents", ("__label__de", "__label__en"), (0.16, 0.15), "en"),
    ("Show me the PDF documents", ("__label__de", "__label__en"), (0.61, 0.02), "other"),
    ("Show documents", ("__label__de", "__label__en"), (0.16, 0.15), "other"),
])
def test_probability_gate_and_narrow_imperative(monkeypatch, text, labels, probabilities, expected):
    model = FakeModel(labels, probabilities)
    monkeypatch.setattr(language_id, "load_language_model", lambda: model)

    assert language_id.question_language(text) == expected
    assert model.calls == [(text, 2)]


@pytest.mark.parametrize("text,expected", [
    ("PDF?", "unclear"), ("123", "unclear"),
    ("document", "other"), ("hi", "other"), ("will", "other"),
])
def test_short_guard_before_prediction(monkeypatch, text, expected):
    model = FakeModel()
    monkeypatch.setattr(language_id, "load_language_model", lambda: model)

    assert language_id.question_language(text) == expected
    assert model.calls == []


def test_bundled_model_integrity_and_load_once():
    language_id.load_language_model.cache_clear()
    first = language_id.load_language_model()
    assert first is language_id.load_language_model()
    assert language_id.question_language("Какие документы доступны?") == "ru"


@pytest.mark.parametrize("payload", [None, "повреждена".encode()])
def test_missing_or_corrupt_model_fails_closed_before_history(database, monkeypatch, tmp_path, payload):
    path = tmp_path / "lid.176.ftz"
    if payload is not None:
        path.write_bytes(payload)
    language_id.load_language_model.cache_clear()
    monkeypatch.setattr(language_id, "MODEL_PATH", path)
    monkeypatch.setattr(telegram_bot, "read_history", lambda *_args: pytest.fail("История не должна читаться"))
    monkeypatch.setattr(telegram_bot, "generate_answer", lambda *_args, **_kwargs: pytest.fail("Поиск не должен запускаться"))
    update = {
        "update_id": 1,
        "message": {
            "from": {"id": 101, "language_code": "en"},
            "chat": {"id": 101, "type": "private"},
            "text": "What documents support PDF?",
        },
    }
    api = SimpleNamespace(send_message=lambda *_args: pytest.fail("Отправки не должно быть"))

    with pytest.raises(language_id.LanguageModelError):
        telegram_bot.process_update(update, {101}, database, Settings(database_url="sqlite+pysqlite://"), api)
    assert telegram_bot.read_offset(database) == 0
    with database() as db:
        assert db.scalars(select(TelegramHistory)).all() == []
