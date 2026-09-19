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
MIN_RU_PROBABILITY = 0.60
MIN_EN_PROBABILITY = 0.38
MIN_MARGIN = 0.25
TECHNICAL_TERMS = {"AI", "API", "CSV", "DOCX", "HTTP", "ID", "JSON", "KB", "MD", "PDF", "SQL", "TXT", "UI", "URL"}
IMPERATIVE_VERBS = {"show", "list", "summarize", "describe", "explain", "find"}
OBJECT_DETERMINERS = {"the", "a", "an", "all", "these", "those"}
RU_QUESTION_WORDS = {"что", "какие", "кто", "когда", "сколько"}
RU_IMPERATIVES = {"покажи", "расскажи", "объясни", "найди"}
RU_PREDICATE_ENDINGS = ("ет", "ит", "ют", "ут", "ят", "ат", "ешь", "ишь", "ем", "им", "ете", "ите", "ал", "ала", "али", "ен", "на", "но", "ны")


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


def _russian_infinitive(word: str) -> bool:
    return len(word) >= 4 and word.endswith(("ть", "ти", "чь"))


def _russian_construction(words: list[str]) -> bool:
    if len(words) < 2:
        return False
    if words[0] in RU_QUESTION_WORDS and any(len(word) > 1 for word in words[1:]):
        return True
    if words[0] in {"как", "где"}:
        verb_index = 2 if len(words) > 1 and words[1] in {"мне", "нам"} else 1
        return len(words) > verb_index and _russian_infinitive(words[verb_index])
    if words[0] == "можно":
        verb_index = 2 if len(words) > 1 and words[1] == "ли" else 1
        return len(words) > verb_index and _russian_infinitive(words[verb_index])
    if len(words) >= 3 and words[1] == "ли" and (words[0] == "есть" or words[0].endswith(RU_PREDICATE_ENDINGS)):
        return True
    if words[0] in RU_IMPERATIVES:
        return len(words) >= (3 if words[1] == "о" else 2)
    return len(words) >= 3 and words[:2] == ["о", "чём"]


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
    margin = top_probability - second_probability
    if top_language == "en" and top_probability >= MIN_EN_PROBABILITY and margin >= MIN_MARGIN:
        return "en"
    if top_language == "ru" and top_probability >= MIN_RU_PROBABILITY and margin >= MIN_MARGIN and _russian_construction(words):
        return "ru"
    if top_probability < MIN_RU_PROBABILITY and _english_imperative(words):
        return "en"
    return "other"
