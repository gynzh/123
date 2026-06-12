from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
import hashlib
import json
import re

# MinerU 负责 PDF、Office、图片等版面复杂文档。
MINERU_FILE_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
}

# HTML 统一交给 Jina Reader API，避免混用本地 HTML 解析和 MinerU-HTML。
JINA_HTML_EXTS = {".html", ".htm"}

# 纯文本文件不需要外部解析服务。
LOCAL_TEXT_EXTS = {".txt", ".md", ".markdown"}

ALL_SUPPORTED_EXTS = MINERU_FILE_EXTS | JINA_HTML_EXTS | LOCAL_TEXT_EXTS


def slugify(value: str, *, max_len: int = 128) -> str:
    """生成外部 API 可接受的 ASCII 标识。

    注意：这个函数只用于 MinerU/Jina 的内部 data_id 或路径片段，不能用于比赛
    doc_id。AFAC 题目文件里的 doc_ids 使用原始文件名 stem，可能包含中文、全角
    标点等字符，必须原样保留。
    """

    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = value.strip("._-") or "item"
    return value[:max_len]


def exact_doc_id_from_stem(stem: str) -> str:
    """返回与题目 JSON 中 doc_ids 对齐的原始文档 ID。"""

    return stem.strip()


def safe_path_component(value: str, *, max_len: int = 160) -> str:
    """生成 Windows/Linux 都安全的路径片段，同时尽量保留可读中文。"""

    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    value = value.strip(" ._") or "item"
    return value[:max_len]


@dataclass(frozen=True)
class SourceDocument:
    """raw 目录中的一个源文档。

    doc_id 使用原始文件名 stem；data_id 是供外部 API 使用的稳定安全 ID。
    """

    path: Path
    raw_root: Path
    domain: str
    doc_id: str
    rel_path: str
    engine: str

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def data_id(self) -> str:
        """稳定、短小、低冲突的外部 API ID。"""

        rel_no_suffix = Path(self.rel_path).with_suffix("").as_posix()
        digest = hashlib.sha1(self.rel_path.encode("utf-8")).hexdigest()[:10]
        prefix = slugify(rel_no_suffix.replace("/", "__"), max_len=105)
        return f"{prefix}_{digest}"[:128]

    def to_json(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path)
        data["raw_root"] = str(self.raw_root)
        return data


def infer_domain(path: Path, raw_root: Path) -> str:
    """根据 raw/<domain>/... 推断文档所属领域。"""

    rel = path.relative_to(raw_root)
    if len(rel.parts) < 2:
        return "unknown"
    return rel.parts[0]


def infer_doc_id(path: Path, raw_root: Path) -> str:
    """推断题目引用的 doc_id。

    AFAC 的 question JSON 按原始文件名 stem 引用文档，不按 MinerU 安全化名称引用。
    因此这里不能做 slugify，否则 regulatory/txt 下的中文法规文件会匹配失败。
    """

    _ = raw_root  # 保留参数，便于后续按相对路径扩展规则。
    return exact_doc_id_from_stem(path.stem)


def detect_engine(path: Path) -> str:
    """根据扩展名选择解析引擎。"""

    suffix = path.suffix.lower()
    if suffix in LOCAL_TEXT_EXTS:
        return "local_text"
    if suffix in JINA_HTML_EXTS:
        return "jina_html"
    if suffix in MINERU_FILE_EXTS:
        return "mineru_vlm"
    return "unsupported"


def collect_source_documents(
    raw_root: Path,
    *,
    domains: Sequence[str] | None = None,
    recursive: bool = True,
) -> list[SourceDocument]:
    """扫描 raw 目录，返回可解析源文档列表。"""

    raw_root = raw_root.resolve()
    pattern = "**/*" if recursive else "*"
    wanted_domains = set(domains or [])
    docs: list[SourceDocument] = []

    for path in sorted(raw_root.glob(pattern)):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALL_SUPPORTED_EXTS:
            continue

        domain = infer_domain(path, raw_root)
        if wanted_domains and domain not in wanted_domains:
            continue

        engine = detect_engine(path)
        if engine == "unsupported":
            continue

        docs.append(
            SourceDocument(
                path=path.resolve(),
                raw_root=raw_root,
                domain=domain,
                doc_id=infer_doc_id(path, raw_root),
                rel_path=path.relative_to(raw_root).as_posix(),
                engine=engine,
            )
        )

    return docs


def group_by_engine(docs: Iterable[SourceDocument]) -> dict[str, list[SourceDocument]]:
    """按解析引擎分组，便于 parse 命令分阶段处理。"""

    grouped: dict[str, list[SourceDocument]] = {}
    for doc in docs:
        grouped.setdefault(doc.engine, []).append(doc)
    return grouped


def load_question_doc_ids(question_root: Path) -> dict[str, dict[str, set[str]]]:
    """读取问题集，统计每个 domain 的题号、题型和引用文档。"""

    result: dict[str, dict[str, set[str]]] = {}
    for file in sorted(question_root.glob("*.json")):
        try:
            questions = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue

        for q in questions:
            domain = str(q.get("domain") or file.stem.replace("_questions", ""))
            bucket = result.setdefault(
                domain,
                {"doc_ids": set(), "qids": set(), "types": set(), "answer_formats": set()},
            )
            for doc_id in q.get("doc_ids", []) or []:
                bucket["doc_ids"].add(str(doc_id))
            if q.get("qid"):
                bucket["qids"].add(str(q["qid"]))
            if q.get("type"):
                bucket["types"].add(str(q["type"]))
            if q.get("answer_format"):
                bucket["answer_formats"].add(str(q["answer_format"]))

    return result


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    """以 UTF-8 JSONL 写出记录。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    """读取 UTF-8 JSONL 文件。"""

    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
