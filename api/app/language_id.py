"""Локально определяет язык сообщения Telegram."""

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
MIN_OTHER_PROBABILITY = 0.60
MIN_MARGIN = 0.25


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


def question_language(text: str) -> Literal["ru", "en", "other", "unclear"]:
    model = load_language_model()
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).strip())
    words = re.findall(r"[^\W_]+", normalized.casefold(), re.UNICODE)
    if len(words) < 3:
        return "unclear"

    try:
        labels, probabilities = model.predict(normalized, k=2)
        top_language = labels[0].removeprefix("__label__")
        top_probability, second_probability = float(probabilities[0]), float(probabilities[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise LanguageModelError("Языковая модель вернула некорректный результат") from exc
    if top_probability - second_probability < MIN_MARGIN:
        return "unclear"
    if top_language == "ru" and top_probability >= MIN_RU_PROBABILITY:
        return "ru"
    if top_language == "en" and top_probability >= MIN_EN_PROBABILITY:
        return "en"
    if top_language not in ("ru", "en") and top_probability >= MIN_OTHER_PROBABILITY:
        return "other"
    return "unclear"
