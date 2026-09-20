"""Последовательно доставляет ответы Telegram и хранит отдельную историю канала."""

import logging
import time
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.chat import AnswerResult, ChatError, HistoryPair, generate_answer
from app.config import Settings, settings
from app.db import SessionLocal
from app.language_id import load_language_model, question_language
from app.models import TelegramCursor, TelegramHistory, TelegramLanguagePreference

logger = logging.getLogger("uraldocs.telegram")
Language = Literal["ru", "en"]
MAX_MESSAGE_CHARS = 3900
LANGUAGE_BOUNDARY = "Поддерживаются вопросы на русском и английском. / Please ask in Russian or English."
PROFILE_UNKNOWN = "Язык вопроса и профиля не определён. Напишите по-русски или по-английски. / Language unclear. Please ask in Russian or English."
LANGUAGE_CONFIRMED = {"ru": "Язык ответов: русский.", "en": "Answer language: English."}


class TelegramError(Exception):
    pass


class TelegramPermanentDeliveryError(TelegramError):
    pass


def allowed_ids(config: Settings) -> set[int]:
    values = [part.strip() for part in config.telegram_allowed_ids.split(",")]
    if not values or any(not value.isdecimal() or int(value) <= 0 for value in values):
        raise ValueError("Задайте TELEGRAM_ALLOWED_IDS как список положительных ID через запятую")
    return {int(value) for value in values}


def profile_language(code: object) -> Language | None:
    if not isinstance(code, str):
        return None
    primary = code.lower().split("-", 1)[0].split("_", 1)[0]
    return primary if primary in ("ru", "en") else None


def message_language(text: str, profile_code: object) -> Language | Literal["other"] | None:
    detected = question_language(text)
    return profile_language(profile_code) if detected == "unclear" else detected


def split_message(text: str) -> list[str]:
    parts = []
    rest = text.strip()
    while len(rest) > MAX_MESSAGE_CHARS:
        cut = rest.rfind("\n", 0, MAX_MESSAGE_CHARS + 1)
        if cut < MAX_MESSAGE_CHARS // 2:
            cut = rest.rfind(" ", 0, MAX_MESSAGE_CHARS + 1)
        if cut < MAX_MESSAGE_CHARS // 2:
            cut = MAX_MESSAGE_CHARS
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


def format_answer(result: AnswerResult, language: Language, config: Settings) -> str:
    if not result.sources:
        return result.text
    heading = "Источники" if language == "ru" else "Sources"
    lines = [result.text, "", f"{heading}:"]
    for number, source in enumerate(result.sources, start=1):
        location = (
            (f"стр. {source.page_number}" if language == "ru" else f"page {source.page_number}")
            if source.page_number is not None else
            (f"строки {source.line_start}–{source.line_end}" if language == "ru" else f"lines {source.line_start}–{source.line_end}")
        )
        marker = f"[{source.citation_id}] " if source.citation_id else ""
        document = "документ" if language == "ru" else "document"
        lines.append(f"{number}. {marker}{source.filename} ({document} #{source.document_id}, {location})")
        if result.kind == "demo":
            excerpt = "Выдержка" if language == "ru" else "Excerpt"
            lines.append(f"{excerpt}: {source.text[:config.chat_max_excerpt_chars]}")
    return "\n".join(lines)


class TelegramApi:
    def __init__(self, token: str, client: httpx.Client):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = client

    def _call(self, method: str, payload: dict) -> object:
        try:
            response = self.client.post(f"{self.base_url}/{method}", json=payload)
        except (httpx.RequestError, httpx.InvalidURL):
            raise TelegramError("Не удалось связаться с Telegram API") from None
        if response.status_code != 200:
            if method == "sendMessage" and response.status_code in (400, 403):
                raise TelegramPermanentDeliveryError(
                    f"Telegram окончательно отклонил доставку: HTTP {response.status_code}"
                )
            raise TelegramError(f"Telegram API вернул HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise TelegramError("Telegram API вернул некорректный JSON") from None
        if not isinstance(body, dict) or body.get("ok") is not True or "result" not in body:
            raise TelegramError("Telegram API отклонил запрос")
        return body["result"]

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        result = self._call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]})
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise TelegramError("Telegram API вернул некорректные обновления")
        return result

    def send_message(self, chat_id: int, text: str) -> None:
        result = self._call("sendMessage", {"chat_id": chat_id, "text": text})
        if not isinstance(result, dict) or type(result.get("message_id")) is not int:
            raise TelegramError("Telegram API не подтвердил отправку сообщения")


def read_offset(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        cursor = db.get(TelegramCursor, 1)
        return cursor.next_update_id if cursor else 0


def read_history(factory: sessionmaker[Session], telegram_id: int) -> list[HistoryPair]:
    with factory() as db:
        rows = db.scalars(
            select(TelegramHistory).where(TelegramHistory.telegram_id == telegram_id)
            .order_by(TelegramHistory.update_id.desc()).limit(3)
        ).all()
        return [HistoryPair(row.question, row.answer) for row in reversed(rows)]


def read_preference(factory: sessionmaker[Session], telegram_id: int) -> Language | None:
    with factory() as db:
        preference = db.get(TelegramLanguagePreference, telegram_id)
        return preference.language if preference else None


def advance(factory: sessionmaker[Session], update_id: int, pair: tuple[int, str, str] | None = None,
            preference: tuple[int, Language] | None = None) -> None:
    with factory.begin() as db:
        cursor = db.scalar(select(TelegramCursor).where(TelegramCursor.id == 1).with_for_update())
        if cursor is None:
            cursor = TelegramCursor(id=1, next_update_id=0)
            db.add(cursor)
            db.flush()
        if update_id < cursor.next_update_id:
            return
        if preference is not None:
            telegram_id, language = preference
            selected = db.get(TelegramLanguagePreference, telegram_id)
            if selected is None:
                db.add(TelegramLanguagePreference(telegram_id=telegram_id, language=language))
            else:
                selected.language = language
        if pair is not None:
            telegram_id, question, answer = pair
            db.add(TelegramHistory(telegram_id=telegram_id, update_id=update_id, question=question, answer=answer))
            db.flush()
            older = db.scalars(
                select(TelegramHistory).where(TelegramHistory.telegram_id == telegram_id)
                .order_by(TelegramHistory.update_id.desc()).offset(3)
            ).all()
            for row in older:
                db.delete(row)
        cursor.next_update_id = update_id + 1


def _deliver(api: TelegramApi, chat_id: int, text: str) -> None:
    for part in split_message(text):
        api.send_message(chat_id, part)


def process_update(update: dict, allowed: set[int], factory: sessionmaker[Session], config: Settings,
                   api: TelegramApi) -> None:
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0:
        raise TelegramError("Telegram API вернул обновление без корректного ID")
    message = update.get("message")
    sender = message.get("from") if isinstance(message, dict) else None
    chat = message.get("chat") if isinstance(message, dict) else None
    sender_id = sender.get("id") if isinstance(sender, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if (
        type(sender_id) is not int or sender_id not in allowed or not isinstance(chat, dict)
        or chat.get("type") != "private" or type(chat_id) is not int or chat_id != sender_id
    ):
        advance(factory, update_id)
        return
    question = message.get("text")
    if not isinstance(question, str) or not question.strip():
        advance(factory, update_id)
        return
    if question.strip() in ("/ru", "/en"):
        selected_language: Language = question.strip()[1:]
        _deliver(api, chat_id, LANGUAGE_CONFIRMED[selected_language])
        advance(factory, update_id, preference=(sender_id, selected_language))
        return
    language = read_preference(factory, sender_id) or message_language(question, sender.get("language_code"))
    if language == "other":
        _deliver(api, chat_id, LANGUAGE_BOUNDARY)
        advance(factory, update_id)
        return
    if language is None:
        _deliver(api, chat_id, PROFILE_UNKNOWN)
        advance(factory, update_id)
        return
    history = read_history(factory, sender_id)
    try:
        result = generate_answer(factory, question, history, config, language=language)
    except ChatError as exc:
        error = (
            "Вопрос пуст или слишком длинный." if language == "ru" else "The question is empty or too long."
        ) if exc.status_code == 422 else (
            "Сервис ответа недоступен. Повторите вопрос позже." if language == "ru" else
            "The answer service is unavailable. Please try again later."
        )
        _deliver(api, chat_id, error)
        advance(factory, update_id)
        return
    _deliver(api, chat_id, format_answer(result, language, config))
    advance(factory, update_id, (sender_id, question.strip(), result.text))


def run_once(api: TelegramApi, allowed: set[int], factory: sessionmaker[Session], config: Settings) -> int:
    offset = read_offset(factory)
    updates = api.get_updates(offset, config.telegram_poll_timeout_seconds)
    ordered = sorted(updates, key=lambda item: item.get("update_id", -1) if type(item.get("update_id")) is int else -1)
    for update in ordered:
        update_id = update.get("update_id")
        if type(update_id) is not int or update_id < 0:
            raise TelegramError("Telegram API вернул обновление без корректного ID")
        if update_id < offset:
            continue
        try:
            process_update(update, allowed, factory, config, api)
        except TelegramPermanentDeliveryError as exc:
            logger.warning("Обновление %s пропущено после окончательного отказа доставки: %s", update_id, exc)
            advance(factory, update_id)
        offset = update_id + 1
    return len(ordered)


def main() -> None:
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not settings.telegram_bot_token:
        raise ValueError("Задайте TELEGRAM_BOT_TOKEN для запуска Telegram-бота")
    allowed = allowed_ids(settings)
    load_language_model()
    timeout = httpx.Timeout(settings.telegram_poll_timeout_seconds + 10)
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        api = TelegramApi(settings.telegram_bot_token, client)
        while True:
            try:
                run_once(api, allowed, SessionLocal, settings)
            except TelegramError as exc:
                logger.warning("Ошибка Telegram API: %s", exc)
                time.sleep(2)
            except Exception:
                logger.exception("Ошибка обработки Telegram; повторная попытка")
                time.sleep(2)


if __name__ == "__main__":
    main()
