"""Локально определяет язык полнофразового вопроса до обращения к базе знаний."""

import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

import fasttext

MODEL_PATH = Path(__file__).parent / "data" / "lid.176.ftz"
MODEL_SIZE = 938013
MODEL_SHA256 = "8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83"
MIN_PROBABILITY = 0.60
MIN_MARGIN = 0.25
TECHNICAL_TERMS = {"AI", "API", "CSV", "DOCX", "HTTP", "ID", "JSON", "KB", "MD", "PDF", "SQL", "TXT", "UI", "URL"}
IMPERATIVE_VERBS = {"show", "list", "summarize", "describe", "explain", "find"}
OBJECT_DETERMINERS = {"the", "a", "an", "all", "these", "those"}


class LanguageModelError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_language_model():
    try:
        payload = MODEL_PATH.read_bytes()
        if len(payload) != MODEL_SIZE or hashlib.sha256(payload).hexdigest() != MODEL_SHA256:
            raise LanguageModelError("Файл языковой модели не прошёл проверку целостности")
        return fasttext.load_model(str(MODEL_PATH))
    except (OSError, ValueError) as exc:
        raise LanguageModelError("Языковая модель недоступна") from exc


def _ambiguous_term(text: str, words: list[str]) -> bool:
    if re.fullmatch(r"\d+(?:[.,]\d+)?\??", text):
        return True
    return len(words) == 1 and words[0].upper() in TECHNICAL_TERMS


def _english_imperative(words: list[str]) -> bool:
    return (
        len(words) >= 4
        and words[0] in IMPERATIVE_VERBS
        and words[1] in {"me", "us"}
        and words[2] in OBJECT_DETERMINERS
        and any(any(character.isalpha() for character in word) for word in words[3:])
    )


def question_language(text: str) -> Literal["ru", "en", "other", "unclear"]:
    # Даже для коротких терминов модель должна быть доступна: иначе профиль мог бы открыть доступ к KB.
    model = load_language_model()
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).strip())
    words = re.findall(r"[^\W_]+", normalized.casefold(), re.UNICODE)
    if _ambiguous_term(normalized, words):
        return "unclear"
    if len(words) < 2:
        return "other"

    try:
        labels, probabilities = model.predict(normalized, k=2)
        top_language = labels[0].removeprefix("__label__")
        top_probability, second_probability = float(probabilities[0]), float(probabilities[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise LanguageModelError("Языковая модель вернула некорректный результат") from exc
    if top_language in {"ru", "en"} and top_probability >= MIN_PROBABILITY and top_probability - second_probability >= MIN_MARGIN:
        return top_language
    if top_probability < MIN_PROBABILITY and _english_imperative(words):
        return "en"
    return "other"
