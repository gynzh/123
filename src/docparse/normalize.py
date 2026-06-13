"""解析结果归一化与文档级产物构建。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json
import shutil

from .artifacts import (
    content_list_to_text,
    discover_artifacts,
    load_json_if_exists,
    normalize_ws,
    read_markdown,
    resolve_artifact,
)
from .dataset import SourceDocument, safe_path_component, write_jsonl


def write_json(path: Path, data: Any) -> None:
    """写出 UTF-8 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_extract_dir(record: dict[str, Any]) -> Path:
    """返回一条解析记录对应的本地解析产物目录。"""

    return Path(str(record.get("local_extract_dir") or ""))


def handle_local_text_docs(docs: Iterable[SourceDocument], output_dir: Path) -> list[dict[str, Any]]:
    """处理 TXT/Markdown 文档。\n\n    本地文本不调用外部 API，而是直接复制成标准 full.md，随后进入统一\n    manifest 与 parsed_documents 流程。\n    """

    records: list[dict[str, Any]] = []
    local_root = output_dir / "local_text"
    for doc in docs:
        safe_dir = safe_path_component(f"{doc.domain}__{doc.doc_id}__{doc.data_id[-10:]}", max_len=120)
        extract_dir = local_root / safe_dir
        extract_dir.mkdir(parents=True, exist_ok=True)
        full_md = extract_dir / "full.md"
        source_copy = extract_dir / f"source{doc.suffix or '.txt'}"

        text = doc.path.read_text(encoding="utf-8", errors="ignore")
        full_md.write_text(normalize_ws(text), encoding="utf-8")
        shutil.copyfile(doc.path, source_copy)

        records.append(
            {
                **doc.to_json(),
                "local_extract_dir": str(extract_dir),
                "download_status": "done",
                "download_error": "",
                "parser": "local_text",
            }
        )
    return records


def _has_any_text_artifact(extract_dir: Path, artifacts: dict[str, str]) -> bool:
    """判断解析目录是否包含可用于文档级文本的产物。"""

    markdown = resolve_artifact(extract_dir, artifacts.get("markdown_path", ""))
    content_list = resolve_artifact(extract_dir, artifacts.get("content_list_path", ""))
    content_list_v2 = resolve_artifact(extract_dir, artifacts.get("content_list_v2_path", ""))
    return any(path and path.exists() and path.stat().st_size > 0 for path in (markdown, content_list, content_list_v2))


def enrich_records_with_artifacts(records: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    """为解析记录补充 artifact 字段和索引状态。"""

    enriched: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        extract_dir = _record_extract_dir(item)
        artifacts = discover_artifacts(extract_dir) if extract_dir.exists() else {}
        item["artifacts"] = artifacts

        if str(item.get("download_status")) == "done" and _has_any_text_artifact(extract_dir, artifacts):
            item["index_status"] = "usable"
        elif str(item.get("download_status")) == "done":
            item["index_status"] = "missing_text_artifact"
        else:
            item["index_status"] = "parse_failed"
        enriched.append(item)

    write_jsonl(output_dir / "manifest.jsonl", enriched)
    write_json(output_dir / "manifest.json", enriched)
    return enriched


def build_doc_id_map(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """按 domain::doc_id 建立源文件与解析片段映射。"""

    mapping: dict[str, Any] = {}
    for record in records:
        domain = str(record.get("domain") or "unknown")
        doc_id = str(record.get("doc_id") or "")
        key = f"{domain}::{doc_id}"
        bucket = mapping.setdefault(
            key,
            {
                "domain": domain,
                "doc_id": doc_id,
                "source_files": [],
                "records": [],
                "status": "missing",
                "collision": False,
            },
        )
        source_file = str(record.get("rel_path") or "")
        if source_file and source_file not in bucket["source_files"]:
            bucket["source_files"].append(source_file)
        bucket["records"].append(
            {
                "data_id": record.get("data_id", ""),
                "engine": record.get("engine", ""),
                "index_status": record.get("index_status", ""),
                "local_extract_dir": record.get("local_extract_dir", ""),
                "artifacts": record.get("artifacts", {}),
                "upload_part_no": record.get("upload_part_no"),
                "upload_total_parts": record.get("upload_total_parts"),
                "upload_page_start": record.get("upload_page_start"),
                "upload_page_end": record.get("upload_page_end"),
            }
        )
        if record.get("index_status") == "usable":
            bucket["status"] = "usable"

    for bucket in mapping.values():
        bucket["collision"] = len(set(bucket["source_files"])) > 1
        bucket["records"].sort(key=lambda x: (x.get("upload_part_no") or 0, str(x.get("data_id") or "")))

    write_json(output_dir / "doc_id_map.json", mapping)
    return mapping


def _text_from_record(record: dict[str, Any]) -> str:
    """从单条解析记录中读取最完整的文档文本。"""

    extract_dir = _record_extract_dir(record)
    artifacts = record.get("artifacts") or {}

    markdown_text = read_markdown(extract_dir, artifacts)
    if markdown_text:
        return markdown_text

    for key in ("content_list_path", "content_list_v2_path", "middle_json_path", "model_json_path"):
        json_path = resolve_artifact(extract_dir, artifacts.get(key, ""))
        data = load_json_if_exists(json_path)
        text = content_list_to_text(data) if data is not None else ""
        if text:
            return text

    return ""


def build_parse_outputs(records: list[dict[str, Any]], output_dir: Path) -> None:
    """生成文档解析阶段最终文档级产物。"""

    usable_records = [r for r in records if r.get("index_status") == "usable"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in usable_records:
        key = f"{record.get('domain', 'unknown')}::{record.get('doc_id', '')}"
        grouped.setdefault(key, []).append(record)

    parsed_documents: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        rows.sort(key=lambda r: (r.get("upload_part_no") or 0, str(r.get("data_id") or "")))
        texts = [_text_from_record(row) for row in rows]
        texts = [text for text in texts if text]
        if not texts:
            continue

        first = rows[0]
        parsed_documents.append(
            {
                "domain": first.get("domain", "unknown"),
                "doc_id": first.get("doc_id", ""),
                "source_files": sorted({str(r.get("rel_path") or "") for r in rows if r.get("rel_path")}),
                "engines": sorted({str(r.get("engine") or "") for r in rows if r.get("engine")}),
                "parts": [
                    {
                        "data_id": row.get("data_id", ""),
                        "upload_part_no": row.get("upload_part_no"),
                        "upload_total_parts": row.get("upload_total_parts"),
                        "upload_page_start": row.get("upload_page_start"),
                        "upload_page_end": row.get("upload_page_end"),
                        "local_extract_dir": row.get("local_extract_dir", ""),
                        "artifacts": row.get("artifacts", {}),
                    }
                    for row in rows
                ],
                "char_count": sum(len(text) for text in texts),
                "text": normalize_ws("\n\n".join(texts)),
            }
        )

    write_jsonl(output_dir / "parsed_documents.jsonl", parsed_documents)
    write_json(
        output_dir / "parse_stats.json",
        {
            "total_records": len(records),
            "usable_records": len(usable_records),
            "parsed_documents": len(parsed_documents),
            "failed_records": len([r for r in records if r.get("index_status") == "parse_failed"]),
            "missing_text_artifact_records": len([r for r in records if r.get("index_status") == "missing_text_artifact"]),
            "outputs": [
                "source_documents.jsonl",
                "manifest.jsonl",
                "manifest.json",
                "doc_id_map.json",
                "parsed_documents.jsonl",
                "parse_stats.json",
            ],
        },
    )


def remove_out_of_scope_outputs(output_dir: Path) -> None:
    """删除不属于文档解析阶段的输出文件。"""

    for path in output_dir.glob("corpus_*.jsonl"):
        if path.is_file():
            path.unlink()
