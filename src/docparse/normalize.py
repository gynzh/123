from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .artifacts import discover_artifacts, normalize_ws, read_markdown, resolve_artifact
from .dataset import SourceDocument, safe_path_component, write_jsonl


def handle_local_text_docs(docs: list[SourceDocument], output_dir: Path) -> list[dict[str, Any]]:
    """将 txt/md 等纯文本文件转换为与外部解析结果一致的 manifest 记录。
    """

    records: list[dict[str, Any]] = []
    for doc in docs:
        target_dir = output_dir / "local_text" / safe_path_component(
            f"{doc.domain}__{doc.doc_id}__{doc.data_id}"
        )
        target_dir.mkdir(parents=True, exist_ok=True)

        md_path = target_dir / "full.md"
        text = doc.path.read_text(encoding="utf-8", errors="ignore")
        md_path.write_text(normalize_ws(text), encoding="utf-8")

        record = {
            **doc.to_json(),
            "batch_no": 0,
            "batch_id": "local_text",
            "mineru_state": "done",
            "mineru_error": "",
            "mineru_error_code": "",
            "full_zip_url": "",
            "local_zip_path": "",
            "local_extract_dir": str(target_dir),
            "download_status": "done",
            "download_error": "",
            "model_version": "local_text",
            "upload_part_no": 1,
            "upload_total_parts": 1,
            "upload_page_start": None,
            "upload_page_end": None,
            "upload_total_pages": None,
            "upload_rel_path": doc.rel_path,
            "upload_path": str(doc.path),
        }
        records.append(record)
    return records


def enrich_records_with_artifacts(records: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    """补全每条解析记录对应的本地产物信息，并写出 manifest。
    """

    enriched: list[dict[str, Any]] = []
    for record in records:
        extract_dir = Path(record.get("local_extract_dir") or "")
        artifacts = discover_artifacts(extract_dir) if extract_dir.exists() else {}
        record = {**record, "artifacts": artifacts}

        markdown_text = read_markdown(extract_dir, artifacts) if artifacts else ""
        record["markdown_char_count"] = len(markdown_text)
        record["markdown_preview"] = markdown_text[:500]

        # 如果 ZIP 解压只在图片文件上失败，但 full.md/content_list 等核心文本产物仍存在，
        # 则该记录仍可视为文档解析可用。
        if record.get("download_status") != "done" and _has_text_artifact(record):
            record["parse_status"] = "usable_with_partial_extract"
        else:
            record["parse_status"] = "usable" if _has_text_artifact(record) else "missing_text_artifact"

        # 兼容旧字段名，避免历史脚本读取 index_status 时报错。
        record["index_status"] = record["parse_status"]
        enriched.append(record)

    write_jsonl(output_dir / "manifest.jsonl", enriched)
    (output_dir / "manifest.json").write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return enriched


def _has_text_artifact(record: dict[str, Any]) -> bool:
    extract_dir = Path(record.get("local_extract_dir") or "")
    artifacts = record.get("artifacts") or {}
    if not extract_dir.exists() or not artifacts:
        return False

    for key in ("markdown_path", "content_list_path", "content_list_v2_path", "html_path"):
        path = resolve_artifact(extract_dir, artifacts.get(key, ""))
        if path is not None and path.exists() and path.stat().st_size > 0:
            return True
    return False


def _is_record_usable(record: dict[str, Any]) -> bool:
    return record.get("download_status") == "done" or _has_text_artifact(record)


def _part_item(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "part_no": r.get("upload_part_no", 1),
        "total_parts": r.get("upload_total_parts", 1),
        "page_start": r.get("upload_page_start"),
        "page_end": r.get("upload_page_end"),
        "total_pages": r.get("upload_total_pages"),
        "upload_rel_path": r.get("upload_rel_path", r.get("rel_path")),
        "upload_path": r.get("upload_path", r.get("path")),
        "status": r.get("download_status"),
        "parse_status": r.get("parse_status", r.get("index_status", "")),
        "mineru_state": r.get("mineru_state"),
        "mineru_error_code": r.get("mineru_error_code", ""),
        "mineru_error": r.get("mineru_error", ""),
        "extract_dir": r.get("local_extract_dir"),
        "artifacts": r.get("artifacts", {}),
        "markdown_char_count": r.get("markdown_char_count", 0),
    }


def build_doc_id_map(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """按比赛 domain/doc_id 聚合解析分片，生成 doc_id_map.json。

    长 PDF 会被拆成多个上传分片，但它们仍属于同一个原始 doc_id。
    该映射文件用于后续阶段准确找到一个文档的所有解析产物。
    """

    mapping: dict[str, Any] = {}
    collisions: dict[str, list[dict[str, Any]]] = {}

    for r in records:
        key = f"{r.get('domain')}::{r.get('doc_id')}"
        part_item = _part_item(r)
        if key not in mapping:
            mapping[key] = {
                "domain": r.get("domain"),
                "doc_id": r.get("doc_id"),
                "source_rel_path": r.get("rel_path"),
                "source_file": r.get("path"),
                "engine": r.get("engine"),
                "model_version": r.get("model_version"),
                "status": r.get("download_status"),
                "parts": [part_item],
            }
            continue

        existing = mapping[key]
        # 同一源文件的多个记录是长 PDF 拆分分片，不是 doc_id 冲突。
        if existing.get("source_file") == r.get("path"):
            existing.setdefault("parts", []).append(part_item)
            statuses = [p.get("status") for p in existing.get("parts", [])]
            parse_statuses = [p.get("parse_status") for p in existing.get("parts", [])]
            if parse_statuses and all(
                s in {"usable", "usable_with_partial_extract"} for s in parse_statuses
            ):
                existing["parse_status"] = "usable"
            if statuses and all(s == "done" for s in statuses):
                existing["status"] = "done"
            elif any(s == "done" for s in statuses):
                existing["status"] = "partial_done"
            else:
                existing["status"] = statuses[0] if statuses else r.get("download_status")
        else:
            collisions.setdefault(key, [existing]).append({**part_item, "source_file": r.get("path")})

    for item in mapping.values():
        item["parts"] = sorted(item.get("parts", []), key=lambda x: int(x.get("part_no") or 0))

    out = {"mapping": mapping, "collisions": collisions}
    (output_dir / "doc_id_map.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def _doc_row_from_records(group: list[dict[str, Any]]) -> dict[str, Any]:
    group = sorted(group, key=lambda r: int(r.get("upload_part_no") or 1))
    first = group[0]
    parts = [_part_item(r) for r in group]
    markdown_char_count = sum(int(r.get("markdown_char_count") or 0) for r in group)
    preview = "\n".join(
        str(r.get("markdown_preview") or "") for r in group if r.get("markdown_preview")
    )[:500]

    statuses = [p.get("status") for p in parts]
    parse_statuses = [p.get("parse_status") for p in parts]
    return {
        "domain": first.get("domain"),
        "doc_id": first.get("doc_id"),
        "source_rel_path": first.get("rel_path"),
        "source_file": first.get("path"),
        "parse_engine": first.get("engine"),
        "model_version": first.get("model_version"),
        "status": "done" if statuses and all(s == "done" for s in statuses) else "partial_done",
        "parse_status": (
            "usable"
            if parse_statuses
            and all(s in {"usable", "usable_with_partial_extract"} for s in parse_statuses)
            else "partial_usable"
        ),
        "upload_total_parts": max(int(r.get("upload_total_parts") or 1) for r in group),
        "upload_total_pages": first.get("upload_total_pages"),
        "parts": parts,
        "markdown_char_count": markdown_char_count,
        "markdown_preview": preview,
    }


def build_parse_outputs(records: list[dict[str, Any]], output_dir: Path) -> None:
    """生成文档解析阶段的汇总文件。

    输出内容仅描述“解析产物是否存在、在哪里、属于哪个原始 doc_id”。
    """

    usable_records = [r for r in records if _is_record_usable(r)]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in usable_records:
        grouped.setdefault((str(r.get("domain")), str(r.get("doc_id"))), []).append(r)

    doc_rows = [_doc_row_from_records(group) for group in grouped.values()]
    doc_rows = sorted(doc_rows, key=lambda r: (str(r.get("domain")), str(r.get("doc_id"))))
    write_jsonl(output_dir / "parsed_documents.jsonl", doc_rows)

    stats = {
        "num_documents": len(doc_rows),
        "num_upload_parts": len(usable_records),
        "domains": sorted({str(r.get("domain")) for r in doc_rows}),
        "documents_by_domain": {
            domain: sum(1 for r in doc_rows if r.get("domain") == domain)
            for domain in sorted({str(r.get("domain")) for r in doc_rows})
        },
        "upload_parts_by_domain": {
            domain: sum(1 for r in usable_records if r.get("domain") == domain)
            for domain in sorted({str(r.get("domain")) for r in usable_records})
        },
        "upload_parts_by_engine": {
            engine: sum(1 for r in usable_records if r.get("engine") == engine)
            for engine in sorted({str(r.get("engine")) for r in usable_records})
        },
        "outputs": {
            "source_documents": "source_documents.jsonl",
            "manifest_jsonl": "manifest.jsonl",
            "manifest_json": "manifest.json",
            "doc_id_map": "doc_id_map.json",
            "parsed_documents": "parsed_documents.jsonl",
        },
    }
    (output_dir / "parse_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

