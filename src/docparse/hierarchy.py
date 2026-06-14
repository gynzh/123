"""Rule-based and optional LLM title hierarchy enhancement for MinerU parse artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
import collections
import json
import math
import re
import time

from .llm_client import LLMUsage, OpenAICompatibleClient, compute_usage_cost, estimate_tokens


TITLE_SYSTEM_PROMPT = """你是中文金融、合同、年报、保险条款和监管文档的标题层级重建器。

任务：根据完整 PDF 的标题序列和目录参考，判断正文标题的真实层级。

规则：
1. 只处理 titles 中的项目；toc中是目录中的标题信息，是判断title level的高置信度信息，尽量参考该信息保证高层级title level的准确性。
2. 每个 titles.id 必须返回一次，不能新增、删除或改写标题。
3. 返回层级范围为 1-6；不是正文标题时返回 0。
4. raw_level 只是 MinerU 的弱参考，不要直接照抄。
5. 常见层级参考：第X章/第X节=1；一、二、三、=2；（一）（二）=3；1、2、或1.1=4；（1）（2）或①②=5；a、b、A.、B.=6。
6. 封面机构名称、承销商名称、页眉页脚、表格字段、图片说明、重复噪声不是正文标题。
7. 只返回 JSON，不要解释。

返回格式：{"items":[["标题ID",层级], ...]}，层级为 1-6；0 表示不是正文标题。
"""


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
    """A title candidate extracted from MinerU artifacts."""

    title_id: str
    text: str
    raw_level: int | None
    page_idx: int | None
    bbox: list[float] | list[int] | None
    order: int
    source_artifact: str = ""
    is_toc_heading: bool = False
    is_toc_entry: bool = False
    toc_reason: str = ""
    part_no: int = 1
    global_order: int = 0
    global_page_idx: int | None = None
    record_index: int = 0
    rule_detected: bool = False
    rule_reason: str = ""

    def to_prompt_json(self) -> dict[str, Any]:
        """Return the minimal title item sent to the LLM."""
        return {
            "id": self.title_id,
            "o": self.global_order or self.order,
            "p": self.global_page_idx,
            "r": self.raw_level,
            "x": self.text,
        }

    def to_toc_prompt_json(self) -> dict[str, Any]:
        """Return the minimal TOC reference item sent to the LLM."""
        return {
            "o": self.global_order or self.order,
            "p": self.global_page_idx,
            "x": self.text,
        }


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
    is_toc_heading: bool = False
    is_toc_entry: bool = False
    toc_reason: str = ""
    part_no: int = 1
    global_order: int = 0
    global_page_idx: int | None = None
    record_index: int = 0
    rule_detected: bool = False
    rule_reason: str = ""

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
            "is_toc_heading": self.is_toc_heading,
            "is_toc_entry": self.is_toc_entry,
            "toc_reason": self.toc_reason,
            "rule_detected": self.rule_detected,
            "rule_reason": self.rule_reason,
            "section_path": self.section_path,
            "page_idx": self.page_idx,
            "global_page_idx": self.global_page_idx,
            "global_order": self.global_order or self.order,
            "bbox": self.bbox,
            "source_artifact": self.source_artifact,
            "enhance_method": enhance_method,
            "model": model,
            "local_extract_dir": record.get("local_extract_dir", ""),
        }


@dataclass
class TocDetectionResult:
    """Detected table-of-contents pages and reasons."""

    enabled: bool
    toc_pages: set[int] = field(default_factory=set)
    toc_start_page: int | None = None
    page_stats: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass
class HierarchyEnhanceOptions:
    """Options for one enhancement run."""

    output_dir: Path
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key: str | None = None
    base_url: str | None = None
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
    enable_toc_filter: bool = True
    toc_max_start_page: int = 15
    toc_max_follow_pages: int = 12

@dataclass
class DocumentGroup:
    """A complete PDF-level group assembled from one or more MinerU parts."""

    group_id: str
    domain: str
    doc_id: str
    records: list[dict[str, Any]]


def normalize_inline_text(text: str) -> str:
    """Normalize inline text for matching titles across artifacts and Markdown."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = text.replace("\u3000", " ").replace(" ", " ")
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


def _safe_int(value: Any) -> int | None:
    """Convert a value to int, returning None for invalid values."""
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


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


_TOC_HEADING_RE = re.compile(r"^(目录|目\s*录|目錄|目次|contents|table\s+of\s+contents)$", re.IGNORECASE)
_DOT_LEADER_RE = re.compile(r"(\.{2,}|·{2,}|…{1,}|⋯{1,}|-{2,}|—{2,}|_{2,})")
_TRAILING_PAGE_RE = re.compile(r"(?:\s|\.|·|…|⋯|-|—|_)+(?:[ivxlcdmIVXLCDM]+|\d{1,4})\s*$")
_HEADING_LIKE_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万0-9〇零]+[章节篇部分]|"
    r"[一二三四五六七八九十百千万〇零]+[、．.]|"
    r"（[一二三四五六七八九十百千万〇零]+）|"
    r"\(?\d+(?:\.\d+)*[、．.)）]?|"
    r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|"
    r"[A-Za-z][、．.])"
)


def is_toc_heading_text(text: str) -> bool:
    """Return True for the TOC page heading itself."""
    normalized = re.sub(r"\s+", "", (text or "").strip()).lower()
    return bool(_TOC_HEADING_RE.match(normalized)) or normalized in {"contents", "tableofcontents"}


def is_toc_entry_text(text: str) -> bool:
    """Return True if a line looks like a table-of-contents entry."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if not stripped or is_toc_heading_text(stripped):
        return False
    if len(stripped) > 120:
        return False
    compact = re.sub(r"\s+", "", stripped)
    has_trailing_page = bool(_TRAILING_PAGE_RE.search(stripped)) or bool(re.search(r"[.·…⋯\-—_]\d{1,4}$", compact))
    if not has_trailing_page:
        return False
    if _DOT_LEADER_RE.search(stripped) or _DOT_LEADER_RE.search(compact):
        return True
    without_page = _TRAILING_PAGE_RE.sub("", stripped).strip()
    without_page = re.sub(r"[.·…⋯\-—_]+$", "", without_page).strip()
    if len(without_page) <= 2:
        return False
    return bool(_HEADING_LIKE_RE.match(without_page)) or len(without_page) <= 45


_MAJOR_UNNUMBERED_TITLES = {
    "目录",
    "目次",
    "声明",
    "释义",
    "摘要",
    "重大事项提示",
    "重要提示",
    "风险提示",
    "风险提示及说明",
    "发行概况",
    "募集资金运用",
    "发行人基本情况",
    "财务会计信息",
    "管理层讨论与分析",
    "债券持有人会议规则",
    "受托管理人",
    "备查文件",
    "审计报告",
    "董事会报告",
    "监事会报告",
    "公司治理",
    "保险责任",
    "责任免除",
    "重大疾病释义",
    "现金价值",
    "保险金申请",
}

_NOISE_TITLE_PATTERNS = [
    re.compile(r"^(?:[A-Z][A-Z\s&.,()\-]{6,}|CITIC SECURITIES|CHINA SECURITIES|GF SECURITIES|HUAFU SECURITIES)", re.I),
    re.compile(r"^(?:牵头主承销商|联席主承销商|簿记管理人|受托管理人|保荐机构|主承销商|信用评级机构)(?:[/／].*)?[:：]?$"),
    re.compile(r"^(?:中信证券|中信建投证券|广发证券|华福证券|国信证券|万联证券|华泰联合证券|国泰海通证券|金圆统一证券有限公司)$"),
    re.compile(r"^(?:单位[:：]|注[:：]|资料来源[:：]|数据来源[:：])"),
    re.compile(r"^(?:发行人名称|注册地址|法定代表人|注册资本|成立日期|统一社会信用代码|联系人|联系电话|传真|邮政编码)$"),
]

_TITLE_NUMBER_PATTERNS: list[tuple[str, int, str]] = [
    (r"^第[一二三四五六七八九十百千万0-9〇零]+[章节篇部分]", 1, "chapter_or_section"),
    (r"^第[一二三四五六七八九十百千万0-9〇零]+条", 2, "article"),
    (r"^[一二三四五六七八九十百千万〇零]+[、．.]", 2, "chinese_number"),
    (r"^（[一二三四五六七八九十百千万〇零]+）", 3, "parenthesized_chinese"),
    (r"^[0-9]+\.$", 2, "annual_arabic_top"),
    (r"^[0-9]+\.[0-9]+\.?$", 3, "annual_arabic_sub"),
    (r"^\(?[0-9]+(?:\.[0-9]+)+[、．.)）]?", 4, "arabic_dot_number"),
    (r"^[0-9]+[、．.]", 4, "arabic_number"),
    (r"^（[0-9]+）", 5, "parenthesized_arabic"),
    (r"^\([0-9]+\)", 5, "parenthesized_arabic"),
    (r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]", 5, "circled_number"),
    (r"^[a-zA-Z][、．.]", 6, "letter_number"),
]


def strip_toc_page_marker(text: str) -> str:
    """Remove table-of-contents dot leaders and trailing page numbers."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    stripped = _TRAILING_PAGE_RE.sub("", stripped).strip()
    stripped = re.sub(r"[.·…⋯\-—_]+(?:[ivxlcdmIVXLCDM]+|\d{1,4})?$", "", stripped).strip()
    stripped = re.sub(r"[.·…⋯\-—_]+$", "", stripped).strip()
    return stripped


def normalize_title_key(text: str) -> str:
    """Normalize a title key for matching TOC entries to body headings."""
    cleaned = strip_toc_page_marker(text)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = cleaned.replace("\u3000", " ").replace(" ", " ")
    cleaned = re.sub(r"[#*_`|]+", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[：:；;。，,.、]+$", "", cleaned)
    return cleaned


def numbered_heading_level(text: str) -> tuple[int | None, str]:
    """Return rule level and reason for numbered Chinese/financial headings."""
    stripped = strip_toc_page_marker(text)
    compact = re.sub(r"\s+", "", stripped)
    for pattern, level, reason in _TITLE_NUMBER_PATTERNS:
        if re.match(pattern, compact):
            return level, reason
    return None, ""


def is_noise_title(text: str, *, page_idx: int | None = None) -> bool:
    """Filter cover logos, role labels, table notes and other non-structure headings."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if not stripped:
        return True
    if len(stripped) > 140:
        return True
    compact = normalize_title_key(stripped)
    if compact in _MAJOR_UNNUMBERED_TITLES:
        return False
    for pattern in _NOISE_TITLE_PATTERNS:
        if pattern.search(stripped) or pattern.search(compact):
            return True
    if re.fullmatch(r"[A-Za-z0-9\-_/ .]{1,30}", stripped) and not re.search(r"[\u4e00-\u9fff]", stripped):
        return True
    if stripped.startswith("!") or stripped.startswith("<") or stripped.startswith(("☑", "□", "√")):
        return True
    if re.fullmatch(r"[0-9,.，%％()（）+\- ]+(?:元|万元|亿元|百万元)?", stripped):
        return True
    return False


def looks_like_sentence(text: str) -> bool:
    """Return True if a line is more likely body prose than a standalone heading."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if len(stripped) > 90:
        return True
    if stripped.endswith(("。", "；", ";")):
        return True
    if stripped.count("，") + stripped.count(",") >= 2 and len(stripped) > 45:
        return True
    return False


def looks_like_body_list_item(text: str, reason: str) -> bool:
    """Return True for numbered prose items that should not become headings."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if reason in {"parenthesized_arabic", "circled_number", "letter_number"}:
        return len(stripped) > 32 or "的，" in stripped or stripped.endswith(("：", ":", "；", ";", "。"))
    if reason == "arabic_dot_number":
        return len(stripped) > 70 or stripped.endswith(("：", ":", "；", ";", "。"))
    if reason == "arabic_number":
        return len(stripped) > 65 or stripped.endswith(("；", ";", "。"))
    return False


def rule_candidate_reason(block: TextBlock, *, page_is_toc: bool = False) -> tuple[bool, str]:
    """Decide whether a MinerU text block should be considered a heading candidate."""
    text = (block.text or "").strip()
    if not text:
        return False, "empty"
    if block.block_type == "title" or block.raw_level:
        return True, "mineru_title"
    if page_is_toc and (is_toc_heading_text(text) or is_toc_entry_text(text)):
        return True, "toc_text"
    if is_noise_title(text, page_idx=block.page_idx) or looks_like_sentence(text):
        return False, "prose_or_noise"
    level, reason = numbered_heading_level(text)
    if level is not None:
        return True, reason
    if normalize_title_key(text) in _MAJOR_UNNUMBERED_TITLES:
        return True, "known_unnumbered"
    return False, "no_rule"


def toc_entry_level(text: str) -> int:
    """Infer TOC entry level from its numbering pattern."""
    cleaned = strip_toc_page_marker(text)
    if normalize_title_key(cleaned) in _MAJOR_UNNUMBERED_TITLES:
        return 1
    level, _ = numbered_heading_level(cleaned)
    return level or 1


def build_toc_level_map(toc_reference: list[TitleCandidate]) -> dict[str, int]:
    """Build a high-confidence title-level map from TOC entries."""
    level_map: dict[str, int] = {}
    for candidate in toc_reference:
        if candidate.is_toc_heading:
            continue
        key = normalize_title_key(candidate.text)
        if not key:
            continue
        level = toc_entry_level(candidate.text)
        if key not in level_map or level < level_map[key]:
            level_map[key] = level
    return level_map


def clamp_level_to_stack(level: int, stack: list[EnhancedTitle]) -> int:
    """Avoid large hierarchy jumps when numbered headings skip visible parents."""
    if not stack:
        return max(1, min(6, level))
    parent_level = stack[-1].enhanced_title_level
    if level > parent_level + 1:
        return parent_level + 1
    return max(1, min(6, level))


def rule_level_for_candidate(
    candidate: TitleCandidate,
    *,
    toc_level_map: dict[str, int],
    stack: list[EnhancedTitle],
) -> tuple[int, bool, str]:
    """Infer final title level using TOC anchors and deterministic numbering rules."""
    text = candidate.text.strip()
    key = normalize_title_key(text)
    if candidate.is_toc_heading:
        return 1, True, "toc_heading"
    if candidate.is_toc_entry:
        return candidate.raw_level or 2, False, "toc_entry"
    if is_noise_title(text, page_idx=candidate.global_page_idx):
        return 0, False, "noise"
    if key in toc_level_map:
        return toc_level_map[key], True, "toc_match"
    if key in _MAJOR_UNNUMBERED_TITLES:
        return 1, True, "known_unnumbered"
    level, reason = numbered_heading_level(text)
    if level is not None:
        if looks_like_body_list_item(text, reason):
            return 0, False, "body_list_item"
        return clamp_level_to_stack(level, stack), True, reason
    if candidate.raw_level and not looks_like_sentence(text):
        level = 1 if candidate.raw_level == 1 else min(6, max(2, candidate.raw_level))
        return clamp_level_to_stack(level, stack), True, "mineru_unnumbered"
    return 0, False, "not_heading"


def _page_density(blocks: list[TextBlock], page_idx: int) -> dict[str, Any]:
    """Compute TOC-like density statistics for one page."""
    page_blocks = [block for block in blocks if block.page_idx == page_idx and block.text]
    text_count = len(page_blocks)
    title_count = len([block for block in page_blocks if block.block_type == "title" or block.raw_level])
    toc_heading_count = len([block for block in page_blocks if is_toc_heading_text(block.text)])
    toc_entry_count = len([block for block in page_blocks if is_toc_entry_text(block.text)])
    ratio_base = max(1, min(text_count, max(title_count, 1)))
    return {
        "text_count": text_count,
        "title_count": title_count,
        "toc_heading_count": toc_heading_count,
        "toc_entry_count": toc_entry_count,
        "toc_entry_ratio": toc_entry_count / ratio_base,
    }


def detect_toc_pages(
    blocks: list[TextBlock],
    *,
    enabled: bool = True,
    max_start_page: int = 15,
    max_follow_pages: int = 12,
) -> TocDetectionResult:
    """Detect a possibly multi-page table-of-contents interval."""
    if not enabled:
        return TocDetectionResult(enabled=False)
    page_indexes = sorted({block.page_idx for block in blocks if block.page_idx is not None})
    if not page_indexes:
        return TocDetectionResult(enabled=True)

    page_stats = {page_idx: _page_density(blocks, page_idx) for page_idx in page_indexes}
    start_page: int | None = None
    for page_idx in page_indexes:
        if page_idx > max_start_page:
            break
        stats = page_stats[page_idx]
        next_stats = page_stats.get(page_idx + 1, {})
        has_heading = stats.get("toc_heading_count", 0) > 0
        enough_entries = stats.get("toc_entry_count", 0) >= 2 or next_stats.get("toc_entry_count", 0) >= 3
        if has_heading and enough_entries:
            start_page = page_idx
            break
    if start_page is None:
        return TocDetectionResult(enabled=True, page_stats=page_stats)

    toc_pages: set[int] = {start_page}
    last_page = start_page
    for page_idx in page_indexes:
        if page_idx <= start_page:
            continue
        if page_idx - start_page > max_follow_pages:
            break
        if page_idx != last_page + 1:
            break
        stats = page_stats[page_idx]
        entry_count = int(stats.get("toc_entry_count", 0))
        entry_ratio = float(stats.get("toc_entry_ratio", 0.0))
        has_continuation = entry_count >= 3 or entry_ratio >= 0.35 or (entry_count >= 2 and last_page in toc_pages)
        if not has_continuation:
            break
        toc_pages.add(page_idx)
        last_page = page_idx

    return TocDetectionResult(
        enabled=True,
        toc_pages=toc_pages,
        toc_start_page=start_page,
        page_stats=page_stats,
    )


def detect_flat_toc_orders(blocks: list[TextBlock], *, enabled: bool = True) -> set[int]:
    """Detect TOC heading/entry orders when page_idx is unavailable, as in Markdown fallback."""
    if not enabled or any(block.page_idx is not None for block in blocks):
        return set()
    toc_orders: set[int] = set()
    start_index: int | None = None
    for idx, block in enumerate(blocks[:120]):
        if is_toc_heading_text(block.text):
            following = blocks[idx + 1 : idx + 20]
            if sum(1 for item in following if is_toc_entry_text(item.text)) >= 2:
                start_index = idx
                break
    if start_index is None:
        return toc_orders
    toc_orders.add(blocks[start_index].order)
    misses = 0
    for block in blocks[start_index + 1 :]:
        if is_toc_entry_text(block.text):
            toc_orders.add(block.order)
            misses = 0
            continue
        if not block.text.strip():
            continue
        misses += 1
        if misses >= 2:
            break
    return toc_orders


def build_title_candidates(
    record: dict[str, Any],
    *,
    enable_toc_filter: bool = True,
    toc_max_start_page: int = 15,
    toc_max_follow_pages: int = 12,
    include_rule_candidates: bool = False,
) -> tuple[list[TitleCandidate], TocDetectionResult]:
    """Build ordered title candidates from MinerU titles and optional rule-matched text blocks."""
    blocks, source_artifact = extract_blocks(record)
    toc_detection = detect_toc_pages(
        blocks,
        enabled=enable_toc_filter,
        max_start_page=toc_max_start_page,
        max_follow_pages=toc_max_follow_pages,
    )
    flat_toc_orders = detect_flat_toc_orders(blocks, enabled=enable_toc_filter)
    candidates: list[TitleCandidate] = []
    for index, block in enumerate(blocks):
        mineru_is_title = block.block_type == "title" or bool(block.raw_level)
        page_is_toc = (block.page_idx in toc_detection.toc_pages if block.page_idx is not None else False) or block.order in flat_toc_orders
        rule_is_title, rule_reason = rule_candidate_reason(block, page_is_toc=page_is_toc)
        if not mineru_is_title and not (include_rule_candidates and rule_is_title):
            continue
        doc_id = str(record.get("doc_id") or "doc")
        part_no = record.get("upload_part_no", record.get("part_no", 1))
        title_id = f"{doc_id}_p{part_no}_t{len(candidates):06d}"
        is_toc_heading = page_is_toc and is_toc_heading_text(block.text)
        is_toc_entry = page_is_toc and not is_toc_heading and is_toc_entry_text(block.text)
        toc_reason = ""
        if is_toc_heading:
            toc_reason = "toc_heading"
        elif is_toc_entry:
            toc_reason = "toc_entry_on_detected_toc_page"
        candidates.append(
            TitleCandidate(
                title_id=title_id,
                text=block.text,
                raw_level=block.raw_level,
                page_idx=block.page_idx,
                bbox=block.bbox,
                order=block.order,
                source_artifact=source_artifact,
                is_toc_heading=is_toc_heading,
                is_toc_entry=is_toc_entry,
                toc_reason=toc_reason,
                rule_detected=not mineru_is_title,
                rule_reason=rule_reason,
            )
        )
    return candidates, toc_detection


def _record_part_no(record: dict[str, Any]) -> int:
    """Return a stable numeric part number for sorting and prompt context."""
    return _safe_int(record.get("upload_part_no", record.get("part_no", 1))) or 1


def _record_page_start(record: dict[str, Any]) -> int | None:
    """Return the original PDF page start if available."""
    return _safe_int(record.get("upload_page_start"))


def _global_page_idx(record: dict[str, Any], local_page_idx: int | None) -> int | None:
    """Convert MinerU local page_idx to an approximate full-PDF page index."""
    if local_page_idx is None:
        return None
    page_start = _record_page_start(record)
    if page_start is None:
        return local_page_idx
    return page_start + local_page_idx


def prepare_group_candidates(
    group: DocumentGroup,
    *,
    enable_toc_filter: bool = True,
    toc_max_start_page: int = 15,
    toc_max_follow_pages: int = 12,
    include_rule_candidates: bool = False,
) -> tuple[list[TitleCandidate], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Extract title candidates for all parts of one PDF and assign global order."""
    all_candidates: list[TitleCandidate] = []
    candidate_records: dict[int, dict[str, Any]] = {}
    part_summaries: list[dict[str, Any]] = []
    global_order = 0

    for record_index, record in enumerate(group.records):
        candidates, toc_detection = build_title_candidates(
            record,
            enable_toc_filter=enable_toc_filter,
            toc_max_start_page=toc_max_start_page,
            toc_max_follow_pages=toc_max_follow_pages,
            include_rule_candidates=include_rule_candidates,
        )
        part_no = _record_part_no(record)
        for candidate in candidates:
            candidate.record_index = record_index
            candidate.part_no = part_no
            candidate.global_order = global_order
            candidate.global_page_idx = _global_page_idx(record, candidate.page_idx)
            candidate_records[id(candidate)] = record
            all_candidates.append(candidate)
            global_order += 1
        part_summaries.append(
            {
                "domain": record.get("domain", ""),
                "doc_id": record.get("doc_id", ""),
                "part_no": part_no,
                "local_extract_dir": record.get("local_extract_dir", ""),
                "title_candidates": len(candidates),
                "toc_start_page": toc_detection.toc_start_page,
                "toc_pages": sorted(toc_detection.toc_pages),
                "toc_heading_candidates": len([c for c in candidates if c.is_toc_heading]),
                "toc_entry_candidates": len([c for c in candidates if c.is_toc_entry]),
                "rule_detected_candidates": len([c for c in candidates if c.rule_detected]),
                "source_artifact": candidates[0].source_artifact if candidates else "",
            }
        )
    return all_candidates, candidate_records, part_summaries


def split_prompt_candidates(candidates: list[TitleCandidate]) -> tuple[list[TitleCandidate], list[TitleCandidate]]:
    """Split candidates into TOC reference items and正文标题待增强 items."""
    toc_reference = [candidate for candidate in candidates if candidate.is_toc_heading or candidate.is_toc_entry]
    titles_to_enhance = [candidate for candidate in candidates if not candidate.is_toc_heading and not candidate.is_toc_entry]
    return toc_reference, titles_to_enhance


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


def _call_mock_llm(candidates: list[TitleCandidate], model: str) -> tuple[list[Any], LLMUsage]:
    """Return deterministic hierarchy corrections without network calls."""
    items: list[list[Any]] = []
    prompt_text = json.dumps({"titles": [c.to_prompt_json() for c in candidates]}, ensure_ascii=False)
    for candidate in candidates:
        level, is_title = _mock_level_for_text(candidate.text, candidate.raw_level)
        # Compact response format: [title_id, level]. level=0 means non-title.
        items.append([candidate.title_id, level if is_title else 0])
    output_text = json.dumps({"items": items}, ensure_ascii=False)
    usage = compute_usage_cost(
        model=model,
        prompt_tokens=estimate_tokens(prompt_text),
        completion_tokens=estimate_tokens(output_text),
    )
    return items, usage


def _parse_llm_items(content: str) -> list[Any]:
    """Parse model JSON content into compact [id, level] items."""
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
    return items


def _item_to_id_level(item: Any) -> tuple[str, int | None]:
    """Normalize one LLM item from compact array format.

    The expected format is [title_id, level], where level=0 means non-title.
    A dict fallback is accepted only to make error recovery easier during model
    output drift, but prompts always ask for the compact array format.
    """
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return str(item[0] or ""), _safe_int(item[1])
    if isinstance(item, dict):
        raw_level = item.get("level")
        if item.get("is_title") is False:
            raw_level = 0
        return str(item.get("id") or ""), _safe_int(raw_level)
    return "", None


def _validate_items(candidates: list[TitleCandidate], items: list[Any]) -> dict[str, dict[str, Any]]:
    """Validate and normalize compact LLM returned title items."""
    expected = {candidate.title_id: candidate for candidate in candidates}
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        title_id, level = _item_to_id_level(item)
        if title_id not in expected or title_id in result:
            continue
        if level is None:
            level = expected[title_id].raw_level or 2
        is_title = level > 0
        level = 0 if not is_title else max(1, min(6, level))
        result[title_id] = {"enhanced_level": level, "is_title": is_title}
    for title_id, candidate in expected.items():
        if title_id not in result:
            level, is_title = _mock_level_for_text(candidate.text, candidate.raw_level)
            result[title_id] = {"enhanced_level": level if is_title else 0, "is_title": is_title}
    return result


def build_prompt_payload(
    *,
    group: DocumentGroup,
    toc_reference: list[TitleCandidate],
    candidates: list[TitleCandidate],
) -> dict[str, Any]:
    """Build the minimal full-PDF JSON payload sent to the LLM."""
    return {
        "toc": [candidate.to_toc_prompt_json() for candidate in toc_reference],
        "titles": [candidate.to_prompt_json() for candidate in candidates],
    }




def enhance_document_candidates_by_rule(
    *,
    toc_reference: list[TitleCandidate],
    candidates: list[TitleCandidate],
    model: str,
) -> tuple[list[EnhancedTitle], LLMUsage]:
    """Enhance title levels deterministically with TOC anchors and numbering rules."""
    toc_level_map = build_toc_level_map(toc_reference)
    enhanced: list[EnhancedTitle] = []
    stack: list[EnhancedTitle] = []
    for candidate in candidates:
        level, is_title, reason = rule_level_for_candidate(candidate, toc_level_map=toc_level_map, stack=stack)
        item = EnhancedTitle(
            title_id=candidate.title_id,
            text=candidate.text,
            raw_title_level=candidate.raw_level,
            enhanced_title_level=level,
            is_title=is_title,
            page_idx=candidate.page_idx,
            bbox=candidate.bbox,
            order=candidate.order,
            source_artifact=candidate.source_artifact,
            is_toc_heading=False,
            is_toc_entry=False,
            part_no=candidate.part_no,
            global_order=candidate.global_order,
            global_page_idx=candidate.global_page_idx,
            record_index=candidate.record_index,
            rule_detected=candidate.rule_detected,
            rule_reason=reason or candidate.rule_reason,
        )
        enhanced.append(item)
        if item.is_title:
            while stack and stack[-1].enhanced_title_level >= item.enhanced_title_level:
                stack.pop()
            stack.append(item)
    return enhanced, LLMUsage(requests=0)

def enhance_document_candidates(
    *,
    group: DocumentGroup,
    toc_reference: list[TitleCandidate],
    candidates: list[TitleCandidate],
    options: HierarchyEnhanceOptions,
    client: OpenAICompatibleClient | None,
) -> tuple[list[EnhancedTitle], LLMUsage]:
    """Enhance all non-TOC title candidates of one complete PDF in one request."""
    if options.provider == "rule":
        return enhance_document_candidates_by_rule(toc_reference=toc_reference, candidates=candidates, model=options.model)
    if options.provider == "mock":
        items, usage = _call_mock_llm(candidates, options.model)
    else:
        if client is None:
            raise RuntimeError("provider 为 deepseek 或 qwen 时必须提供 LLM client")
        payload = build_prompt_payload(group=group, toc_reference=toc_reference, candidates=candidates)
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
                is_toc_heading=False,
                is_toc_entry=False,
                part_no=candidate.part_no,
                global_order=candidate.global_order,
                global_page_idx=candidate.global_page_idx,
                record_index=candidate.record_index,
                rule_detected=candidate.rule_detected,
                rule_reason=candidate.rule_reason,
            )
        )
    return enhanced, usage


def fixed_title_from_candidate(candidate: TitleCandidate) -> EnhancedTitle:
    """Create a deterministic enhanced record for TOC heading or TOC entry."""
    if candidate.is_toc_heading:
        return EnhancedTitle(
            title_id=candidate.title_id,
            text=candidate.text,
            raw_title_level=candidate.raw_level,
            enhanced_title_level=1,
            is_title=True,
            page_idx=candidate.page_idx,
            bbox=candidate.bbox,
            order=candidate.order,
            source_artifact=candidate.source_artifact,
            is_toc_heading=True,
            is_toc_entry=False,
            toc_reason=candidate.toc_reason,
            part_no=candidate.part_no,
            global_order=candidate.global_order,
            global_page_idx=candidate.global_page_idx,
            record_index=candidate.record_index,
            rule_detected=candidate.rule_detected,
            rule_reason=candidate.rule_reason,
        )
    return EnhancedTitle(
        title_id=candidate.title_id,
        text=candidate.text,
        raw_title_level=candidate.raw_level,
        enhanced_title_level=candidate.raw_level or 2,
        is_title=False,
        page_idx=candidate.page_idx,
        bbox=candidate.bbox,
        order=candidate.order,
        source_artifact=candidate.source_artifact,
        is_toc_heading=False,
        is_toc_entry=True,
        toc_reason=candidate.toc_reason,
        part_no=candidate.part_no,
        global_order=candidate.global_order,
        global_page_idx=candidate.global_page_idx,
        record_index=candidate.record_index,
        rule_detected=candidate.rule_detected,
        rule_reason=candidate.rule_reason,
    )


def assign_section_paths(titles: list[EnhancedTitle]) -> None:
    """Assign section_path to enhanced titles in-place."""
    stack: list[EnhancedTitle] = []
    for title in titles:
        if title.is_toc_heading:
            title.section_path = [title.text]
            continue
        if title.is_toc_entry or not title.is_title:
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
        if title.is_toc_heading or title.is_toc_entry or not title.is_title:
            continue
        while stack and stack[-1].enhanced_title_level >= title.enhanced_title_level:
            stack.pop()
        stack.append(title)
    return [{"level": item.enhanced_title_level, "text": item.text} for item in stack[-8:]]


def rewrite_markdown_headings(extract_dir: Path, titles: list[EnhancedTitle]) -> Path | None:
    """Write full_titleEnhanced.md by correcting existing and rule-detected Markdown headings."""
    full_md = extract_dir / "full.md"
    if not full_md.exists():
        candidates = sorted(extract_dir.glob("*full*.md"), key=lambda p: p.stat().st_size, reverse=True)
        full_md = candidates[0] if candidates else full_md
    if not full_md.exists():
        return None

    lines = full_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    title_queue = sorted(titles, key=lambda item: item.order)
    cursor = 0
    used_title_indexes: set[int] = set()
    heading_re = re.compile(r"^(#{1,6})(\s+)(.+?)(\s*)$")
    new_lines: list[str] = []

    def find_title_index(line_text: str, start_cursor: int, *, window: int) -> int | None:
        key = normalize_inline_text(line_text)
        if not key:
            return None
        search_ranges = [range(start_cursor, min(start_cursor + window, len(title_queue))), range(0, len(title_queue))]
        for search_range in search_ranges:
            for idx in search_range:
                if idx in used_title_indexes:
                    continue
                if normalize_inline_text(title_queue[idx].text) == key:
                    used_title_indexes.add(idx)
                    return idx
        return None

    for line in lines:
        match = heading_re.match(line)
        if match:
            heading_text = match.group(3)
            found_index = find_title_index(heading_text, cursor, window=45)
            if found_index is None:
                new_lines.append(line)
                continue
            title = title_queue[found_index]
            cursor = found_index + 1
            if not title.is_title or title.is_toc_entry:
                new_lines.append(f"{heading_text}{match.group(4)}")
                continue
            level = 1 if title.is_toc_heading else title.enhanced_title_level
            marks = "#" * max(1, min(6, level))
            new_lines.append(f"{marks}{match.group(2)}{heading_text}{match.group(4)}")
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith("!") or stripped.startswith("<"):
            new_lines.append(line)
            continue
        found_index = find_title_index(stripped, cursor, window=45)
        if found_index is None:
            new_lines.append(line)
            continue
        title = title_queue[found_index]
        cursor = found_index + 1
        if title.is_toc_entry or not title.is_title:
            new_lines.append(line)
            continue
        level = 1 if title.is_toc_heading else title.enhanced_title_level
        marks = "#" * max(1, min(6, level))
        leading = line[: len(line) - len(line.lstrip())]
        new_lines.append(f"{leading}{marks} {stripped}")

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


def _counter_to_json(counter: collections.Counter[int]) -> dict[str, int]:
    return {str(k): v for k, v in sorted(counter.items())}


def _log(message: str) -> None:
    """Print progress immediately so long LLM calls do not look frozen."""
    print(message, flush=True)


def _fmt_seconds(seconds: float) -> str:
    """Format elapsed seconds for readable command output."""
    return f"{seconds:.1f}s"


def _usage_line(prefix: str, usage: LLMUsage, *, elapsed_seconds: float | None = None) -> str:
    """Build a compact token and cost usage line."""
    data = usage.to_json()
    parts = [
        prefix,
        f"prompt_tokens={data.get('prompt_tokens', 0)}",
        f"completion_tokens={data.get('completion_tokens', 0)}",
        f"total_tokens={data.get('total_tokens', 0)}",
        f"cost_usd={data.get('total_cost_usd', 0)}",
    ]
    if elapsed_seconds is not None:
        parts.insert(1, f"elapsed={_fmt_seconds(elapsed_seconds)}")
    return ", ".join(parts)


def _record_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    """Sort MinerU parts in original PDF order as far as metadata allows."""
    return (
        _record_part_no(record),
        _safe_int(record.get("upload_page_start")) or 0,
        str(record.get("local_extract_dir") or ""),
    )


def _build_document_groups(records: list[dict[str, Any]], options: HierarchyEnhanceOptions) -> list[DocumentGroup]:
    """Group selected MinerU records by complete PDF instead of individual part."""
    if options.extract_dir:
        groups = [
            DocumentGroup(
                group_id="manual_extract_dir",
                domain=str(records[0].get("domain") or "manual"),
                doc_id=str(records[0].get("doc_id") or Path(str(records[0].get("local_extract_dir") or "manual")).name),
                records=records,
            )
        ]
        return groups[: options.limit_docs] if options.limit_docs else groups

    grouped: collections.OrderedDict[tuple[str, str], list[dict[str, Any]]] = collections.OrderedDict()
    for record in sorted(records, key=lambda row: (str(row.get("domain") or ""), str(row.get("doc_id") or ""), _record_sort_key(row))):
        key = (str(record.get("domain") or ""), str(record.get("doc_id") or ""))
        grouped.setdefault(key, []).append(record)

    groups: list[DocumentGroup] = []
    for (domain, doc_id), group_records in grouped.items():
        group_records = sorted(group_records, key=_record_sort_key)
        groups.append(
            DocumentGroup(
                group_id=f"{domain}/{doc_id}",
                domain=domain,
                doc_id=doc_id,
                records=group_records,
            )
        )
    return groups[: options.limit_docs] if options.limit_docs else groups


def _estimate_prompt_tokens_for_document(
    *,
    group: DocumentGroup,
    toc_reference: list[TitleCandidate],
    candidates: list[TitleCandidate],
) -> int:
    """Estimate prompt size before the PDF-level LLM call."""
    payload = build_prompt_payload(group=group, toc_reference=toc_reference, candidates=candidates)
    prompt_text = TITLE_SYSTEM_PROMPT + "\n" + json.dumps(payload, ensure_ascii=False)
    return estimate_tokens(prompt_text)


def enhance_hierarchy(options: HierarchyEnhanceOptions) -> dict[str, Any]:
    """Run PDF-level hierarchy enhancement and write output artifacts."""
    run_started = time.perf_counter()
    options.output_dir = Path(options.output_dir)
    records = _load_records_for_options(options)
    if not records:
        raise RuntimeError("没有找到需要增强标题层级的 MinerU 记录")

    groups = _build_document_groups(records, options)
    if not groups:
        raise RuntimeError("没有找到需要增强标题层级的 PDF 分组")

    _log(
        "[SELECT] "
        f"records={len(records)}, pdf_groups={len(groups)}, provider={options.provider}, model={options.model}, "
        f"mode=whole_pdf_one_request, timeout={options.timeout}s, retries={options.max_retries}"
    )
    client: OpenAICompatibleClient | None = None
    if options.provider == "deepseek":
        _log("[LLM] 初始化 DeepSeek/OpenAI-compatible client")
        client = OpenAICompatibleClient.from_deepseek_env(
            model=options.model,
            api_key=options.api_key,
            base_url=options.base_url,
            timeout=options.timeout,
            max_retries=options.max_retries,
            input_price_per_1m=options.input_price_per_1m,
            output_price_per_1m=options.output_price_per_1m,
        )
    elif options.provider == "qwen":
        _log("[LLM] 初始化 Qwen/DashScope OpenAI-compatible client")
        client = OpenAICompatibleClient.from_qwen_env(
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
    llm_request_count = 0
    llm_sent_total = 0
    toc_heading_total = 0
    toc_entry_total = 0
    non_title_total = 0
    toc_pages_total = 0
    rule_detected_total = 0

    for group_index, group in enumerate(groups, start=1):
        group_started = time.perf_counter()
        _log(f"[PDF] {group_index}/{len(groups)} {group.group_id}, parts={len(group.records)}")
        for part_index, part_record in enumerate(group.records, start=1):
            _log(
                f"[PART] {part_index}/{len(group.records)} "
                f"part={_record_part_no(part_record)}, extract_dir={part_record.get('local_extract_dir', '')}"
            )

        candidates, candidate_records_by_id_raw, part_summaries = prepare_group_candidates(
            group,
            enable_toc_filter=options.enable_toc_filter,
            toc_max_start_page=options.toc_max_start_page,
            toc_max_follow_pages=options.toc_max_follow_pages,
            include_rule_candidates=options.provider == "rule",
        )
        candidate_records_by_title_id = {candidate.title_id: candidate_records_by_id_raw[id(candidate)] for candidate in candidates}

        for candidate in candidates:
            if candidate.raw_level:
                raw_counts[candidate.raw_level] += 1

        if not candidates:
            _log(f"[EXTRACT] {group.group_id}: no title candidates found")
            docs_summary.append(
                {
                    "group_id": group.group_id,
                    "domain": group.domain,
                    "doc_id": group.doc_id,
                    "part_count": len(group.records),
                    "title_candidates": 0,
                    "enhanced_titles": 0,
                    "elapsed_seconds": round(time.perf_counter() - group_started, 3),
                    "warning": "no title candidates found",
                    "parts": part_summaries,
                }
            )
            continue

        toc_reference, llm_candidates = split_prompt_candidates(candidates)
        fixed_candidates = toc_reference
        enhanced_by_id: dict[str, EnhancedTitle] = {}
        for candidate in fixed_candidates:
            enhanced_by_id[candidate.title_id] = fixed_title_from_candidate(candidate)

        toc_heading_count = len([c for c in toc_reference if c.is_toc_heading])
        toc_entry_count = len([c for c in toc_reference if c.is_toc_entry])
        rule_detected_count = len([c for c in candidates if c.rule_detected])
        toc_page_sets = [set(part.get("toc_pages", [])) for part in part_summaries]
        toc_pages_in_group = sum(len(pages) for pages in toc_page_sets)
        toc_heading_total += toc_heading_count
        toc_entry_total += toc_entry_count
        toc_pages_total += toc_pages_in_group
        rule_detected_total += rule_detected_count
        llm_sent_total += len(llm_candidates)

        page_indexes = [candidate.global_page_idx for candidate in llm_candidates if candidate.global_page_idx is not None]
        page_range = f", global_pages={min(page_indexes)}-{max(page_indexes)}" if page_indexes else ""
        if options.provider == "rule":
            _log(
                f"[EXTRACT] {group.group_id}: titles={len(candidates)}, toc_reference={len(toc_reference)}, "
                f"toc_headings={toc_heading_count}, toc_entries={toc_entry_count}, "
                f"rule_detected={rule_detected_count}, rule_eval={len(llm_candidates)}{page_range}"
            )
        else:
            prompt_estimate = _estimate_prompt_tokens_for_document(
                group=group,
                toc_reference=toc_reference,
                candidates=llm_candidates,
            )
            _log(
                f"[EXTRACT] {group.group_id}: titles={len(candidates)}, toc_reference={len(toc_reference)}, "
                f"toc_headings={toc_heading_count}, toc_entries={toc_entry_count}, "
                f"llm_sent={len(llm_candidates)}, prompt_est_tokens≈{prompt_estimate}{page_range}"
            )

        if llm_candidates:
            if options.provider == "rule":
                _log(
                    f"[RULE] start provider=rule, model={options.model}, "
                    f"pdf={group.group_id}, titles={len(llm_candidates)}, toc_refs={len(toc_reference)}"
                )
            else:
                llm_request_count += 1
                _log(
                    f"[LLM] start request={llm_request_count}, provider={options.provider}, model={options.model}, "
                    f"pdf={group.group_id}, titles={len(llm_candidates)}, toc_refs={len(toc_reference)}"
                )
            request_started = time.perf_counter()
            enhanced_llm_titles, usage = enhance_document_candidates(
                group=group,
                toc_reference=toc_reference,
                candidates=llm_candidates,
                options=options,
                client=client,
            )
            request_elapsed = time.perf_counter() - request_started
            if options.provider == "rule":
                _log(f"[RULE] done elapsed={_fmt_seconds(request_elapsed)}, enhanced_candidates={len(enhanced_llm_titles)}")
            else:
                _log(_usage_line(f"[LLM] done request={llm_request_count}", usage, elapsed_seconds=request_elapsed))
            usage_total.add(usage)
            for item in enhanced_llm_titles:
                enhanced_by_id[item.title_id] = item
        else:
            _log(f"[ENHANCE] skipped {group.group_id}: no non-TOC title candidates")

        enhanced_titles = [enhanced_by_id[c.title_id] for c in candidates if c.title_id in enhanced_by_id]
        enhanced_titles.sort(key=lambda item: item.global_order if item.global_order is not None else item.order)
        assign_section_paths(enhanced_titles)

        group_enhanced_title_count = 0
        group_non_title_count = 0
        for title in enhanced_titles:
            if title.is_title:
                enhanced_counts[title.enhanced_title_level] += 1
                group_enhanced_title_count += 1
            else:
                non_title_total += 1
                group_non_title_count += 1
            row_record = candidate_records_by_title_id.get(title.title_id, group.records[0])
            all_rows.append(title.to_json(row_record, enhance_method=options.provider, model=options.model))

        group_written_md: list[str] = []
        for record_index, record in enumerate(group.records):
            extract_dir = resolve_path(str(record.get("local_extract_dir") or ""))
            part_titles = [title for title in enhanced_titles if title.record_index == record_index]
            enhanced_md_path: Path | None = None
            if options.write_enhanced_md and extract_dir is not None and extract_dir.exists():
                enhanced_md_path = rewrite_markdown_headings(extract_dir, part_titles)
                if enhanced_md_path is not None:
                    written_md.append(str(enhanced_md_path))
                    group_written_md.append(str(enhanced_md_path))
                    _log(f"[WRITE] enhanced_md={enhanced_md_path}")
                else:
                    _log(f"[WRITE] skipped: full.md not found in {extract_dir}")

        group_elapsed = time.perf_counter() - group_started
        _log(
            f"[PDF DONE] {group.group_id}: elapsed={_fmt_seconds(group_elapsed)}, "
            f"parts={len(group.records)}, enhanced_titles={group_enhanced_title_count}, non_titles={group_non_title_count}"
        )

        docs_summary.append(
            {
                "group_id": group.group_id,
                "domain": group.domain,
                "doc_id": group.doc_id,
                "part_count": len(group.records),
                "title_candidates": len(candidates),
                "toc_filter_enabled": options.enable_toc_filter,
                "toc_reference_candidates": len(toc_reference),
                "toc_heading_candidates": toc_heading_count,
                "toc_entry_candidates": toc_entry_count,
                "llm_sent_candidates": 0 if options.provider == "rule" else len(llm_candidates),
                "rule_evaluated_candidates": len(llm_candidates) if options.provider == "rule" else 0,
                "rule_detected_candidates": rule_detected_count,
                "llm_request_count": 0 if options.provider == "rule" else (1 if llm_candidates else 0),
                "enhanced_titles": group_enhanced_title_count,
                "non_title_candidates": group_non_title_count,
                "elapsed_seconds": round(group_elapsed, 3),
                "enhanced_markdown_files": group_written_md,
                "parts": part_summaries,
            }
        )

    title_hierarchy_path = options.output_dir / "title_hierarchy.jsonl"
    stats_path = options.output_dir / "hierarchy_stats.json"
    write_jsonl(title_hierarchy_path, all_rows)
    total_elapsed = time.perf_counter() - run_started
    stats = {
        "provider": options.provider,
        "model": options.model,
        "record_count": len(records),
        "pdf_group_count": len(groups),
        "title_record_count": len(all_rows),
        "llm_request_count": llm_request_count,
        "title_candidates_total": len(all_rows),
        "llm_sent_candidates_total": 0 if options.provider == "rule" else llm_sent_total,
        "rule_evaluated_candidates_total": llm_sent_total if options.provider == "rule" else 0,
        "toc_heading_candidates_total": toc_heading_total,
        "toc_entry_candidates_total": toc_entry_total,
        "toc_pages_total": toc_pages_total,
        "rule_detected_candidates_total": rule_detected_total,
        "non_title_candidates_total": non_title_total,
        "elapsed_seconds": round(total_elapsed, 3),
        "enhancement_mode": "rule_based" if options.provider == "rule" else "whole_pdf_one_request",
        "toc_filter": {
            "enabled": options.enable_toc_filter,
            "max_start_page": options.toc_max_start_page,
            "max_follow_pages": options.toc_max_follow_pages,
        },
        "raw_title_level_counts": _counter_to_json(raw_counts),
        "enhanced_title_level_counts": _counter_to_json(enhanced_counts),
        "usage": usage_total.to_json(),
        "outputs": {
            "title_hierarchy_jsonl": str(title_hierarchy_path),
            "hierarchy_stats_json": str(stats_path),
            "enhanced_markdown_files": written_md,
        },
        "documents": docs_summary,
    }
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"[WRITE] title_hierarchy={title_hierarchy_path}")
    _log(f"[WRITE] hierarchy_stats={stats_path}")
    _log(_usage_line("[RUN DONE]", usage_total, elapsed_seconds=total_elapsed))
    return stats
