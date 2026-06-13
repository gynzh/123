from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .dataset import collect_source_documents, group_by_engine, load_question_doc_ids, write_jsonl
from .jina_client import JinaBatchOptions, run_jina_html_docs
from .mineru_client import MinerUBatchOptions, get_token, run_mineru_batches
from .normalize import (
    build_doc_id_map,
    build_parse_outputs,
    enrich_records_with_artifacts,
    handle_local_text_docs,
    remove_obsolete_corpus_files,
)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """为 inspect/parse 子命令添加通用数据集参数。"""

    parser.add_argument(
        "--dataset-root",
        default="public_dataset_a/public_dataset_upload",
        help="比赛数据根目录",
    )
    parser.add_argument("--raw-dir", default=None, help="raw 目录；默认等于 <dataset-root>/raw")
    parser.add_argument(
        "--question-dir",
        default=None,
        help="题目目录；默认等于 <dataset-root>/questions/group_a",
    )
    parser.add_argument("--output-dir", default="outputs/parse/mineru", help="文档解析汇总输出目录")
    parser.add_argument("--domain", action="append", dest="domains", help="只解析指定 domain，可重复传入")
    parser.add_argument("--no-recursive", action="store_true", help="不递归扫描 raw 目录")


def cmd_inspect(args: argparse.Namespace) -> None:
    """扫描数据集和题目引用关系，不调用外部解析 API。"""

    dataset_root = Path(args.dataset_root)
    raw_root = Path(args.raw_dir) if args.raw_dir else dataset_root / "raw"
    question_root = Path(args.question_dir) if args.question_dir else dataset_root / "questions" / "group_a"

    docs = collect_source_documents(raw_root, domains=args.domains, recursive=not args.no_recursive)
    grouped = group_by_engine(docs)

    print(f"[DATASET] raw_root={raw_root.resolve()}")
    print(f"[DATASET] collected_docs={len(docs)}")
    for engine, rows in sorted(grouped.items()):
        print(f"  - {engine}: {len(rows)}")

    by_domain: dict[str, int] = {}
    for doc in docs:
        by_domain[doc.domain] = by_domain.get(doc.domain, 0) + 1

    print("[DOMAINS]")
    for domain, count in sorted(by_domain.items()):
        print(f"  - {domain}: {count}")

    if question_root.exists():
        qmeta = load_question_doc_ids(question_root)
        print("[QUESTIONS]")
        for domain, meta in sorted(qmeta.items()):
            parsed_doc_ids = {d.doc_id for d in docs if d.domain == domain}
            missing = sorted(meta["doc_ids"] - parsed_doc_ids)
            print(
                f"  - {domain}: qids={len(meta['qids'])}, "
                f"referenced_docs={len(meta['doc_ids'])}, "
                f"missing_in_raw_scan={len(missing)}"
            )
            if missing[:10]:
                print(f"    missing examples: {missing[:10]}")


def _jina_output_dir_from_args(args: argparse.Namespace) -> Path:
    """确定 Jina HTML 原始解析产物目录。

    默认 final output_dir 为 outputs/parse/mineru；Jina 原始产物放到同级
    outputs/parse/jina/jina_html，避免和 MinerU 原始产物混在一起。
    """

    if getattr(args, "jina_output_dir", None):
        return Path(args.jina_output_dir)
    return Path(args.output_dir).parent / "jina" / "jina_html"


def _write_parse_outputs(records: list[dict], output_dir: Path) -> None:
    """写出文档解析阶段汇总文件"""

    records = enrich_records_with_artifacts(records, output_dir)
    build_doc_id_map(records, output_dir)
    build_parse_outputs(records, output_dir)
    remove_obsolete_corpus_files(output_dir)


def cmd_parse(args: argparse.Namespace) -> None:
    """自动续跑文档解析，并生成解析阶段汇总文件。"""

    if load_dotenv:
        load_dotenv()

    dataset_root = Path(args.dataset_root)
    raw_root = Path(args.raw_dir) if args.raw_dir else dataset_root / "raw"
    output_dir = Path(args.output_dir)
    jina_output_dir = _jina_output_dir_from_args(args)

    docs = collect_source_documents(raw_root, domains=args.domains, recursive=not args.no_recursive)
    if not docs:
        raise RuntimeError(f"没有找到可解析文件：{raw_root}")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "source_documents.jsonl", [d.to_json() for d in docs])

    grouped = group_by_engine(docs)
    print(f"[INFO] 待解析文件：{len(docs)}")
    for engine, rows in sorted(grouped.items()):
        print(f"  - {engine}: {len(rows)}")

    if args.dry_run:
        print(f"[DRY RUN] 文件清单已写入：{output_dir / 'source_documents.jsonl'}")
        return

    all_records: list[dict] = []

    local_docs = grouped.get("local_text", [])
    if local_docs:
        print(f"\n========== LOCAL TEXT: {len(local_docs)} files ==========")
        all_records.extend(handle_local_text_docs(local_docs, output_dir))

    mineru_docs = grouped.get("mineru_vlm", [])
    if mineru_docs:
        options = MinerUBatchOptions(
            model_version=args.model_version,
            language=args.language,
            enable_formula=not args.disable_formula,
            enable_table=not args.disable_table,
            is_ocr=args.ocr,
            extra_formats=args.extra_format,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            batch_size=args.batch_size,
            download_retries=args.download_retries,
            download_chunk_size_mb=args.download_chunk_size_mb,
            page_ranges=args.page_ranges,
            no_cache=args.no_cache,
            auto_split_pdf=not args.no_auto_split_pdf,
            max_pdf_pages_per_upload=args.max_pdf_pages_per_upload,
        )
        token = get_token()
        all_records.extend(
            run_mineru_batches(mineru_docs, output_dir / "mineru_vlm", token=token, options=options)
        )

    html_docs = grouped.get("jina_html", [])
    if html_docs:
        print(f"\n========== JINA HTML: {len(html_docs)} files ==========")
        print(f"[JINA OUTPUT] {jina_output_dir}")
        jina_options = JinaBatchOptions(
            respond_with=args.jina_respond_with,
            max_retries=args.jina_retries,
            rate_limit_sleep=args.jina_rate_limit_sleep,
            request_gap_seconds=args.jina_request_gap,
        )
        all_records.extend(run_jina_html_docs(html_docs, jina_output_dir, options=jina_options))

    _write_parse_outputs(all_records, output_dir)
    print(f"[DONE] 文档解析完成，输出目录：{output_dir.resolve()}")
    print("[DONE] 关键文件：manifest.jsonl / doc_id_map.json / parsed_documents.jsonl / parse_stats.json")


def _load_manifest(output_dir: Path) -> list[dict]:
    manifest_path = output_dir / "manifest.jsonl"
    if not manifest_path.exists():
        raise RuntimeError(f"找不到 manifest.jsonl：{manifest_path}。请先运行 parse。")

    records: list[dict] = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def cmd_build_manifest(args: argparse.Namespace) -> None:
    """基于已存在 manifest 重建解析阶段汇总文件。"""

    output_dir = Path(args.output_dir)
    records = _load_manifest(output_dir)
    _write_parse_outputs(records, output_dir)
    print(f"[DONE] 解析汇总文件重建完成：{output_dir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AFAC2026 Challenge4 文档解析工具")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="扫描数据集与题目引用关系，不调用解析 API")
    add_common_args(inspect)
    inspect.set_defaults(func=cmd_inspect)

    parse = sub.add_parser("parse", help="自动续跑解析 raw 文档，并生成解析汇总文件")
    add_common_args(parse)
    parse.add_argument("--model-version", default="vlm", choices=["pipeline", "vlm"], help="非 HTML 文档使用的 MinerU 模型")
    parse.add_argument("--language", default="ch", help="MinerU 解析语言")
    parse.add_argument("--ocr", action="store_true", help="开启 OCR")
    parse.add_argument("--disable-formula", action="store_true", help="关闭公式识别")
    parse.add_argument("--disable-table", action="store_true", help="关闭表格识别")
    parse.add_argument("--extra-format", action="append", choices=["docx", "html", "latex"], help="MinerU 额外导出格式，可重复传入")
    parse.add_argument("--poll-interval", type=int, default=10, help="MinerU 轮询间隔秒数")
    parse.add_argument("--max-wait", type=int, default=3600, help="单个 MinerU 批次最长等待秒数")
    parse.add_argument("--batch-size", type=int, default=50, help="MinerU 每批申请上传链接数量，不能超过 50")
    parse.add_argument("--download-retries", type=int, default=5, help="MinerU 结果 zip 下载重试次数")
    parse.add_argument("--download-chunk-size-mb", type=int, default=1, help="结果 zip 下载分块大小，单位 MB")
    parse.add_argument("--page-ranges", default=None, help="调试用页码范围，例如 1-3；正式解析建议不传")
    parse.add_argument("--no-auto-split-pdf", action="store_true", help="关闭长 PDF 自动拆分；默认开启")
    parse.add_argument("--max-pdf-pages-per-upload", type=int, default=200, help="每个拆分 PDF 的最大页数，默认 200")
    parse.add_argument("--no-cache", action="store_true", default=None, help="传给 MinerU，绕过缓存")
    parse.add_argument("--jina-output-dir", default=None, help="Jina HTML 原始解析结果目录；默认与 output-dir 同级：jina/jina_html")
    parse.add_argument("--jina-respond-with", default="readerlm-v2", help="Jina Reader x-respond-with，默认 readerlm-v2")
    parse.add_argument("--jina-retries", type=int, default=6, help="Jina 请求失败重试次数")
    parse.add_argument("--jina-rate-limit-sleep", type=int, default=30, help="Jina 429 限流后的基础等待秒数")
    parse.add_argument("--jina-request-gap", type=float, default=0.2, help="连续 Jina 请求之间的等待秒数")
    parse.add_argument("--dry-run", action="store_true", help="只扫描文件，不上传解析")
    parse.set_defaults(func=cmd_parse)

    # 保留 build-index 作为兼容别名，但语义已改为“重建解析清单”，不再生成 chunks。
    build_index = sub.add_parser("build-index", help="兼容别名：基于 manifest 重建解析汇总文件，不生成 chunks")
    build_index.add_argument("--output-dir", default="outputs/parse/mineru")
    build_index.set_defaults(func=cmd_build_manifest)

    build_manifest = sub.add_parser("build-manifest", help="基于 manifest 重建解析汇总文件")
    build_manifest.add_argument("--output-dir", default="outputs/parse/mineru")
    build_manifest.set_defaults(func=cmd_build_manifest)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
