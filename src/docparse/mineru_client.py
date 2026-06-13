"""MinerU v4 文档解析客户端。\n\n本模块负责复杂文档的上传、轮询、结果下载、解压和续跑复用。\n解析策略保持为：PDF/Office/图片使用 MinerU VLM；超过页数限制的 PDF\n自动拆分为多个上传文件，但 manifest 仍按原始 domain/doc_id 聚合。\n"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
import hashlib
import json
import os
import time
import zipfile

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, RequestException, SSLError, Timeout
from urllib3.util.retry import Retry

from .dataset import SourceDocument, safe_path_component, slugify

API_BASE = "https://mineru.net/api/v4"
APPLY_UPLOAD_URL = f"{API_BASE}/file-urls/batch"
BATCH_RESULT_URL = f"{API_BASE}/extract-results/batch/{{batch_id}}"


def is_rate_limit_error(exc: BaseException) -> bool:
    """识别 MinerU 限流错误。"""

    text = str(exc).lower()
    return (
        "http 429" in text
        or "too many requests" in text
        or "50 files/min" in text
        or "限流" in text
        or "rate limit" in text
    )


def wait_for_rate_window(last_apply_at: float | None, gap_seconds: int) -> None:
    """控制连续申请上传 URL 的间隔，降低触发限流的概率。"""

    if not last_apply_at or gap_seconds <= 0:
        return
    elapsed = time.monotonic() - last_apply_at
    sleep_seconds = gap_seconds - elapsed
    if sleep_seconds > 0:
        print(f"[RATE LIMIT] 等待 {sleep_seconds:.1f}s 后再申请下一批上传链接")
        time.sleep(sleep_seconds)


def make_retry_session() -> requests.Session:
    """创建带 HTTP 重试策略的 requests Session。"""

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_token() -> str:
    """读取 MinerU API Token。"""

    token = os.getenv("MINERU_API_KEY") or os.getenv("MINERU_TOKEN")
    if not token:
        raise RuntimeError("请先设置环境变量 MINERU_API_KEY 或 MINERU_TOKEN。")
    return token


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """发送请求并检查 MinerU 标准 JSON 响应。"""

    resp = session.request(method, url, headers=headers, timeout=120, **kwargs)
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"非 JSON 响应：HTTP {resp.status_code}, body={resp.text[:500]}") from exc

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP 请求失败：HTTP {resp.status_code}, body={data}")
    if data.get("code") != 0:
        raise RuntimeError(
            f"MinerU 业务失败：code={data.get('code')}, msg={data.get('msg')}, trace_id={data.get('trace_id')}"
        )
    return data


@dataclass(frozen=True)
class MinerUUploadDocument:
    """实际提交给 MinerU 的一个物理文件。\n\n    对于长 PDF，该文件可能是拆分后的 part；domain/doc_id 始终指向原始\n    SourceDocument，确保后续 parsed_documents 可以按原始文档聚合。\n    """

    source: SourceDocument
    upload_path: Path
    upload_rel_path: str
    part_no: int = 1
    total_parts: int = 1
    page_start: int | None = None
    page_end: int | None = None
    total_pages: int | None = None

    @property
    def path(self) -> Path:
        return self.upload_path

    @property
    def domain(self) -> str:
        return self.source.domain

    @property
    def doc_id(self) -> str:
        return self.source.doc_id

    @property
    def rel_path(self) -> str:
        return self.source.rel_path

    @property
    def engine(self) -> str:
        return self.source.engine

    @property
    def suffix(self) -> str:
        return self.upload_path.suffix.lower()

    @property
    def data_id(self) -> str:
        """为上传 part 生成稳定 data_id。"""

        if self.total_parts <= 1:
            return self.source.data_id
        marker = f"{self.source.rel_path}#part={self.part_no}#pages={self.page_start}-{self.page_end}"
        digest = hashlib.sha1(marker.encode("utf-8")).hexdigest()[:10]
        prefix = slugify(Path(self.source.rel_path).with_suffix("").as_posix().replace("/", "__"), max_len=86)
        page_tag = f"p{self.page_start or 0}-{self.page_end or 0}"
        return f"{prefix}_{page_tag}_{digest}"[:128]

    def to_json(self) -> dict[str, Any]:
        """转换为 manifest 可使用的字典。"""

        data = self.source.to_json()
        data.update(
            {
                "upload_path": str(self.upload_path),
                "upload_rel_path": self.upload_rel_path,
                "upload_part_no": self.part_no,
                "upload_total_parts": self.total_parts,
                "upload_page_start": self.page_start,
                "upload_page_end": self.page_end,
                "upload_total_pages": self.total_pages,
                "data_id": self.data_id,
            }
        )
        return data


def batched_uploads(items: Sequence[MinerUUploadDocument], size: int) -> Iterable[list[MinerUUploadDocument]]:
    """按 MinerU 批量接口限制拆分上传批次。"""

    if size <= 0 or size > 50:
        raise ValueError("MinerU batch size 必须在 1..50 之间。")
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _load_pypdf() -> tuple[Any, Any]:
    """延迟加载 pypdf，避免非 PDF 流程强制依赖。"""

    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except Exception as exc:
        raise RuntimeError("需要安装 pypdf 才能自动拆分超过 MinerU 页数限制的 PDF。请运行：pip install pypdf") from exc
    return PdfReader, PdfWriter


def split_pdf_for_mineru(
    doc: SourceDocument,
    split_root: Path,
    *,
    max_pages_per_part: int,
) -> list[MinerUUploadDocument]:
    """按页数把长 PDF 拆分为多个上传 part。"""

    PdfReader, PdfWriter = _load_pypdf()
    try:
        reader = PdfReader(str(doc.path))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                pass
        total_pages = len(reader.pages)
    except Exception as exc:
        message = str(exc)
        if "cryptography" in message and "AES" in message:
            raise RuntimeError("pypdf 读取 AES 加密 PDF 需要 cryptography，请运行：pip install cryptography") from exc
        raise

    if total_pages <= max_pages_per_part:
        return [
            MinerUUploadDocument(
                source=doc,
                upload_path=doc.path,
                upload_rel_path=doc.rel_path,
                part_no=1,
                total_parts=1,
                page_start=1,
                page_end=total_pages,
                total_pages=total_pages,
            )
        ]

    total_parts = (total_pages + max_pages_per_part - 1) // max_pages_per_part
    doc_part_dir = split_root / safe_path_component(doc.domain) / safe_path_component(doc.doc_id, max_len=120)
    doc_part_dir.mkdir(parents=True, exist_ok=True)

    upload_docs: list[MinerUUploadDocument] = []
    for part_no, start_idx in enumerate(range(0, total_pages, max_pages_per_part), start=1):
        end_idx = min(total_pages, start_idx + max_pages_per_part)
        page_start = start_idx + 1
        page_end = end_idx
        part_name = safe_path_component(
            f"{doc.doc_id}__part{part_no:03d}__p{page_start:04d}-{page_end:04d}.pdf",
            max_len=180,
        )
        part_path = doc_part_dir / part_name
        if not part_path.exists() or part_path.stat().st_size == 0:
            writer = PdfWriter()
            for page_idx in range(start_idx, end_idx):
                writer.add_page(reader.pages[page_idx])
            with part_path.open("wb") as f:
                writer.write(f)

        upload_docs.append(
            MinerUUploadDocument(
                source=doc,
                upload_path=part_path,
                upload_rel_path=f"{doc.rel_path}#part={part_no}/{total_parts}#pages={page_start}-{page_end}",
                part_no=part_no,
                total_parts=total_parts,
                page_start=page_start,
                page_end=page_end,
                total_pages=total_pages,
            )
        )
    return upload_docs


def prepare_upload_documents(
    docs: Sequence[SourceDocument],
    output_dir: Path,
    *,
    auto_split_pdf: bool,
    max_pdf_pages_per_upload: int,
) -> list[MinerUUploadDocument]:
    """把源文档转换为 MinerU 实际上传文档列表。"""

    if max_pdf_pages_per_upload <= 0 or max_pdf_pages_per_upload > 200:
        raise ValueError("max_pdf_pages_per_upload 必须在 1..200 之间。MinerU v4 批量解析单文件最多 200 页。")

    upload_docs: list[MinerUUploadDocument] = []
    split_root = output_dir / "_upload_parts"
    for doc in docs:
        if auto_split_pdf and doc.suffix == ".pdf":
            parts = split_pdf_for_mineru(doc, split_root, max_pages_per_part=max_pdf_pages_per_upload)
            if len(parts) > 1:
                print(
                    f"[SPLIT] {doc.rel_path}: {parts[0].total_pages} pages -> "
                    f"{len(parts)} parts, <= {max_pdf_pages_per_upload} pages/part"
                )
            upload_docs.extend(parts)
        else:
            upload_docs.append(
                MinerUUploadDocument(
                    source=doc,
                    upload_path=doc.path,
                    upload_rel_path=doc.rel_path,
                )
            )
    return upload_docs


def apply_upload_urls(
    session: requests.Session,
    token: str,
    docs: Sequence[MinerUUploadDocument],
    *,
    model_version: str,
    language: str = "ch",
    enable_formula: bool = True,
    enable_table: bool = True,
    is_ocr: bool = False,
    extra_formats: list[str] | None = None,
    page_ranges: str | None = None,
    no_cache: bool | None = None,
    rate_limit_retries: int = 8,
    rate_limit_sleep: int = 75,
) -> dict[str, Any]:
    """向 MinerU 申请一批预签名上传 URL。"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    files_payload: list[dict[str, Any]] = []
    for doc in docs:
        item: dict[str, Any] = {"name": doc.path.name, "data_id": doc.data_id, "is_ocr": is_ocr}
        if page_ranges:
            item["page_ranges"] = page_ranges
        files_payload.append(item)

    payload: dict[str, Any] = {
        "files": files_payload,
        "model_version": model_version,
        "language": language,
        "enable_formula": enable_formula,
        "enable_table": enable_table,
    }
    if extra_formats:
        payload["extra_formats"] = extra_formats
    if no_cache is not None:
        payload["no_cache"] = no_cache

    max_attempts = max(1, rate_limit_retries + 1)
    last_exc: RuntimeError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return request_json(session, "POST", APPLY_UPLOAD_URL, headers=headers, json=payload)["data"]
        except RuntimeError as exc:
            if not is_rate_limit_error(exc):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                break
            sleep_seconds = max(1, rate_limit_sleep)
            print(f"[RATE LIMIT] MinerU 返回限流：{exc}；{sleep_seconds}s 后重试（{attempt}/{max_attempts - 1}）")
            time.sleep(sleep_seconds)

    raise RuntimeError(f"MinerU 上传链接申请多次触发限流，已停止重试：{last_exc}")


def upload_files_to_presigned_urls(
    session: requests.Session,
    docs: Sequence[MinerUUploadDocument],
    file_urls: Sequence[str],
) -> None:
    """把文件上传到 MinerU 返回的预签名 URL。"""

    if len(docs) != len(file_urls):
        raise RuntimeError(f"文件数和上传 URL 数不一致：docs={len(docs)}, urls={len(file_urls)}")

    for doc, upload_url in zip(docs, file_urls):
        print(f"[UPLOAD] {doc.upload_rel_path}")
        with doc.path.open("rb") as f:
            resp = session.put(upload_url, data=f, timeout=600)
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"上传失败：{doc.upload_rel_path}, HTTP {resp.status_code}, body={resp.text[:500]}")
        print(f"[UPLOAD OK] {doc.upload_rel_path}")


def poll_batch_result(
    session: requests.Session,
    token: str,
    batch_id: str,
    *,
    interval: int = 10,
    max_wait: int = 3600,
) -> list[dict[str, Any]]:
    """轮询 MinerU 批处理结果直到所有文件进入终态。"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    start = time.time()
    final_states = {"done", "failed"}
    printed_failed: set[str] = set()

    while True:
        data = request_json(session, "GET", BATCH_RESULT_URL.format(batch_id=batch_id), headers=headers)
        results = data["data"].get("extract_result", [])
        if results:
            state_count: dict[str, int] = {}
            for item in results:
                state = item.get("state", "unknown")
                state_count[state] = state_count.get(state, 0) + 1
            print(f"[POLL] batch_id={batch_id}, 状态统计={state_count}")

            for item in results:
                if item.get("state") == "failed":
                    key = str(item.get("data_id") or item.get("file_name") or id(item))
                    if key not in printed_failed:
                        printed_failed.add(key)
                        print(
                            f"[FAILED DETAIL] file={item.get('file_name', '')}, "
                            f"data_id={item.get('data_id', '')}, err_code={item.get('err_code', '')}, "
                            f"err_msg={item.get('err_msg', '')}"
                        )

            if all(item.get("state") in final_states for item in results):
                return results
        else:
            print(f"[POLL] batch_id={batch_id}, 暂无结果")

        if time.time() - start > max_wait:
            raise TimeoutError(f"等待超时：batch_id={batch_id}")
        time.sleep(interval)


def is_valid_zip(zip_path: Path) -> bool:
    """检查 zip 是否存在且结构完整。"""

    if not zip_path.exists() or zip_path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return zf.testzip() is None
    except zipfile.BadZipFile:
        return False


def download_file_with_retries(
    url: str,
    target_path: Path,
    *,
    max_retries: int = 5,
    block_size: int = 1024 * 1024,
    timeout: tuple[int, int] = (30, 600),
) -> None:
    """下载 MinerU 结果 zip，失败时重试并使用 .part 临时文件。"""

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if is_valid_zip(target_path):
        print(f"[DOWNLOAD SKIP] 已存在有效 zip：{target_path}")
        return

    part_path = target_path.with_suffix(target_path.suffix + ".part")
    if part_path.exists():
        part_path.unlink()

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[DOWNLOAD TRY {attempt}/{max_retries}] {url}")
            headers = {
                "User-" + "A" + "gent": "Mozilla/5.0 AFAC2026-DocumentParser/1.0",
                "Accept": "application/zip,application/octet-stream,*/*",
                "Connection": "close",
            }
            with requests.Session() as download_session:
                with download_session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"下载失败：HTTP {resp.status_code}, body={resp.text[:500]}")
                    with part_path.open("wb") as f:
                        for block in resp.iter_content(block_size):
                            if block:
                                f.write(block)

            if not is_valid_zip(part_path):
                raise zipfile.BadZipFile(f"下载完成但不是有效 zip：{part_path}")
            part_path.replace(target_path)
            print(f"[DOWNLOAD OK] {target_path}")
            return
        except (SSLError, ConnectionError, Timeout, RequestException, OSError, zipfile.BadZipFile) as err:
            last_err = err
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass
            if attempt >= max_retries:
                break
            sleep_seconds = min(60, 3 * attempt)
            print(f"[DOWNLOAD WARN] 第 {attempt} 次下载失败：{err}")
            print(f"[DOWNLOAD RETRY] {sleep_seconds} 秒后重试...")
            time.sleep(sleep_seconds)

    raise RuntimeError(f"多次下载仍失败：{url}；最后错误：{last_err}")


def safe_extract_zip(zip_path: Path, output_dir: Path) -> list[str]:
    """安全解压 MinerU 结果 zip，阻止路径穿越。"""

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()

        def priority(member: zipfile.ZipInfo) -> int:
            name = member.filename.lower()
            if name.endswith((".md", ".json", ".html", ".txt")):
                return 0
            if name.endswith(".pdf"):
                return 1
            return 2

        for member in sorted(members, key=priority):
            raw_name = member.filename.replace("\\", "/")
            posix = PurePosixPath(raw_name)
            if posix.is_absolute() or ".." in posix.parts:
                raise RuntimeError(f"zip 中存在不安全路径：{member.filename}")

            if raw_name.endswith("/"):
                (output_dir / posix).mkdir(parents=True, exist_ok=True)
                continue

            target_path = (output_dir / posix).resolve()
            if not str(target_path).startswith(str(output_dir)):
                raise RuntimeError(f"zip 中存在不安全路径：{member.filename}")

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src, target_path.open("wb") as dst:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        dst.write(block)
            except OSError as exc:
                warnings.append(f"{member.filename}: {exc}")

    return warnings


@dataclass
class MinerUBatchOptions:
    """MinerU 批量解析参数。"""

    model_version: str = "vlm"
    language: str = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = False
    extra_formats: list[str] | None = None
    poll_interval: int = 10
    max_wait: int = 3600
    batch_size: int = 50
    download_retries: int = 5
    download_block_size_mb: int = 1
    page_ranges: str | None = None
    no_cache: bool | None = None
    auto_split_pdf: bool = True
    max_pdf_pages_per_upload: int = 200
    batch_apply_gap_seconds: int = 75
    rate_limit_retries: int = 8
    rate_limit_sleep: int = 75


def write_json(path: Path, data: Any) -> None:
    """写出 UTF-8 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_existing_summary(summary_path: Path) -> list[dict[str, Any]]:
    """读取已有 MinerU 解析摘要，用于自动续跑。"""

    if not summary_path.exists():
        return []
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def _has_text_artifact(record: dict[str, Any]) -> bool:
    """判断记录对应解压目录是否已有可用文本产物。"""

    extract_dir = Path(record.get("local_extract_dir") or "")
    if not extract_dir.exists():
        return False
    patterns = ["*.md", "*content_list*.json", "content_list*.json"]
    return any(path.is_file() and path.stat().st_size > 0 for pattern in patterns for path in extract_dir.rglob(pattern))


def _record_is_complete(record: dict[str, Any]) -> bool:
    """判断一条历史记录是否可复用。"""

    return str(record.get("download_status")) == "done" and _has_text_artifact(record)


def _existing_records_by_data_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 data_id 建立历史记录映射，优先保留完整记录。"""

    result: dict[str, dict[str, Any]] = {}
    for record in records:
        data_id = str(record.get("data_id") or "")
        if not data_id:
            continue
        if data_id not in result or _record_is_complete(record):
            result[data_id] = record
    return result


def _record_paths_for_doc(batch_dir: Path, doc: MinerUUploadDocument) -> tuple[Path, Path]:
    """计算单个上传文件的解压目录和 zip 路径。"""

    part_tag = f"part{doc.part_no:03d}"
    safe_dir_name = safe_path_component(f"{doc.domain}__{doc.doc_id}__{part_tag}__{doc.data_id[-10:]}", max_len=96)
    extract_dir = batch_dir / safe_dir_name
    zip_path = extract_dir / "mineru_result.zip"
    return extract_dir, zip_path


def run_mineru_batches(
    docs: Sequence[SourceDocument],
    output_dir: Path,
    *,
    token: str,
    options: MinerUBatchOptions,
) -> list[dict[str, Any]]:
    """提交文档到 MinerU，并复用已经成功解析的历史结果。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    upload_docs = prepare_upload_documents(
        docs,
        output_dir,
        auto_split_pdf=options.auto_split_pdf,
        max_pdf_pages_per_upload=options.max_pdf_pages_per_upload,
    )
    if len(upload_docs) != len(docs):
        print(f"[INFO] MinerU 实际上传文件数：{len(upload_docs)}（原始文档数：{len(docs)}，长 PDF 已自动拆分）")

    summary_path = output_dir / "mineru_parse_summary.json"
    existing_records = read_existing_summary(summary_path)
    existing_by_data_id = _existing_records_by_data_id(existing_records)

    all_records: list[dict[str, Any]] = []
    pending_docs: list[MinerUUploadDocument] = []
    for doc in upload_docs:
        existing = existing_by_data_id.get(doc.data_id)
        if existing and _record_is_complete(existing):
            all_records.append(existing)
        else:
            pending_docs.append(doc)

    print(f"[MINERU RESUME] total_upload_parts={len(upload_docs)}, done_skipped={len(all_records)}, pending={len(pending_docs)}")
    if not pending_docs:
        write_json(summary_path, all_records)
        return all_records
    if not token:
        raise RuntimeError("存在待解析的 MinerU 文档，但未设置 MINERU_API_KEY 或 MINERU_TOKEN。")

    with make_retry_session() as session:
        last_apply_at: float | None = None
        start_batch_no = 1 + max((int(r.get("batch_no") or 0) for r in all_records), default=0)
        for offset, batch_docs in enumerate(batched_uploads(list(pending_docs), options.batch_size), start=0):
            batch_no = start_batch_no + offset
            print(f"\n========== MINERU BATCH {batch_no}: {len(batch_docs)} files ==========")
            wait_for_rate_window(last_apply_at, options.batch_apply_gap_seconds)
            apply_data = apply_upload_urls(
                session,
                token,
                batch_docs,
                model_version=options.model_version,
                language=options.language,
                enable_formula=options.enable_formula,
                enable_table=options.enable_table,
                is_ocr=options.is_ocr,
                extra_formats=options.extra_formats,
                page_ranges=options.page_ranges,
                no_cache=options.no_cache,
                rate_limit_retries=options.rate_limit_retries,
                rate_limit_sleep=options.rate_limit_sleep,
            )
            last_apply_at = time.monotonic()
            batch_id = apply_data["batch_id"]
            file_urls = apply_data["file_urls"]
            print(f"[BATCH ID] {batch_id}")

            upload_files_to_presigned_urls(session, batch_docs, file_urls)
            results = poll_batch_result(
                session,
                token,
                str(batch_id),
                interval=options.poll_interval,
                max_wait=options.max_wait,
            )
            results_by_data_id = {str(r.get("data_id") or ""): r for r in results}
            batch_dir = output_dir / f"batch_{batch_no}_{str(batch_id)[:8]}"

            for doc in batch_docs:
                item = results_by_data_id.get(doc.data_id) or next(
                    (r for r in results if r.get("file_name") == doc.path.name),
                    {},
                )
                state = item.get("state", "unknown")
                zip_url = item.get("full_zip_url", "") or ""
                err_msg = item.get("err_msg", "") or ""
                err_code = item.get("err_code", "") or ""
                extract_dir, zip_path = _record_paths_for_doc(batch_dir, doc)
                record = {
                    **doc.to_json(),
                    "batch_no": batch_no,
                    "batch_id": batch_id,
                    "mineru_state": state,
                    "mineru_error": err_msg,
                    "mineru_error_code": err_code,
                    "full_zip_url": zip_url,
                    "local_zip_path": str(zip_path),
                    "local_extract_dir": str(extract_dir),
                    "download_status": "not_started",
                    "download_error": "",
                    "model_version": options.model_version,
                }

                if state == "done" and zip_url:
                    try:
                        download_file_with_retries(
                            zip_url,
                            zip_path,
                            max_retries=options.download_retries,
                            block_size=options.download_block_size_mb * 1024 * 1024,
                        )
                        extract_warnings = safe_extract_zip(zip_path, extract_dir)
                        record["download_status"] = "done"
                        if extract_warnings:
                            record["extract_warnings"] = extract_warnings[:20]
                            record["download_error"] = "; ".join(extract_warnings[:3])
                    except Exception as exc:
                        record["download_status"] = "download_or_extract_failed"
                        record["download_error"] = str(exc)
                elif state == "failed":
                    record["download_status"] = "skip_failed"
                else:
                    record["download_status"] = "skip_not_done_or_no_zip"

                all_records.append(record)
                existing_by_data_id[doc.data_id] = record
                write_json(summary_path, all_records)

    return all_records
