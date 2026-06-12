from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
import json
import os
import time

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, RequestException, SSLError, Timeout
from urllib3.util.retry import Retry

from .dataset import SourceDocument, safe_path_component

JINA_READER_URL = "https://r.jina.ai/"


def make_retry_session() -> requests.Session:
    """创建带基础重试能力的 Jina HTTP Session。"""

    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_jina_token() -> str:
    """读取 Jina API Key；没有配置时使用匿名请求。"""

    return os.getenv("JINA_API_KEY") or os.getenv("JINA_TOKEN") or ""


def _is_rate_limited(status_code: int, text: str) -> bool:
    lower = text.lower()
    return status_code == 429 or "rate limit" in lower or "too many requests" in lower


def read_existing_summary(summary_path: Path) -> list[dict[str, Any]]:
    """读取已完成的 Jina 解析摘要，用于幂等续跑。"""

    if not summary_path.exists():
        return []
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _markdown_is_usable(record: dict[str, Any]) -> bool:
    """判断已有 Jina 记录是否可以直接复用。"""

    if record.get("download_status") != "done":
        return False
    extract_dir = Path(record.get("local_extract_dir") or "")
    md_path = extract_dir / "full.md"
    return md_path.exists() and md_path.stat().st_size > 0


def _target_dir(output_dir: Path, doc: SourceDocument) -> Path:
    return output_dir / safe_path_component(f"{doc.domain}__{doc.doc_id}__{doc.data_id}", max_len=110)


def _record_for_existing_file(doc: SourceDocument, target_dir: Path) -> dict[str, Any] | None:
    """兼容 summary 丢失但 full.md 仍存在的情况。"""

    md_path = target_dir / "full.md"
    if not md_path.exists() or md_path.stat().st_size == 0:
        return None

    record = _base_record(doc, target_dir)
    record.update(
        {
            "jina_state": "done",
            "download_status": "done",
            "download_error": "",
            "jina_cached": True,
        }
    )
    return record


def _base_record(doc: SourceDocument, target_dir: Path) -> dict[str, Any]:
    """生成与 MinerU 记录兼容的标准 manifest 记录。"""

    return {
        **doc.to_json(),
        "batch_no": 0,
        "batch_id": "jina_html",
        "mineru_state": "done",
        "mineru_error": "",
        "mineru_error_code": "",
        "jina_state": "not_started",
        "jina_error": "",
        "full_zip_url": "",
        "local_zip_path": "",
        "local_extract_dir": str(target_dir),
        "download_status": "not_started",
        "download_error": "",
        "model_version": "jina-readerlm-v2",
        "upload_part_no": 1,
        "upload_total_parts": 1,
        "upload_page_start": None,
        "upload_page_end": None,
        "upload_total_pages": None,
        "upload_rel_path": doc.rel_path,
        "upload_path": str(doc.path),
    }


def _reference_url(doc: SourceDocument) -> str:
    """为本地 HTML 构造稳定的伪 URL，供 Jina 解析相对路径。"""

    rel = doc.rel_path.replace(" ", "%20")
    return f"https://afac.local/{rel}"


def jina_read_html(
    session: requests.Session,
    doc: SourceDocument,
    *,
    token: str = "",
    respond_with: str = "readerlm-v2",
    timeout: tuple[int, int] = (30, 300),
    max_retries: int = 6,
    rate_limit_sleep: int = 30,
) -> str:
    """调用 Jina Reader API 将本地 HTML 内容转换为 Markdown。

    这里采用 raw HTML POST 方式：读取本地 HTML 内容放入 payload["html"]。
    不使用本地 fallback，避免最终语料混入不同 HTML 解析模式。
    """

    html = doc.path.read_text(encoding="utf-8", errors="ignore")
    payload = {"html": html, "url": _reference_url(doc)}
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/markdown",
        "x-respond-with": respond_with,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.post(JINA_READER_URL, headers=headers, json=payload, timeout=timeout)
        except (RequestException, Timeout, ConnectionError, SSLError) as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                break
            sleep_seconds = min(90, rate_limit_sleep * attempt)
            print(f"[JINA RETRY] {doc.rel_path}: {last_error}; {sleep_seconds}s 后重试")
            time.sleep(sleep_seconds)
            continue

        text = resp.text or ""
        if resp.status_code == 200 and text.strip():
            return text

        last_error = f"HTTP {resp.status_code}: {text[:500]}"
        if _is_rate_limited(resp.status_code, text) and attempt < max_retries:
            sleep_seconds = min(120, rate_limit_sleep * attempt)
            print(f"[JINA RATE LIMIT] {doc.rel_path}: {last_error}; {sleep_seconds}s 后重试")
            time.sleep(sleep_seconds)
            continue

        if 500 <= resp.status_code < 600 and attempt < max_retries:
            sleep_seconds = min(90, 5 * attempt)
            print(f"[JINA RETRY] {doc.rel_path}: {last_error}; {sleep_seconds}s 后重试")
            time.sleep(sleep_seconds)
            continue

        break

    raise RuntimeError(f"Jina HTML 解析失败：{doc.rel_path}; {last_error}")


@dataclass
class JinaBatchOptions:
    respond_with: str = "readerlm-v2"
    max_retries: int = 6
    rate_limit_sleep: int = 30
    request_gap_seconds: float = 0.2


def run_jina_html_docs(
    docs: Sequence[SourceDocument],
    output_dir: Path,
    *,
    options: JinaBatchOptions | None = None,
) -> list[dict[str, Any]]:
    """批量解析 HTML，并自动复用已成功结果。"""

    options = options or JinaBatchOptions()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "jina_parse_summary.json"

    existing_records = read_existing_summary(summary_path)
    existing_by_data_id: dict[str, dict[str, Any]] = {}
    for record in existing_records:
        data_id = str(record.get("data_id") or "")
        if data_id and _markdown_is_usable(record):
            existing_by_data_id[data_id] = record

    records: list[dict[str, Any]] = []
    pending: list[SourceDocument] = []
    for doc in docs:
        cached = existing_by_data_id.get(doc.data_id)
        if cached and _markdown_is_usable(cached):
            records.append(cached)
            continue

        target_dir = _target_dir(output_dir, doc)
        existing_file_record = _record_for_existing_file(doc, target_dir)
        if existing_file_record is not None:
            records.append(existing_file_record)
            continue

        pending.append(doc)

    print(f"[JINA RESUME] total_html={len(docs)}, done_skipped={len(records)}, pending={len(pending)}")
    if not pending:
        write_json(summary_path, records)
        return records

    token = get_jina_token()
    with make_retry_session() as session:
        for idx, doc in enumerate(pending, start=1):
            target_dir = _target_dir(output_dir, doc)
            target_dir.mkdir(parents=True, exist_ok=True)

            print(f"[JINA] ({idx}/{len(pending)}) {doc.rel_path}")
            markdown = jina_read_html(
                session,
                doc,
                token=token,
                respond_with=options.respond_with,
                max_retries=options.max_retries,
                rate_limit_sleep=options.rate_limit_sleep,
            )

            (target_dir / "source.html").write_text(doc.path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            (target_dir / "full.md").write_text(markdown.strip() + "\n", encoding="utf-8")
            write_json(
                target_dir / "jina_meta.json",
                {
                    "api": "jina_reader",
                    "endpoint": JINA_READER_URL,
                    "respond_with": options.respond_with,
                    "reference_url": _reference_url(doc),
                    "source_rel_path": doc.rel_path,
                    "doc_id": doc.doc_id,
                    "domain": doc.domain,
                },
            )

            record = _base_record(doc, target_dir)
            record.update(
                {
                    "jina_state": "done",
                    "jina_error": "",
                    "download_status": "done",
                    "download_error": "",
                }
            )
            records.append(record)
            write_json(summary_path, records)

            if options.request_gap_seconds > 0:
                time.sleep(options.request_gap_seconds)

    return records
