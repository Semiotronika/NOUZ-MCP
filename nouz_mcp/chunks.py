"""Deterministic Markdown chunking primitives for retrieval pipelines."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class _Block:
    start: int
    end: int
    text: str
    heading: str


def chunk_markdown(
    text: str,
    *,
    source_id: str = "",
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[dict[str, Any]]:
    """Split Markdown into stable, embedding-ready chunks.

    This is a pure low-level primitive: it does not read files, write SQLite, or
    call an embedding model. Higher-level retrieval and context tools can build
    on this contract without coupling chunking to a specific workflow.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []

    max_chars = max(1, int(max_chars))
    overlap_chars = max(0, min(int(overlap_chars), max_chars // 2))
    base_chunks = _make_base_chunks(normalized, max_chars=max_chars)

    chunks: list[dict[str, Any]] = []
    for index, block in enumerate(base_chunks):
        prefix_start = max(0, block.start - overlap_chars) if index else block.start
        overlap = block.start - prefix_start
        chunk_text = normalized[prefix_start:block.end]
        digest = hashlib.sha1(
            f"{source_id}\0{index}\0{block.start}\0{block.end}\0{chunk_text}".encode("utf-8")
        ).hexdigest()[:12]
        chunks.append(
            {
                "id": f"chunk:{digest}",
                "source_id": source_id,
                "index": index,
                "start_char": block.start,
                "end_char": block.end,
                "overlap_chars": overlap,
                "char_count": len(chunk_text),
                "heading": block.heading,
                "text": chunk_text,
            }
        )
    return chunks


def _make_base_chunks(text: str, *, max_chars: int) -> list[_Block]:
    chunks: list[_Block] = []
    current_text = ""
    current_start = 0
    current_end = 0
    current_heading = ""

    for block in _markdown_blocks(text):
        if current_text and block.heading and block.heading != current_heading:
            chunks.append(_Block(current_start, current_end, current_text, current_heading))
            current_text = ""

        if len(block.text) > max_chars:
            if current_text:
                chunks.append(_Block(current_start, current_end, current_text, current_heading))
                current_text = ""
            chunks.extend(_split_large_block(block, max_chars=max_chars))
            continue

        if current_text and len(current_text) + len(block.text) > max_chars:
            chunks.append(_Block(current_start, current_end, current_text, current_heading))
            current_text = ""

        if not current_text:
            current_start = block.start
            current_heading = block.heading
        current_text += block.text
        current_end = block.end
        if block.heading:
            current_heading = block.heading

    if current_text:
        chunks.append(_Block(current_start, current_end, current_text, current_heading))
    return chunks


def _markdown_blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    current: list[str] = []
    current_start = 0
    pos = 0
    active_heading = ""
    block_heading = ""

    def flush(end: int) -> None:
        nonlocal current, current_start, block_heading
        if not current:
            return
        block_text = "".join(current)
        if block_text.strip():
            blocks.append(_Block(current_start, end, block_text, block_heading))
        current = []
        block_heading = active_heading

    for line in text.splitlines(keepends=True):
        heading_match = HEADING_RE.match(line.rstrip("\n"))
        is_blank = not line.strip()

        if heading_match:
            flush(pos)
            active_heading = heading_match.group(2).strip()
            block_heading = active_heading
            current_start = pos
            current = [line]
        elif is_blank:
            if current:
                current.append(line)
                pos += len(line)
                flush(pos)
                continue
        else:
            if not current:
                current_start = pos
                block_heading = active_heading
            current.append(line)

        pos += len(line)

    flush(pos)
    return blocks


def _split_large_block(block: _Block, *, max_chars: int) -> list[_Block]:
    pieces: list[_Block] = []
    cursor = block.start
    text_cursor = 0
    while text_cursor < len(block.text):
        slice_text = block.text[text_cursor : text_cursor + max_chars]
        start = cursor
        end = cursor + len(slice_text)
        pieces.append(_Block(start, end, slice_text, block.heading))
        cursor = end
        text_cursor += len(slice_text)
    return pieces
