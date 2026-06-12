from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .artifacts import content_item_to_text, load_json_if_exists, normalize_ws, resolve_artifact


def estimate_tokens(text: str) -> int:
    # Rough heuristic for mixed Chinese/English without extra dependencies.
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = max(0, len(text) - cjk)
    return int(cjk / 1.6 + other / 4)


def sliding_window_text(text: str, *, max_chars: int = 1800, overlap_chars: int = 180) -> list[str]:
    text = normalize_ws(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            # Try to stop on a Chinese/English sentence boundary.
            window = text[start:end]
            cut = max(
                window.rfind("。"),
                window.rfind("；"),
                window.rfind("\n"),
                window.rfind("."),
                window.rfind(";"),
            )
            if cut > max_chars * 0.55:
                end = start + cut + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return chunks


def _looks_like_content_item(obj: dict[str, Any]) -> bool:
    content_keys = {
        "type",
        "category",
        "text",
        "content",
        "html",
        "latex",
        "table_body",
        "table_caption",
        "image_caption",
        "chart_caption",
        "code_body",
        "img_path",
    }
    return any(k in obj for k in content_keys)


def iter_content_items(content: Any) -> Iterable[dict[str, Any]]:
    """Yield flat dict items from MinerU content JSON.

    MinerU output is not perfectly stable across models/versions. In addition to
    the common flat `list[dict]`, we have seen nested shapes like
    `list[list[dict]]` and dictionaries containing page/block arrays. This
    iterator flattens those structures safely.
    """
    if isinstance(content, list):
        for item in content:
            yield from iter_content_items(item)
        return

    if not isinstance(content, dict):
        return

    if _looks_like_content_item(content):
        yield content
        return

    # Page/block containers from different MinerU JSON variants.
    for key in (
        "content",
        "items",
        "children",
        "blocks",
        "page_blocks",
        "preproc_blocks",
        "layout_blocks",
        "pdf_info",
        "pages",
        "data",
    ):
        value = content.get(key)
        if value is not None:
            yield from iter_content_items(value)


def _item_page_idx(item: dict[str, Any]) -> int | None:
    for key in ("page_idx", "page", "page_no", "page_num"):
        value = item.get(key)
        if isinstance(value, int):
            # page_idx is normally zero-based; page/page_no may be one-based.
            if key == "page_idx":
                return value + 1
            return value
        if isinstance(value, str) and value.isdigit():
            number = int(value)
            if key == "page_idx":
                return number + 1
            return number
    return None


def chunks_from_content_list(
    content: Any,
    *,
    max_chars: int = 1800,
    overlap_chars: int = 180,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    buf: list[str] = []
    buf_pages: set[int] = set()
    buf_types: set[str] = set()
    heading_stack: list[str] = []

    def flush() -> None:
        nonlocal buf, buf_pages, buf_types
        text = normalize_ws("\n".join(buf))
        if not text:
            buf, buf_pages, buf_types = [], set(), set()
            return

        # Split very large table/text blocks while preserving page metadata.
        parts = sliding_window_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
        for part in parts:
            chunks.append(
                {
                    "text": part,
                    "pages": sorted(buf_pages),
                    "content_types": sorted(buf_types),
                    "heading_path": " > ".join(heading_stack[-4:]),
                }
            )
        buf, buf_pages, buf_types = [], set(), set()

    for item in iter_content_items(content):
        item_text = content_item_to_text(item)
        if not item_text:
            continue

        typ = str(item.get("type") or item.get("category") or "text")
        page_no = _item_page_idx(item)
        text_level = item.get("text_level") or item.get("level")

        if isinstance(text_level, int) and text_level > 0 and item_text:
            # Flush before headings so the next chunk has fresh context.
            flush()
            if len(heading_stack) >= text_level:
                heading_stack = heading_stack[: text_level - 1]
            heading_stack.append(item_text[:120])

        item_len = len(item_text)
        current_len = sum(len(x) for x in buf)
        if buf and current_len + item_len > max_chars:
            flush()

        prefix = ""
        if typ in {"table", "chart", "image", "equation"}:
            prefix = f"[{typ}] "
        buf.append(prefix + item_text)

        if isinstance(page_no, int) and page_no > 0:
            buf_pages.add(page_no)
        buf_types.add(typ)

    flush()
    return chunks


def chunks_from_markdown(md_text: str, *, max_chars: int = 1800, overlap_chars: int = 180) -> list[dict[str, Any]]:
    # Split by headings first, then sliding window.
    blocks: list[tuple[str, str]] = []
    current_heading = ""
    current: list[str] = []

    for line in md_text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            if current:
                blocks.append((current_heading, "\n".join(current)))
            current = []
            current_heading = re.sub(r"^#{1,6}\s+", "", line).strip()
        current.append(line)

    if current:
        blocks.append((current_heading, "\n".join(current)))

    chunks: list[dict[str, Any]] = []
    for heading, block in blocks or [("", md_text)]:
        for part in sliding_window_text(block, max_chars=max_chars, overlap_chars=overlap_chars):
            chunks.append({"text": part, "pages": [], "content_types": ["markdown"], "heading_path": heading})
    return chunks


def build_chunks_for_doc(
    manifest_record: dict[str, Any],
    *,
    output_root: Path,
    max_chars: int = 1800,
    overlap_chars: int = 180,
) -> list[dict[str, Any]]:
    extract_dir = Path(manifest_record.get("local_extract_dir") or "")
    artifacts = manifest_record.get("artifacts") or {}

    content_path = resolve_artifact(extract_dir, artifacts.get("content_list_path", ""))
    if content_path is None:
        content_path = resolve_artifact(extract_dir, artifacts.get("content_list_v2_path", ""))
    content = load_json_if_exists(content_path)

    if isinstance(content, (list, dict)):
        raw_chunks = chunks_from_content_list(content, max_chars=max_chars, overlap_chars=overlap_chars)
    else:
        md_path = resolve_artifact(extract_dir, artifacts.get("markdown_path", ""))
        md_text = md_path.read_text(encoding="utf-8", errors="ignore") if md_path else ""
        raw_chunks = chunks_from_markdown(md_text, max_chars=max_chars, overlap_chars=overlap_chars)

    # Fallback: content_list exists but flattened to no usable text.
    if not raw_chunks:
        md_path = resolve_artifact(extract_dir, artifacts.get("markdown_path", ""))
        md_text = md_path.read_text(encoding="utf-8", errors="ignore") if md_path else ""
        raw_chunks = chunks_from_markdown(md_text, max_chars=max_chars, overlap_chars=overlap_chars)

    page_start = manifest_record.get("upload_page_start")
    page_offset = int(page_start) - 1 if isinstance(page_start, int) and page_start > 0 else 0
    part_no = int(manifest_record.get("upload_part_no") or 1)
    total_parts = int(manifest_record.get("upload_total_parts") or 1)
    part_tag = f"p{part_no:03d}" if total_parts > 1 else "p001"

    rows: list[dict[str, Any]] = []
    for idx, c in enumerate(raw_chunks, start=1):
        text = c["text"]
        pages = c.get("pages", []) or []
        if page_offset and pages:
            pages = [int(pg) + page_offset for pg in pages]

        rows.append(
            {
                "chunk_id": f"{manifest_record['domain']}__{manifest_record['doc_id']}__{part_tag}__c{idx:04d}",
                "domain": manifest_record["domain"],
                "doc_id": manifest_record["doc_id"],
                "source_rel_path": manifest_record.get("rel_path", ""),
                "source_file": manifest_record.get("path", ""),
                "upload_rel_path": manifest_record.get("upload_rel_path", ""),
                "upload_part_no": part_no,
                "upload_total_parts": total_parts,
                "upload_page_start": manifest_record.get("upload_page_start"),
                "upload_page_end": manifest_record.get("upload_page_end"),
                "upload_total_pages": manifest_record.get("upload_total_pages"),
                "parse_engine": manifest_record.get("engine", ""),
                "model_version": manifest_record.get("model_version", ""),
                "pages": pages,
                "heading_path": c.get("heading_path", ""),
                "content_types": c.get("content_types", []),
                "char_count": len(text),
                "token_estimate": estimate_tokens(text),
                "text": text,
            }
        )
    return rows
