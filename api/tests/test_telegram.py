import json

import httpx
import pytest
from sqlalchemy import select

from app import telegram_bot as bot
from app.chat import AnswerResult, Candidate, ChatError
from app.config import Settings
from app.kb import config_signature
from app.models import Document, DocumentChunk, TelegramHistory


class FakeTelegram:
    def __init__(self, updates=(), fail_at=None):
        self.updates = list(updates)
        self.sent = []
        self.calls = []
        self.fail_at = fail_at

    def get_updates(self, offset, timeout):
        self.calls.append((offset, timeout))
        return [update for update in self.updates if update["update_id"] >= offset]

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        if self.fail_at == len(self.sent):
            raise bot.TelegramError("сбой доставки")


def update(number, user=101, text="What is alpha?", *, chat_type="private", chat_id=None, profile="en"):
    return {
        "update_id": number,
        "message": {
            "from": {"id": user, "language_code": profile},
            "chat": {"id": user if chat_id is None else chat_id, "type": chat_type},
            "text": text,
        },
    }


def ready_document(database, config):
    with database.begin() as db:
        document = Document(
            filename="guide.txt", file_type="txt", original=b"what is alpha mountain", status="ready",
            generation=1, config_signature=config_signature(config),
        )
        db.add(document)
        db.flush()
        db.add(DocumentChunk(
            document_id=document.id, generation=1, chunk_index=0, text="what is alpha mountain",
            line_start=4, line_end=4,
        ))


def config():
    return Settings(database_url="sqlite+pysqlite://", kb_mode="demo", telegram_allowed_ids="101,202")


def test_allowlist_and_private_gate_before_history_or_answer(database, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Доступ к истории или базе знаний до проверки отправителя")

    monkeypatch.setattr(bot, "read_history", forbidden)
    monkeypatch.setattr(bot, "generate_answer", forbidden)
    api = FakeTelegram([
        update(1, user=303), update(2, chat_type="group"), update(3, chat_id=999),
        {"update_id": 4, "callback_query": {}},
    ])
    bot.run_once(api, {101, 202}, database, config())

    assert bot.read_offset(database) == 5
    assert api.sent == []
    with database() as db:
        assert db.scalars(select(TelegramHistory)).all() == []


def test_order_restart_isolation_and_three_pairs(database):
    settings = config()
    ready_document(database, settings)
    api = FakeTelegram([
        update(4), update(2, user=202), update(1), update(3), update(5),
    ])

    bot.run_once(api, {101, 202}, database, settings)
    assert bot.read_offset(database) == 6
    assert [chat for chat, _ in api.sent] == [101, 202, 101, 101, 101]
    assert len(bot.read_history(database, 101)) == 3
    assert len(bot.read_history(database, 202)) == 1
    assert "no AI answer" in api.sent[0][1]
    assert "Excerpt: what is alpha mountain" in api.sent[0][1]
    assert "guide.txt" in api.sent[0][1] and "lines 4–4" in api.sent[0][1]

    restarted = FakeTelegram(api.updates + [update(6, user=202, text="What is unknown?")])
    bot.run_once(restarted, {101, 202}, database, settings)
    assert restarted.calls[0][0] == 6
    assert bot.read_offset(database) == 7
    assert len(bot.read_history(database, 202)) == 2
    assert "enough information" in restarted.sent[0][1]


def test_second_part_failure_keeps_cursor_and_history(database, monkeypatch):
    monkeypatch.setattr(bot, "generate_answer", lambda *_args, **_kwargs: AnswerResult("answer", "A" * 8000))
    api = FakeTelegram([update(7)], fail_at=2)

    with pytest.raises(bot.TelegramError):
        bot.run_once(api, {101}, database, config())
    assert len(api.sent) == 2
    assert bot.read_offset(database) == 0
    assert bot.read_history(database, 101) == []

    retry = FakeTelegram(api.updates)
    bot.run_once(retry, {101}, database, config())
    assert len(retry.sent) == 3
    assert bot.read_offset(database) == 8
    assert len(bot.read_history(database, 101)) == 1


def test_provider_failure_has_no_pair_or_demo_fallback(database, monkeypatch):
    monkeypatch.setattr(bot, "generate_answer", lambda *_args, **_kwargs: (_ for _ in ()).throw(ChatError(502, "private provider detail")))
    api = FakeTelegram([update(9)])
    bot.run_once(api, {101}, database, config())

    assert bot.read_offset(database) == 10
    assert bot.read_history(database, 101) == []
    assert "unavailable" in api.sent[0][1]
    assert "private provider detail" not in api.sent[0][1]
    assert "Excerpt" not in api.sent[0][1]


@pytest.mark.parametrize("text,profile,expected", [
    ("Что такое альфа?", "en", "ru"),
    ("Как загрузить PDF?", "en", "ru"),
    ("Можно ли импортировать документ?", "en", "ru"),
    ("Поддерживает ли Уралдокс импорт PDF?", "en", "ru"),
    ("Какие документы доступны?", "en", "ru"),
    ("What is alpha?", "ru", "en"),
    ("Does UralDocs support PDF import?", "ru", "en"),
    ("Is PDF supported?", "ru", "en"),
    ("How can I import a document?", "ru", "en"),
    ("How come PDF import fails?", "ru", "en"),
    ("How do I comment on a PDF?", "ru", "en"),
    ("Can I upload a PDF?", "ru", "en"),
    ("Where are the documents?", "ru", "en"),
    ("Which files can I import?", "ru", "en"),
    ("Please summarize the policy", "ru", "en"),
    ("What documents support PDF?", "ru", "en"),
    ("Show me the PDF documents", "ru", "en"),
    ("alpha", "en", "other"),
    ("PDF?", "en", "en"),
    ("PDF?", "ru", "ru"),
    ("PDF?", "de", None),
    ("Ich will ein PDF importieren. Geht das?", "en", "other"),
    ("Hi ha documents disponibles?", "en", "other"),
    ("Hoe maak ik een document?", "en", "other"),
    ("Come funziona importazione PDF?", "en", "other"),
    ("Necesito importar un documento PDF", "en", "other"),
    ("Какво е документ?", "en", "other"),
    ("Как се качва документ?", "en", "other"),
    ("Како ради увоз докумената?", "en", "other"),
    ("Да ли могу да увезем документ?", "en", "other"),
    ("document", "en", "other"),
    ("hi", "en", "other"),
    ("will", "en", "other"),
    ("hola amigo", "en", "other"),
    ("123", "de", None),
])
def test_language_choice(text, profile, expected):
    assert bot.message_language(text, profile) == expected


def test_unsupported_and_unknown_language_do_not_use_answer(database, monkeypatch):
    monkeypatch.setattr(bot, "generate_answer", lambda *_args, **_kwargs: pytest.fail("Лишний вызов поиска"))
    api = FakeTelegram([update(1, text="hola amigo"), update(2, text="123", profile="de")])
    bot.run_once(api, {101}, database, config())
    assert bot.read_offset(database) == 3
    assert api.sent[0][1] == bot.LANGUAGE_BOUNDARY
    assert api.sent[1][1] == bot.PROFILE_UNKNOWN
    assert bot.read_history(database, 101) == []


def test_foreign_questions_do_not_reach_kb_or_history(database, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Вопрос на другом языке не должен обращаться к истории и поиску")

    monkeypatch.setattr(bot, "read_history", forbidden)
    monkeypatch.setattr(bot, "generate_answer", forbidden)
    questions = [
        "Ich will ein PDF importieren. Geht das?", "Hi ha documents disponibles?",
        "Hoe maak ik een document?", "Come funziona importazione PDF?",
        "Necesito importar un documento PDF", "Какво е документ?", "Как се качва документ?",
        "Како ради увоз докумената?", "Да ли могу да увезем документ?",
        "document", "hi", "will",
    ]
    api = FakeTelegram([update(number, text=question, profile="en") for number, question in enumerate(questions, 1)])
    bot.run_once(api, {101}, database, config())

    assert bot.read_offset(database) == len(questions) + 1
    assert api.sent == [(101, bot.LANGUAGE_BOUNDARY)] * len(questions)
    with database() as db:
        assert db.scalars(select(TelegramHistory)).all() == []


def test_question_language_overrides_profile_and_short_query_uses_it(database, monkeypatch):
    observed = []

    def answer(_factory, _question, _history, _config, *, language):
        observed.append(language)
        return AnswerResult("insufficient", "Ответ")

    monkeypatch.setattr(bot, "generate_answer", answer)
    api = FakeTelegram([
        update(1, text="Что такое альфа?", profile="en"),
        update(2, text="What is alpha?", profile="ru"),
        update(3, text="PDF?", profile="en"),
        update(4, text="PDF?", profile="ru"),
        update(5, text="PDF?", profile="de"),
    ])
    bot.run_once(api, {101}, database, config())

    assert observed == ["ru", "en", "en", "ru"]
    assert api.sent[-1] == (101, bot.PROFILE_UNKNOWN)
    assert bot.read_offset(database) == 6
    assert len(bot.read_history(database, 101)) == 3


def test_real_answer_sources_exclude_excerpt():
    source = Candidate(1, 25, 1, "guide.txt", "secret excerpt", 3, None, None, "c1")
    result = AnswerResult("answer", "Answer [c1]", (source,))
    text = bot.format_answer(result, "en", config())
    assert "guide.txt" in text and "page 3" in text and "[c1]" in text
    assert "secret excerpt" not in text


def test_real_ai_sends_english_question_and_history_to_provider(database, embeddings_server):
    settings = Settings(
        database_url="sqlite+pysqlite://", kb_mode="real_ai", ai_base_url=embeddings_server.base_url,
        ai_api_key="private-test-key", ai_embedding_model="test-embedding", ai_chat_model="test-chat",
    )
    with database.begin() as db:
        document = Document(
            filename="guide.txt", file_type="txt", original=b"alpha mountain", status="ready",
            generation=1, config_signature=config_signature(settings),
        )
        db.add(document)
        db.flush()
        db.add(DocumentChunk(
            document_id=document.id, generation=1, chunk_index=0, text="alpha mountain",
            line_start=4, line_end=4, embedding=[1.0, 0.0],
        ))

    def provider(_request):
        if embeddings_server.requests[-1][0].endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        return {"choices": [{"message": {"content": json.dumps({
            "insufficient": False, "answer": "The mountain route is described [c1].", "citation_ids": ["c1"],
        })}}]}

    embeddings_server.payload = provider
    api = FakeTelegram([update(1, text="What is alpha?"), update(2, text="Does alpha have a route?")])
    bot.run_once(api, {101}, database, settings)

    assert bot.read_offset(database) == 3
    assert len(bot.read_history(database, 101)) == 2
    assert all("guide.txt" in sent[1] and "lines 4–4" in sent[1] for sent in api.sent)
    assert all("Excerpt:" not in sent[1] for sent in api.sent)
    chat_requests = [request for request in embeddings_server.requests if request[0].endswith("/chat/completions")]
    assert len(chat_requests) == 2
    assert "in English" in chat_requests[0][2]["messages"][0]["content"]
    first_context = json.loads(chat_requests[0][2]["messages"][1]["content"])
    assert first_context["sources"] == [{"id": "c1", "excerpt": "alpha mountain"}]
    second_context = json.loads(chat_requests[1][2]["messages"][1]["content"])
    assert second_context["history"] == [{
        "question": "What is alpha?", "answer": "The mountain route is described [c1].",
    }]


def test_telegram_client_uses_plain_text_and_validates_delivery():
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content)))
        result = [] if request.url.path.endswith("getUpdates") else {"message_id": 42}
        return httpx.Response(200, json={"ok": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api = bot.TelegramApi("test-token", client)
        assert api.get_updates(5, 25) == []
        api.send_message(101, "plain text [c1]")
    assert requests[0][1] == {"offset": 5, "timeout": 25, "allowed_updates": ["message"]}
    assert requests[1][1] == {"chat_id": 101, "text": "plain text [c1]"}


def test_allowlist_rejects_empty_and_malformed_values():
    with pytest.raises(ValueError):
        bot.allowed_ids(Settings(database_url="sqlite+pysqlite://", telegram_allowed_ids=""))
    assert bot.allowed_ids(config()) == {101, 202}
