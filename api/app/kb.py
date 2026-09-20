import hashlib
import io
import math
import re
from dataclasses import dataclass

from pypdf import PdfReader

from app.config import Settings

MAX_FLOAT32 = 3.4028234663852886e38


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
    start = 0
    while start < len(text):
        if len(text) - start <= limit:
            pieces.append(text[start:])
            break
        cut = text.rfind(" ", start, start + limit + 1)
        if cut < start + limit // 2:
            cut = start + limit
        pieces.append(text[start:cut].strip())
        start = cut
        while start < len(text) and text[start] == " ":
            start += 1
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
    buffer_length = 0
    start = 0
    end = 0

    def flush() -> None:
        nonlocal buffer_length
        if buffer:
            chunks.append(ExtractedChunk(" ".join(buffer), line_start=start, line_end=end))
            buffer.clear()
            buffer_length = 0

    for number, line in enumerate(lines, start=1):
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized:
            continue
        if len(normalized) > 1200:
            flush()
            chunks.extend(ExtractedChunk(part, line_start=number, line_end=number) for part in _split_text(normalized))
            continue
        if buffer and buffer_length + 1 + len(normalized) > 1200:
            flush()
        if not buffer:
            start = number
        else:
            buffer_length += 1
        buffer.append(normalized)
        buffer_length += len(normalized)
        end = number
    flush()
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
        if any(type(value) not in (float, int) or abs(value) > MAX_FLOAT32 or not math.isfinite(value) for value in vector):
            raise ValueError("Embedding содержит некорректные числа")
    return vectors
