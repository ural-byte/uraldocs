import hashlib
import io
import math
import re
from dataclasses import dataclass

from pypdf import PdfReader

from app.config import Settings


@dataclass(frozen=True)
class ExtractedChunk:
    text: str
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None


def config_signature(config: Settings) -> str:
    source = config.kb_mode
    if config.kb_mode == "real_ai":
        source += f"\0{config.ai_base_url.rstrip('/')}\0{config.ai_embedding_model}"
    return hashlib.sha256(source.encode()).hexdigest()


def _split_text(text: str, limit: int = 1200) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    pieces = []
    while text:
        if len(text) <= limit:
            pieces.append(text)
            break
        cut = text.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    return pieces


def extract_chunks(original: bytes, file_type: str) -> list[ExtractedChunk]:
    if file_type == "pdf":
        try:
            reader = PdfReader(io.BytesIO(original))
            chunks = [
                ExtractedChunk(part, page_number=number)
                for number, page in enumerate(reader.pages, start=1)
                for part in _split_text(page.extract_text() or "")
            ]
        except Exception as exc:
            raise ValueError("Не удалось прочитать PDF: файл повреждён или защищён") from exc
        if not chunks:
            raise ValueError("PDF не содержит извлекаемого текста; сканы без текстового слоя не поддерживаются")
        return chunks

    try:
        lines = original.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Текстовый файл должен быть в кодировке UTF-8") from exc
    chunks: list[ExtractedChunk] = []
    buffer: list[str] = []
    start = 1

    def flush(end: int) -> None:
        if buffer:
            for part in _split_text("\n".join(buffer)):
                chunks.append(ExtractedChunk(part, line_start=start, line_end=end))
            buffer.clear()

    for number, line in enumerate(lines, start=1):
        if buffer and sum(len(value) for value in buffer) + len(line) > 1200:
            flush(number - 1)
            start = number
        if not buffer:
            start = number
        buffer.append(line)
    flush(len(lines))
    if not chunks:
        raise ValueError("Документ не содержит текста для индексирования")
    return chunks


def validate_embeddings(vectors: list[list[float]], expected_count: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise ValueError("API embeddings вернул неверное число векторов")
    dimension = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
    if not 1 <= dimension <= 16000:
        raise ValueError("API embeddings вернул неверную размерность")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise ValueError("Размерности embeddings различаются")
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in vector):
            raise ValueError("Embedding содержит некорректные числа")
    return vectors
