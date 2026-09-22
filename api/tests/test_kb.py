from pathlib import Path

import pytest

from app import kb
from app.config import Settings


def test_long_line_is_split_without_losing_text():
    original = b"x" * 100_000

    chunks = kb.extract_chunks(original, "txt")

    assert b"".join(chunk.text.encode() for chunk in chunks) == original
    assert all(1 <= len(chunk.text) <= 1200 for chunk in chunks)
    assert all(chunk.line_start == chunk.line_end == 1 for chunk in chunks)


def test_many_blank_lines_do_not_rescan_buffer(monkeypatch):
    def reject_sum(_values):
        pytest.fail("Повторное суммирование длины буфера")

    monkeypatch.setattr(kb, "sum", reject_sum, raising=False)
    chunks = kb.extract_chunks(b"\n" * 20_000 + b"Useful", "txt")

    assert len(chunks) == 1
    assert chunks[0] == kb.ExtractedChunk("Useful", line_start=20_001, line_end=20_001)


def test_each_chunk_points_to_lines_with_content():
    original = b"First\n\nSecond\n" + b"A" * 1190 + b"\n\n" + b"B" * 700 + b"\n" + b"C" * 1300

    chunks = kb.extract_chunks(original, "txt")

    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 3), (4, 4), (6, 6), (7, 7), (7, 7)]
    assert [chunk.text for chunk in chunks] == ["First Second", "A" * 1190, "B" * 700, "C" * 1200, "C" * 100]


def test_markdown_headings_are_hard_chunk_boundaries():
    original = (
        "Введение\n\n"
        "# Первый раздел\nПервая строка\n"
        "```text\n# Не заголовок\n```\n"
        "## Второй раздел\nВторая строка\n"
        f"{'x' * 1201}\n"
        "### Третий раздел\nТретья строка\n"
    ).encode()

    chunks = kb.extract_chunks(original, "md")

    assert [chunk.text for chunk in chunks[:4]] == [
        "Введение",
        "# Первый раздел Первая строка ```text # Не заголовок ```",
        "## Второй раздел Вторая строка",
        "x" * 1200,
    ]
    assert chunks[4].text == "x"
    assert chunks[5].text == "### Третий раздел Третья строка"
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [
        (1, 1), (3, 7), (8, 9), (10, 10), (10, 10), (11, 12),
    ]
    assert all(len(chunk.text) <= kb.CHUNK_CHAR_LIMIT for chunk in chunks)


def test_markdown_fence_with_info_text_does_not_close_code_block():
    original = (
        "# Раздел\n"
        "```text\n"
        "```markdown\n"
        "## Заголовок внутри кода\n"
        "```   \n"
        "## Следующий раздел\n"
        "Текст\n"
    ).encode()

    chunks = kb.extract_chunks(original, "md")

    assert [chunk.text for chunk in chunks] == [
        "# Раздел ```text ```markdown ## Заголовок внутри кода ```",
        "## Следующий раздел Текст",
    ]
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 5), (6, 7)]


def test_repository_readme_first_chunk_is_project_overview():
    readme = Path(__file__).resolve().parents[2] / "README.md"

    chunks = kb.extract_chunks(readme.read_bytes(), "md")

    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 3
    assert chunks[0].text.startswith("# UralDocs Внутренняя база знаний")
    assert all(len(chunk.text) <= kb.CHUNK_CHAR_LIMIT for chunk in chunks)
    headings = [line.strip() for line in readme.read_text().splitlines() if line.startswith("#")]
    assert all(any(chunk.text.startswith(heading) for chunk in chunks) for heading in headings)


def test_config_signature_includes_index_format_version(monkeypatch):
    config = Settings(database_url="sqlite+pysqlite://", kb_mode="demo")
    assert kb.INDEX_FORMAT_VERSION == 3
    current = kb.config_signature(config)

    monkeypatch.setattr(kb, "INDEX_FORMAT_VERSION", 2)

    assert kb.config_signature(config) != current
