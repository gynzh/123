from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def rel_or_empty(path: Path | None, base: Path) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return str(path)


def find_first(root: Path, patterns: list[str]) -> Path | None:
    if not root.exists():
        return None

    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(root.rglob(pattern))

    if not matches:
        return None

    # Prefer shorter paths and then larger files for full content artifacts.
    matches = sorted(
        set(matches),
        key=lambda p: (len(p.parts), -p.stat().st_size if p.exists() else 0, p.name),
    )
    return matches[0]


def find_largest_markdown(root: Path) -> Path | None:
    candidates = [p for p in root.rglob("*.md") if p.is_file()]
    if not candidates:
        return None

    preferred = [p for p in candidates if p.name == "full.md" or p.name.endswith("_full.md")]
    pool = preferred or candidates
    return max(pool, key=lambda p: p.stat().st_size)


def discover_artifacts(extract_dir: Path) -> dict[str, str]:
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
    if not rel_path:
        return None

    path = Path(rel_path)
    if path.is_absolute():
        return path if path.exists() else None

    full = extract_dir / rel_path
    return full if full.exists() else None


def load_json_if_exists(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_markdown(extract_dir: Path, artifacts: dict[str, str]) -> str:
    md = resolve_artifact(extract_dir, artifacts.get("markdown_path", ""))
    if md is None:
        return ""
    return normalize_ws(md.read_text(encoding="utf-8", errors="ignore"))


def _append_value(parts: list[str], value: Any) -> None:
    """Append text-like values from MinerU artifacts.

    MinerU has slightly different JSON shapes across model versions. Some fields
    are strings, some are lists of strings, and some can be nested dictionaries.
    This helper keeps chunk building tolerant instead of failing on one variant.
    """
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
    """Convert one MinerU content item to plain text.

    Some MinerU content_list files are a flat list of dictionaries, while others
    are nested as pages/blocks and therefore pass a list at this level. The old
    implementation assumed every item was a dict and crashed with:
    AttributeError: 'list' object has no attribute 'get'.
    """
    if isinstance(item, list):
        return normalize_ws("\n".join(content_item_to_text(x) for x in item if x))

    if not isinstance(item, dict):
        return normalize_ws(str(item)) if item not in (None, "") else ""

    typ = str(item.get("type") or item.get("category") or "")
    parts: list[str] = []

    # text/equation usually use text.
    for key in ("text", "content", "html", "latex"):
        _append_value(parts, item.get(key))

    # Table/image/chart/code fields differ across MinerU versions.
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
