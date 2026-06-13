"""Jina Reader HTML 解析客户端。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
import json
import os
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .dataset import SourceDocument, safe_path_component

JINA_READER_URL = "https://r.jina.ai/"


def make_retry_session() -> requests.Session:
    """创建用于 Jina Reader 的重试 Session。"""

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_jina_token() -> str:
    """读取 Jina API Token；未设置时返回空字符串。"""

    return os.getenv("JINA_API_KEY") or os.getenv("JINA_TOKEN") or ""


def is_rate_limit_response(resp: requests.Response, body: str) -> bool:
    """识别 Jina 限流响应。"""

    text = body.lower()
    return resp.status_code == 429 or "rate limit" in text or "too many requests" in text


def jina_read_html(
    session: requests.Session,
    html_path: Path,
    *,
    token: str = "",
    respond_with: str = "readerlm-v2",
    max_retries: int = 6,
    rate_limit_sleep: int = 30,
) -> str:
    """调用 Jina Reader，把本地 HTML 转为 Markdown 风格文本。"""

    html = html_path.read_text(encoding="utf-8", errors="ignore")
    pseudo_url = f"https://afac.local/{html_path.name}"
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Accept": "text/plain, text/markdown, */*",
        "X-Target-URL": pseudo_url,
        "X-Respond-With": respond_with,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: str = ""
    for attempt in range(1, max_retries + 1):
        resp = session.post(JINA_READER_URL, headers=headers, data=html.encode("utf-8"), timeout=180)
        body = resp.text or ""
        if resp.status_code == 200 and body.strip():
            return body.strip()

        last_error = f"HTTP {resp.status_code}: {body[:500]}"
        if is_rate_limit_response(resp, body):
            sleep_seconds = max(1, rate_limit_sleep * attempt)
            print(f"[JINA RATE LIMIT] {sleep_seconds}s 后重试：{html_path}")
            time.sleep(sleep_seconds)
            continue

        if resp.status_code in {500, 502, 503, 504} and attempt < max_retries:
            sleep_seconds = min(60, 2 * attempt)
            print(f"[JINA RETRY] {sleep_seconds}s 后重试：{html_path}")
            time.sleep(sleep_seconds)
            continue
        break

    raise RuntimeError(f"Jina Reader 解析失败：{html_path}；{last_error}")


@dataclass
class JinaBatchOptions:
    """Jina HTML 批处理参数。"""

    respond_with: str = "readerlm-v2"
    max_retries: int = 6
    rate_limit_sleep: int = 30
    request_gap_seconds: float = 0.2


def write_json(path: Path, data: Any) -> None:
    """写出 UTF-8 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_cached_full_md(extract_dir: Path) -> bool:
    """判断 Jina 文档是否已经有可复用 full.md。"""

    full_md = extract_dir / "full.md"
    return full_md.exists() and full_md.stat().st_size > 0


def run_jina_html_docs(
    docs: Sequence[SourceDocument],
    output_dir: Path,
    *,
    options: JinaBatchOptions,
) -> list[dict[str, Any]]:
    """批量处理 HTML 文档并生成与 MinerU 一致的 manifest 记录。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    token = get_jina_token()
    records: list[dict[str, Any]] = []

    with make_retry_session() as session:
        for idx, doc in enumerate(docs, start=1):
            safe_dir = safe_path_component(f"{doc.domain}__{doc.doc_id}__{doc.data_id[-10:]}", max_len=120)
            extract_dir = output_dir / safe_dir
            extract_dir.mkdir(parents=True, exist_ok=True)
            source_html = extract_dir / "source.html"
            full_md = extract_dir / "full.md"
            meta_path = extract_dir / "jina_meta.json"

            record = {
                **doc.to_json(),
                "local_extract_dir": str(extract_dir),
                "download_status": "not_started",
                "download_error": "",
                "jina_respond_with": options.respond_with,
            }

            try:
                source_html.write_text(doc.path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                if _has_cached_full_md(extract_dir):
                    record["download_status"] = "done"
                    record["cache_status"] = "hit"
                    print(f"[JINA SKIP] 已存在 full.md：{doc.rel_path}")
                else:
                    print(f"[JINA] {idx}/{len(docs)} {doc.rel_path}")
                    text = jina_read_html(
                        session,
                        doc.path,
                        token=token,
                        respond_with=options.respond_with,
                        max_retries=options.max_retries,
                        rate_limit_sleep=options.rate_limit_sleep,
                    )
                    full_md.write_text(text, encoding="utf-8")
                    write_json(
                        meta_path,
                        {
                            "source_file": doc.rel_path,
                            "data_id": doc.data_id,
                            "respond_with": options.respond_with,
                            "parser": "jina_reader",
                        },
                    )
                    record["download_status"] = "done"
                    record["cache_status"] = "miss"
                    if options.request_gap_seconds > 0:
                        time.sleep(options.request_gap_seconds)
            except Exception as exc:
                record["download_status"] = "failed"
                record["download_error"] = str(exc)

            records.append(record)

    write_json(output_dir / "jina_parse_summary.json", records)
    return records
