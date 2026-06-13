"""LLM-assisted title hierarchy enhancement for MinerU parse artifacts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
import collections
import json
import re

from .llm_client import LLMUsage, OpenAICompatibleClient, compute_usage_cost, estimate_tokens


TITLE_SYSTEM_PROMPT = """你是中文金融、合同、年报、保险条款和监管文档的标题层级校对器。
你的任务是根据输入的连续标题序列、页码、MinerU 原始层级和少量上下文，纠正每个标题的层级。
必须遵守：
1. 只返回 JSON 对象，不返回 Markdown 或解释文字。
2. 不得新增、删除、合并或改写标题。
3. 每个输入 title_id 必须返回且只返回一次。
4. enhanced_level 必须是 1 到 6 的整数；文档主标题/章/节通常靠近 1，编号越细层级越深。
5. is_title 表示该项是否应保留为标题；明显页眉、页脚、表格字段、机构名单或封面辅助信息可以设为 false。
6. 中文金融文档常见层级可参考：第X章/第X节 -> 1；一、 -> 2；（一） -> 3；1、/1.1 -> 4；（1）/① -> 5；a、/A. -> 6。
输出格式：{"items":[{"title_id":"...","enhanced_level":1,"is_title":true}]}。"""


@dataclass
class TextBlock:
    """A text block extracted from MinerU content artifacts."""

    block_id: str
    text: str
    block_type: str
    raw_level: int | None
    page_idx: int | None
    bbox: list[float] | list[int] | None
    order: int


@dataclass
class TitleCandidate:
    """A title candidate sent to the LLM for hierarchy correction."""

    title_id: str
    text: str
    raw_level: int | None
    page_idx: int | None
    bbox: list[float] | list[int] | None
    order: int
    before_text: str = ""
    after_text: str = ""
    source_artifact: str = ""

    def to_prompt_json(self) -> dict[str, Any]:
        """Return compact JSON used in the LLM prompt."""
        data: dict[str, Any] = {
            "title_id": self.title_id,
            "page_idx": self.page_idx,
            "raw_level": self.raw_level,
            "text": self.text,
        }
        if self.before_text:
            data["before_text"] = self.before_text
        if self.after_text:
            data["after_text"] = self.after_text
        return data


@dataclass
class EnhancedTitle:
    """A validated enhanced title record."""

    title_id: str
    text: str
    raw_title_level: int | None
    enhanced_title_level: int
    is_title: bool
    page_idx: int | None
    bbox: list[float] | list[int] | None
    order: int
    source_artifact: str
    section_path: list[str] = field(default_factory=list)

    def to_json(self, record: dict[str, Any], *, enhance_method: str, model: str) -> dict[str, Any]:
        """Return JSONL-ready title hierarchy record."""
        return {
            "domain": record.get("domain", ""),
            "doc_id": record.get("doc_id", ""),
            "part_no": record.get("upload_part_no", record.get("part_no", 1)),
            "upload_total_parts": record.get("upload_total_parts", 1),
            "upload_page_start": record.get("upload_page_start"),
            "upload_page_end": record.get("upload_page_end"),
            "title_id": self.title_id,
            "text": self.text,
            "raw_title_level": self.raw_title_level,
            "enhanced_title_level": self.enhanced_title_level,
            "is_title": self.is_title,
            "section_path": self.section_path,
            "page_idx": self.page_idx,
            "bbox": self.bbox,
            "source_artifact": self.source_artifact,
            "enhance_method": enhance_method,
            "model": model,
            "local_extract_dir": record.get("local_extract_dir", ""),
        }


@dataclass
class HierarchyEnhanceOptions:
    """Options for one enhancement run."""

    output_dir: Path
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key: str | None = None
    base_url: str | None = None
    batch_size: int = 120
    timeout: int = 120
    max_retries: int = 3
    doc_id: str | None = None
    domain: str | None = None
    extract_dir: Path | None = None
    limit_docs: int | None = None
    write_enhanced_md: bool = True
    resume: bool = False
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None


def normalize_inline_text(text: str) -> str:
    """Normalize inline text for matching titles across artifacts and Markdown."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = text.replace("\u3000", " ").replace("&nbsp;", " ")
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip("# ")


def shorten(text: str, max_chars: int) -> str:
    """Return a compact one-line text snippet."""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:max_chars]


def read_json(path: Path | None) -> Any:
    """Read a JSON file if it exists."""
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL into a list of dictionaries."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write dictionaries as UTF-8 JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def resolve_path(path_value: str | None, *, base: Path | None = None) -> Path | None:
    """Resolve a possibly relative path from manifest or artifacts."""
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    if base is not None:
        candidate = base / path
        if candidate.exists():
            return candidate
    return path


def artifact_path(record: dict[str, Any], key: str) -> Path | None:
    """Resolve an artifact path inside a record's extract directory."""
    extract_dir = resolve_path(str(record.get("local_extract_dir") or ""))
    if extract_dir is None:
        return None
    rel = (record.get("artifacts") or {}).get(key)
    return resolve_path(rel, base=extract_dir)


def text_from_content_value(value: Any) -> str:
    """Extract readable text from nested MinerU content fields."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            if node.strip():
                parts.append(node.strip())
            return
        if isinstance(node, (int, float)):
            parts.append(str(node))
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            for key in (
                "text",
                "content",
                "title_content",
                "paragraph_content",
                "list_content",
                "table_caption",
                "table_body",
                "caption",
            ):
                if key in node:
                    walk(node.get(key))

    walk(value)
    return shorten("".join(parts), 500)


def _extract_blocks_from_content_list_v2(data: Any) -> list[TextBlock]:
    """Extract ordered text blocks from MinerU content_list_v2."""
    blocks: list[TextBlock] = []
    order = 0
    if not isinstance(data, list):
        return blocks
    for page_no, page in enumerate(data):
        if not isinstance(page, list):
            continue
        for item in page:
            if not isinstance(item, dict):
                continue
            typ = str(item.get("type") or "")
            content = item.get("content") if isinstance(item.get("content"), dict) else item
            raw_level = None
            if typ == "title":
                raw_level = _safe_int((content or {}).get("level"))
            text = text_from_content_value(content)
            if not text:
                continue
            blocks.append(
                TextBlock(
                    block_id=f"b{order:06d}",
                    text=text,
                    block_type=typ,
                    raw_level=raw_level,
                    page_idx=page_no,
                    bbox=item.get("bbox"),
                    order=order,
                )
            )
            order += 1
    return blocks


def _extract_blocks_from_content_list(data: Any) -> list[TextBlock]:
    """Extract ordered text blocks from MinerU content_list."""
    blocks: list[TextBlock] = []
    if not isinstance(data, list):
        return blocks
    for order, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        raw_level = _safe_int(item.get("text_level"))
        typ = str(item.get("type") or "")
        text = text_from_content_value(item)
        if not text:
            continue
        block_type = "title" if raw_level else typ
        blocks.append(
            TextBlock(
                block_id=f"b{order:06d}",
                text=text,
                block_type=block_type,
                raw_level=raw_level,
                page_idx=_safe_int(item.get("page_idx")),
                bbox=item.get("bbox"),
                order=order,
            )
        )
    return blocks


def _extract_blocks_from_markdown(markdown_path: Path | None) -> list[TextBlock]:
    """Fallback title extraction from Markdown headings."""
    if markdown_path is None or not markdown_path.exists():
        return []
    blocks: list[TextBlock] = []
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    order = 0
    for line in markdown_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = heading_re.match(stripped)
        if match:
            blocks.append(
                TextBlock(
                    block_id=f"b{order:06d}",
                    text=match.group(2).strip(),
                    block_type="title",
                    raw_level=len(match.group(1)),
                    page_idx=None,
                    bbox=None,
                    order=order,
                )
            )
        else:
            blocks.append(
                TextBlock(
                    block_id=f"b{order:06d}",
                    text=stripped,
                    block_type="text",
                    raw_level=None,
                    page_idx=None,
                    bbox=None,
                    order=order,
                )
            )
        order += 1
    return blocks


def extract_blocks(record: dict[str, Any]) -> tuple[list[TextBlock], str]:
    """Extract blocks from the best available artifact for a manifest record."""
    v2_path = artifact_path(record, "content_list_v2_path")
    v2_data = read_json(v2_path)
    blocks = _extract_blocks_from_content_list_v2(v2_data)
    if blocks:
        return blocks, "content_list_v2"

    content_path = artifact_path(record, "content_list_path")
    content_data = read_json(content_path)
    # Some historical manifests accidentally pointed content_list_path to v2.
    blocks = _extract_blocks_from_content_list_v2(content_data)
    if blocks:
        return blocks, "content_list_v2"
    blocks = _extract_blocks_from_content_list(content_data)
    if blocks:
        return blocks, "content_list"

    md_path = artifact_path(record, "markdown_path")
    blocks = _extract_blocks_from_markdown(md_path)
    if blocks:
        return blocks, "markdown"
    return [], ""


def build_title_candidates(record: dict[str, Any]) -> list[TitleCandidate]:
    """Build ordered title candidates with short local context."""
    blocks, source_artifact = extract_blocks(record)
    candidates: list[TitleCandidate] = []
    for index, block in enumerate(blocks):
        is_title = block.block_type == "title" or bool(block.raw_level)
        if not is_title:
            continue
        before_text = ""
        after_text = ""
        for prev in reversed(blocks[:index]):
            if prev.text and prev.text != block.text:
                before_text = shorten(prev.text, 100)
                break
        for nxt in blocks[index + 1 :]:
            if nxt.text and nxt.text != block.text:
                after_text = shorten(nxt.text, 140)
                break
        doc_id = str(record.get("doc_id") or "doc")
        part_no = record.get("upload_part_no", record.get("part_no", 1))
        title_id = f"{doc_id}_p{part_no}_t{len(candidates):06d}"
        candidates.append(
            TitleCandidate(
                title_id=title_id,
                text=block.text,
                raw_level=block.raw_level,
                page_idx=block.page_idx,
                bbox=block.bbox,
                order=block.order,
                before_text=before_text,
                after_text=after_text,
                source_artifact=source_artifact,
            )
        )
    return candidates


def _safe_int(value: Any) -> int | None:
    """Convert a value to int, returning None for invalid values."""
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _mock_level_for_text(text: str, raw_level: int | None) -> tuple[int, bool]:
    """Local deterministic fallback used for tests and dry runs."""
    stripped = text.strip()
    if not stripped:
        return raw_level or 2, False
    patterns: list[tuple[str, int]] = [
        (r"^第[一二三四五六七八九十百千万0-9〇零]+[章节篇部分]", 1),
        (r"^[一二三四五六七八九十百千万〇零]+[、．.]", 2),
        (r"^（[一二三四五六七八九十百千万〇零]+）", 3),
        (r"^\(?[0-9]+(\.[0-9]+)+[\)、.． ]?", 4),
        (r"^[0-9]+[、．.]", 4),
        (r"^（[0-9]+）", 5),
        (r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]", 5),
        (r"^[a-zA-Z][、．.]", 6),
    ]
    for pattern, level in patterns:
        if re.match(pattern, stripped):
            return level, True
    return max(1, min(6, raw_level or 2)), True


def _call_mock_llm(candidates: list[TitleCandidate], model: str) -> tuple[list[dict[str, Any]], LLMUsage]:
    """Return deterministic hierarchy corrections without network calls."""
    items: list[dict[str, Any]] = []
    prompt_text = json.dumps([c.to_prompt_json() for c in candidates], ensure_ascii=False)
    for candidate in candidates:
        level, is_title = _mock_level_for_text(candidate.text, candidate.raw_level)
        items.append({"title_id": candidate.title_id, "enhanced_level": level, "is_title": is_title})
    output_text = json.dumps({"items": items}, ensure_ascii=False)
    usage = compute_usage_cost(
        model=model,
        prompt_tokens=estimate_tokens(prompt_text),
        completion_tokens=estimate_tokens(output_text),
    )
    return items, usage


def _parse_llm_items(content: str) -> list[dict[str, Any]]:
    """Parse model JSON content into a list of item dictionaries."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end < start:
            raise
        data = json.loads(content[start : end + 1])
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("LLM 输出缺少 items 数组")
    return [item for item in items if isinstance(item, dict)]


def _validate_items(candidates: list[TitleCandidate], items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate and normalize LLM returned title items."""
    expected = {candidate.title_id: candidate for candidate in candidates}
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        title_id = str(item.get("title_id") or "")
        if title_id not in expected or title_id in result:
            continue
        level = _safe_int(item.get("enhanced_level"))
        if level is None:
            level = expected[title_id].raw_level or 2
        level = max(1, min(6, level))
        is_title = bool(item.get("is_title", True))
        result[title_id] = {"enhanced_level": level, "is_title": is_title}
    for title_id, candidate in expected.items():
        if title_id not in result:
            level, is_title = _mock_level_for_text(candidate.text, candidate.raw_level)
            result[title_id] = {"enhanced_level": level, "is_title": is_title}
    return result


def build_prompt_payload(
    *,
    record: dict[str, Any],
    candidates: list[TitleCandidate],
    previous_section_stack: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the compact JSON payload sent to the LLM."""
    return {
        "document": {
            "domain": record.get("domain", ""),
            "doc_id": record.get("doc_id", ""),
            "part_no": record.get("upload_part_no", record.get("part_no", 1)),
            "upload_page_start": record.get("upload_page_start"),
            "upload_page_end": record.get("upload_page_end"),
            "document_type_hint": "中文金融合同、债券募集说明书、年报、保险条款、监管文件或研究报告",
        },
        "previous_section_stack": previous_section_stack,
        "titles": [candidate.to_prompt_json() for candidate in candidates],
    }


def enhance_batch(
    *,
    record: dict[str, Any],
    candidates: list[TitleCandidate],
    previous_section_stack: list[dict[str, Any]],
    options: HierarchyEnhanceOptions,
    client: OpenAICompatibleClient | None,
) -> tuple[list[EnhancedTitle], LLMUsage]:
    """Enhance a single candidate batch with either LLM or mock provider."""
    if options.provider == "mock":
        items, usage = _call_mock_llm(candidates, options.model)
    else:
        if client is None:
            raise RuntimeError("provider=deepseek 时必须提供 LLM client")
        payload = build_prompt_payload(
            record=record,
            candidates=candidates,
            previous_section_stack=previous_section_stack,
        )
        chat_result = client.chat_json(system_prompt=TITLE_SYSTEM_PROMPT, user_payload=payload)
        items = _parse_llm_items(chat_result.content)
        usage = chat_result.usage
    by_id = _validate_items(candidates, items)
    enhanced: list[EnhancedTitle] = []
    for candidate in candidates:
        item = by_id[candidate.title_id]
        enhanced.append(
            EnhancedTitle(
                title_id=candidate.title_id,
                text=candidate.text,
                raw_title_level=candidate.raw_level,
                enhanced_title_level=int(item["enhanced_level"]),
                is_title=bool(item["is_title"]),
                page_idx=candidate.page_idx,
                bbox=candidate.bbox,
                order=candidate.order,
                source_artifact=candidate.source_artifact,
            )
        )
    return enhanced, usage


def assign_section_paths(titles: list[EnhancedTitle]) -> None:
    """Assign section_path to enhanced titles in-place."""
    stack: list[EnhancedTitle] = []
    for title in titles:
        if not title.is_title:
            title.section_path = [item.text for item in stack]
            continue
        level = title.enhanced_title_level
        while stack and stack[-1].enhanced_title_level >= level:
            stack.pop()
        stack.append(title)
        title.section_path = [item.text for item in stack]


def final_section_stack(titles: list[EnhancedTitle]) -> list[dict[str, Any]]:
    """Return the current section stack after processing a batch."""
    stack: list[EnhancedTitle] = []
    for title in titles:
        if not title.is_title:
            continue
        while stack and stack[-1].enhanced_title_level >= title.enhanced_title_level:
            stack.pop()
        stack.append(title)
    return [{"level": item.enhanced_title_level, "text": item.text} for item in stack[-8:]]


def rewrite_markdown_headings(extract_dir: Path, titles: list[EnhancedTitle]) -> Path | None:
    """Write full_titleEnhanced.md by replacing Markdown heading markers in order."""
    full_md = extract_dir / "full.md"
    if not full_md.exists():
        candidates = sorted(extract_dir.glob("*full*.md"), key=lambda p: p.stat().st_size, reverse=True)
        full_md = candidates[0] if candidates else full_md
    if not full_md.exists():
        return None

    lines = full_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    title_queue = [title for title in titles if title.is_title]
    cursor = 0
    matched = 0
    heading_re = re.compile(r"^(#{1,6})(\s+)(.+?)(\s*)$")
    new_lines: list[str] = []
    for line in lines:
        match = heading_re.match(line)
        if not match:
            new_lines.append(line)
            continue
        heading_text = normalize_inline_text(match.group(3))
        found_index: int | None = None
        for idx in range(cursor, min(cursor + 25, len(title_queue))):
            if normalize_inline_text(title_queue[idx].text) == heading_text:
                found_index = idx
                break
        if found_index is None:
            new_lines.append(line)
            continue
        title = title_queue[found_index]
        cursor = found_index + 1
        matched += 1
        marks = "#" * max(1, min(6, title.enhanced_title_level))
        new_lines.append(f"{marks}{match.group(2)}{match.group(3)}{match.group(4)}")

    out_path = full_md.with_name("full_titleEnhanced.md")
    out_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return out_path


def _select_records(records: list[dict[str, Any]], options: HierarchyEnhanceOptions) -> list[dict[str, Any]]:
    """Filter manifest records for hierarchy enhancement."""
    selected: list[dict[str, Any]] = []
    target_extract = options.extract_dir.resolve() if options.extract_dir else None
    for record in records:
        if target_extract is not None:
            local_extract_dir = resolve_path(str(record.get("local_extract_dir") or ""))
            if local_extract_dir is None or local_extract_dir.resolve() != target_extract:
                continue
        if options.doc_id and str(record.get("doc_id")) != options.doc_id:
            continue
        if options.domain and str(record.get("domain")) != options.domain:
            continue
        if str(record.get("engine")) != "mineru_vlm" and target_extract is None:
            continue
        selected.append(record)
        if options.limit_docs is not None and len(selected) >= options.limit_docs:
            break
    return selected


def _manual_record_from_extract_dir(options: HierarchyEnhanceOptions) -> dict[str, Any]:
    """Create a manifest-like record for a directly supplied extract directory."""
    if options.extract_dir is None:
        raise RuntimeError("缺少 extract_dir")
    extract_dir = options.extract_dir
    record: dict[str, Any] = {
        "domain": options.domain or "manual",
        "doc_id": options.doc_id or extract_dir.name,
        "engine": "mineru_vlm",
        "upload_part_no": 1,
        "upload_total_parts": 1,
        "local_extract_dir": str(extract_dir),
        "artifacts": {
            "markdown_path": "full.md",
            "content_list_path": "",
            "content_list_v2_path": "",
        },
    }
    v2 = next(iter(extract_dir.glob("*content_list_v2.json")), None)
    legacy = next(iter(extract_dir.glob("*content_list.json")), None)
    if v2:
        record["artifacts"]["content_list_v2_path"] = v2.name
    if legacy:
        record["artifacts"]["content_list_path"] = legacy.name
    return record


def _load_records_for_options(options: HierarchyEnhanceOptions) -> list[dict[str, Any]]:
    """Load manifest records and support direct extract-dir testing."""
    manifest_path = options.output_dir / "manifest.jsonl"
    if manifest_path.exists():
        records = iter_jsonl(manifest_path)
    elif options.extract_dir:
        records = []
    else:
        raise RuntimeError(f"找不到 manifest.jsonl：{manifest_path}")

    selected = _select_records(records, options)
    # A directly supplied extract directory should always be testable, even if
    # manifest paths were produced on another machine or the record is absent.
    if options.extract_dir and not selected:
        selected = [_manual_record_from_extract_dir(options)]
    return selected


def enhance_hierarchy(options: HierarchyEnhanceOptions) -> dict[str, Any]:
    """Run hierarchy enhancement and write output artifacts."""
    options.output_dir = Path(options.output_dir)
    records = _load_records_for_options(options)
    if not records:
        raise RuntimeError("没有找到需要增强标题层级的 MinerU 记录")

    client: OpenAICompatibleClient | None = None
    if options.provider == "deepseek":
        client = OpenAICompatibleClient.from_deepseek_env(
            model=options.model,
            api_key=options.api_key,
            base_url=options.base_url,
            timeout=options.timeout,
            max_retries=options.max_retries,
            input_price_per_1m=options.input_price_per_1m,
            output_price_per_1m=options.output_price_per_1m,
        )

    all_rows: list[dict[str, Any]] = []
    usage_total = LLMUsage()
    raw_counts: collections.Counter[int] = collections.Counter()
    enhanced_counts: collections.Counter[int] = collections.Counter()
    docs_summary: list[dict[str, Any]] = []
    written_md: list[str] = []

    for record in records:
        candidates = build_title_candidates(record)
        for candidate in candidates:
            if candidate.raw_level:
                raw_counts[candidate.raw_level] += 1
        if not candidates:
            docs_summary.append(
                {
                    "domain": record.get("domain", ""),
                    "doc_id": record.get("doc_id", ""),
                    "title_candidates": 0,
                    "enhanced_titles": 0,
                    "warning": "no title candidates found",
                }
            )
            continue

        enhanced_titles: list[EnhancedTitle] = []
        previous_stack: list[dict[str, Any]] = []
        for start in range(0, len(candidates), max(1, options.batch_size)):
            batch = candidates[start : start + max(1, options.batch_size)]
            enhanced_batch, usage = enhance_batch(
                record=record,
                candidates=batch,
                previous_section_stack=previous_stack,
                options=options,
                client=client,
            )
            enhanced_titles.extend(enhanced_batch)
            usage_total.add(usage)
            # Update temporary stack using all titles seen so far.
            assign_section_paths(enhanced_titles)
            previous_stack = final_section_stack(enhanced_titles)

        assign_section_paths(enhanced_titles)
        for title in enhanced_titles:
            enhanced_counts[title.enhanced_title_level] += 1
            all_rows.append(title.to_json(record, enhance_method=options.provider, model=options.model))

        extract_dir = resolve_path(str(record.get("local_extract_dir") or ""))
        enhanced_md_path: Path | None = None
        if options.write_enhanced_md and extract_dir is not None and extract_dir.exists():
            enhanced_md_path = rewrite_markdown_headings(extract_dir, enhanced_titles)
            if enhanced_md_path is not None:
                written_md.append(str(enhanced_md_path))

        docs_summary.append(
            {
                "domain": record.get("domain", ""),
                "doc_id": record.get("doc_id", ""),
                "part_no": record.get("upload_part_no", 1),
                "title_candidates": len(candidates),
                "enhanced_titles": len([t for t in enhanced_titles if t.is_title]),
                "enhanced_md_path": str(enhanced_md_path) if enhanced_md_path else "",
            }
        )

    title_hierarchy_path = options.output_dir / "title_hierarchy.jsonl"
    stats_path = options.output_dir / "hierarchy_stats.json"
    write_jsonl(title_hierarchy_path, all_rows)
    stats = {
        "provider": options.provider,
        "model": options.model,
        "record_count": len(records),
        "title_record_count": len(all_rows),
        "raw_title_level_counts": {str(k): v for k, v in sorted(raw_counts.items())},
        "enhanced_title_level_counts": {str(k): v for k, v in sorted(enhanced_counts.items())},
        "usage": usage_total.to_json(),
        "outputs": {
            "title_hierarchy_jsonl": str(title_hierarchy_path),
            "hierarchy_stats_json": str(stats_path),
            "enhanced_markdown_files": written_md,
        },
        "documents": docs_summary,
    }
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats
