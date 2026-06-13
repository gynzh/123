"""解析产物发现、读取与文本规整。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re


def rel_or_empty(path: Path | None, base: Path) -> str:
    """把路径转换为相对 base 的 POSIX 路径；不存在则返回空字符串。"""

    if path is None:
        return ""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return str(path)


def find_first(root: Path, patterns: list[str]) -> Path | None:
    """按多个 glob 模式查找第一个优先产物。\n\n    排序规则优先选择路径更短的文件，再选择文件体积更大的文件，最后按文件名稳定排序。\n    """

    if not root.exists():
        return None

    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in root.rglob(pattern) if path.is_file())

    if not matches:
        return None

    matches = sorted(
        set(matches),
        key=lambda p: (len(p.parts), -p.stat().st_size if p.exists() else 0, p.name),
    )
    return matches[0]


def find_largest_markdown(root: Path) -> Path | None:
    """在解析目录中查找最适合作为全文文本的 Markdown 文件。"""

    candidates = [p for p in root.rglob("*.md") if p.is_file()]
    if not candidates:
        return None

    preferred = [p for p in candidates if p.name == "full.md" or p.name.endswith("_full.md")]
    pool = preferred or candidates
    return max(pool, key=lambda p: p.stat().st_size)


def discover_artifacts(extract_dir: Path) -> dict[str, str]:
    """发现一个解析目录中的标准产物。\n\n    返回值中的路径均相对 extract_dir，便于 manifest 在不同机器间迁移。\n    """

    extract_dir = Path(extract_dir)
    md_path = find_largest_markdown(extract_dir)
    content_list = find_first(
        extract_dir,
        ["*_content_list.json", "content_list.json", "*content_list*.json"],
    )
    content_list_v2 = find_first(
        extract_dir,
        ["*_content_list_v2.json", "content_list_v2.json", "*content_list_v2*.json"],
    )
    middle_json = find_first(
        extract_dir,
        ["*_middle.json", "middle.json", "layout.json", "*middle*.json"],
    )
    model_json = find_first(extract_dir, ["*_model.json", "model.json", "*model*.json"])
    layout_pdf = find_first(extract_dir, ["*_layout.pdf", "layout.pdf"])
    html_path = find_first(extract_dir, ["main.html", "*.html"])

    return {
        "markdown_path": rel_or_empty(md_path, extract_dir),
        "content_list_path": rel_or_empty(content_list, extract_dir),
        "content_list_v2_path": rel_or_empty(content_list_v2, extract_dir),
        "middle_json_path": rel_or_empty(middle_json, extract_dir),
        "model_json_path": rel_or_empty(model_json, extract_dir),
        "layout_pdf_path": rel_or_empty(layout_pdf, extract_dir),
        "html_path": rel_or_empty(html_path, extract_dir),
    }


def resolve_artifact(extract_dir: Path, rel_path: str) -> Path | None:
    """把 manifest 中记录的相对产物路径解析为本地路径。"""

    if not rel_path:
        return None
    path = Path(rel_path)
    if path.is_absolute():
        return path if path.exists() else None
    full = extract_dir / rel_path
    return full if full.exists() else None


def load_json_if_exists(path: Path | None) -> Any:
    """读取 JSON 文件；文件不存在时返回 None。"""

    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_ws(text: str) -> str:
    """统一换行和空白，保留段落边界。"""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_markdown(extract_dir: Path, artifacts: dict[str, str]) -> str:
    """读取解析目录中的 Markdown 全文。"""

    md = resolve_artifact(extract_dir, artifacts.get("markdown_path", ""))
    if md is None:
        return ""
    return normalize_ws(md.read_text(encoding="utf-8", errors="ignore"))


def _append_value(parts: list[str], value: Any) -> None:
    """从 MinerU 结构化字段中递归提取可读文本。"""

    if value is None or value == "":
        return
    if isinstance(value, str):
        if value.strip():
            parts.append(value)
        return
    if isinstance(value, (int, float)):
        parts.append(str(value))
        return
    if isinstance(value, list):
        for item in value:
            _append_value(parts, item)
        return
    if isinstance(value, dict):
        for key in ("text", "content", "html", "latex", "body", "caption"):
            if key in value:
                _append_value(parts, value.get(key))
        return


def content_item_to_text(item: Any) -> str:
    """把一个 MinerU content item 转为普通文本。\n\n    MinerU 不同模型或导出格式可能出现扁平 dict、嵌套 list 或非标准字段；\n    本函数只做文本抽取，不改变解析结果本身。\n    """

    if isinstance(item, list):
        return normalize_ws("\n".join(content_item_to_text(x) for x in item if x))
    if not isinstance(item, dict):
        return normalize_ws(str(item)) if item not in (None, "") else ""

    typ = str(item.get("type") or item.get("category") or "")
    parts: list[str] = []

    for key in ("text", "content", "html", "latex"):
        _append_value(parts, item.get(key))

    for key in [
        "table_caption",
        "table_body",
        "table_footnote",
        "image_caption",
        "image_footnote",
        "chart_caption",
        "chart_footnote",
        "code_body",
        "code_caption",
        "code_footnote",
        "caption",
        "footnote",
    ]:
        _append_value(parts, item.get(key))

    text = "\n".join(p for p in parts if p).strip()
    if not text and typ in {"image", "chart", "table"} and item.get("img_path"):
        text = f"[{typ}: {item.get('img_path')}]"
    return normalize_ws(text)


def content_list_to_text(data: Any) -> str:
    """从 content_list、middle/model 等结构化 JSON 中抽取文档级文本。"""

    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            item_text = content_item_to_text(node)
            if item_text:
                texts.append(item_text)
            for key in ("pages", "blocks", "content", "children", "items", "pdf_info", "layout_blocks", "preproc_blocks"):
                child = node.get(key)
                if child is not None:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return normalize_ws("\n\n".join(texts))
