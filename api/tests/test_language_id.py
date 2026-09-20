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
    ("Is PDF supported?", ("__label__en", "__label__hu"), (0.38, 0.13), "en"),
    ("Is PDF supported?", ("__label__en", "__label__hu"), (0.379, 0.01), "unclear"),
    ("What documents support PDF?", ("__label__en", "__label__de"), (0.80, 0.56), "unclear"),
    ("Что такое альфа?", ("__label__ru", "__label__uk"), (0.65, 0.30), "ru"),
    ("Расскажи о документах", ("__label__ru", "__label__uk"), (0.85, 0.05), "ru"),
    ("Ich will ein PDF importieren", ("__label__de", "__label__en"), (0.90, 0.01), "other"),
    ("Hi ha documents disponibles?", ("__label__it", "__label__es"), (0.23, 0.17), "unclear"),
])
def test_model_confidence(monkeypatch, text, labels, probabilities, expected):
    model = FakeModel(labels, probabilities)
    monkeypatch.setattr(language_id, "load_language_model", lambda: model)

    assert language_id.question_language(text) == expected
    assert model.calls == [(text, 2)]


@pytest.mark.parametrize("text", ["PDF?", "123", "document", "hi", "will", "What documents?", "hola amigo"])
def test_short_message_uses_profile_without_prediction(monkeypatch, text):
    model = FakeModel()
    monkeypatch.setattr(language_id, "load_language_model", lambda: model)

    assert language_id.question_language(text) == "unclear"
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
