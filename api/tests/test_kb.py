import pytest

from app import kb


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
