from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
import hashlib
import json
import re

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
JINA_HTML_EXTS = {".html", ".htm"}
LOCAL_TEXT_EXTS = {".txt", ".md", ".markdown"}
ALL_SUPPORTED_EXTS = MINERU_FILE_EXTS | JINA_HTML_EXTS | LOCAL_TEXT_EXTS


def slugify(value: str, *, max_len: int = 128) -> str:
    """Return an ASCII identifier accepted by external APIs.

    Do not use this for competition doc_id. AFAC question files use Unicode
    file stems directly, especially in raw/regulatory/txt.
    """
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = value.strip("._-") or "item"
    return value[:max_len]


def exact_doc_id_from_stem(stem: str) -> str:
    """Return the exact AFAC doc_id represented by a source filename."""
    return stem.strip()


def safe_path_component(value: str, *, max_len: int = 160) -> str:
    """Make a Windows-safe path component while preserving readable Unicode."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    value = value.strip(" ._") or "item"
    return value[:max_len]


@dataclass(frozen=True)
class SourceDocument:
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
        # External APIs usually need compact ASCII IDs. Keep AFAC doc_id exact
        # separately, and add a hash to avoid collisions after normalization.
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
    rel = path.relative_to(raw_root)
    if len(rel.parts) < 2:
        return "unknown"
    return rel.parts[0]


def infer_doc_id(path: Path, raw_root: Path) -> str:
    """Infer the doc_id used by the questions.

    AFAC question JSON references source documents by the original filename stem,
    not by a MinerU-safe slug. This matters for raw/regulatory/txt/*.txt, whose
    doc_ids contain Chinese characters and full-width punctuation.
    """
    return exact_doc_id_from_stem(path.stem)


def detect_engine(path: Path) -> str:
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
    grouped: dict[str, list[SourceDocument]] = {}
    for doc in docs:
        grouped.setdefault(doc.engine, []).append(doc)
    return grouped


def load_question_doc_ids(question_root: Path) -> dict[str, dict[str, set[str]]]:
    """Return domain -> answer_format/type/qids metadata about referenced doc_ids."""
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
