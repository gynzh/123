"""文档解析命令行入口。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .dataset import collect_source_documents, group_by_engine, load_question_doc_ids, write_jsonl
from .hierarchy import HierarchyEnhanceOptions, enhance_hierarchy
from .jina_client import JinaBatchOptions, run_jina_html_docs
from .mineru_client import MinerUBatchOptions, get_token, run_mineru_batches
from .normalize import (
    build_doc_id_map,
    build_parse_outputs,
    enrich_records_with_artifacts,
    handle_local_text_docs,
    remove_out_of_scope_outputs,
)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """为 inspect、parse、build-manifest 子命令添加通用参数。"""
    parser.add_argument(
        "--dataset-root",
        default="public_dataset_a/public_dataset_upload",
        help="比赛数据根目录",
    )
    parser.add_argument("--raw-dir", default=None, help="raw 目录；默认等于 dataset-root/raw")
    parser.add_argument(
        "--question-dir",
        default=None,
        help="题目目录；默认等于 dataset-root/questions/group_a",
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
        print(f" - {engine}: {len(rows)}")

    by_domain: dict[str, int] = {}
    for doc in docs:
        by_domain[doc.domain] = by_domain.get(doc.domain, 0) + 1
    print("[DOMAINS]")
    for domain, count in sorted(by_domain.items()):
        print(f" - {domain}: {count}")

    if question_root.exists():
        qmeta = load_question_doc_ids(question_root)
        print("[QUESTIONS]")
        for domain, meta in sorted(qmeta.items()):
            parsed_doc_ids = {d.doc_id for d in docs if d.domain == domain}
            missing = sorted(meta["doc_ids"] - parsed_doc_ids)
            print(
                f" - {domain}: qids={len(meta['qids'])}, "
                f"referenced_docs={len(meta['doc_ids'])}, "
                f"missing_in_raw_scan={len(missing)}"
            )
            if missing[:10]:
                print(f"   missing examples: {missing[:10]}")


def _jina_output_dir_from_args(args: argparse.Namespace) -> Path:
    """确定 Jina HTML 原始解析产物目录。"""
    if getattr(args, "jina_output_dir", None):
        return Path(args.jina_output_dir)
    return Path(args.output_dir).parent / "jina" / "jina_html"


def _write_parse_outputs(records: list[dict], output_dir: Path) -> None:
    """写出文档解析阶段汇总文件。"""
    records = enrich_records_with_artifacts(records, output_dir)
    build_doc_id_map(records, output_dir)
    build_parse_outputs(records, output_dir)
    remove_out_of_scope_outputs(output_dir)


def cmd_parse(args: argparse.Namespace) -> None:
    """自动续跑文档解析，并生成文档级标准化产物。"""
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
        print(f" - {engine}: {len(rows)}")

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
            download_block_size_mb=args.download_block_size_mb,
            page_ranges=args.page_ranges,
            no_cache=args.no_cache,
            auto_split_pdf=not args.no_auto_split_pdf,
            max_pdf_pages_per_upload=args.max_pdf_pages_per_upload,
        )
        token = get_token()
        all_records.extend(run_mineru_batches(mineru_docs, output_dir / "mineru_vlm", token=token, options=options))

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
    """读取已经存在的 manifest.jsonl。"""
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
    """基于已存在 manifest 重建文档级解析汇总文件。"""
    output_dir = Path(args.output_dir)
    records = _load_manifest(output_dir)
    _write_parse_outputs(records, output_dir)
    print(f"[DONE] 解析汇总文件重建完成：{output_dir.resolve()}")


def _fmt_counts(value: object) -> str:
    """Format a count dictionary for readable command output."""
    if not isinstance(value, dict) or not value:
        return "{}"
    return ", ".join(f"L{k}:{v}" for k, v in sorted(value.items(), key=lambda item: int(item[0])))


def cmd_enhance_hierarchy(args: argparse.Namespace) -> None:
    """基于 MinerU 产物增强标题层级，并生成可视化 Markdown。"""
    if load_dotenv:
        load_dotenv()

    default_model = {"deepseek": "deepseek-v4-flash", "qwen": "qwen-flash", "mock": "mock-title-enhancer"}
    resolved_model = args.model or default_model.get(args.provider, "deepseek-v4-flash")

    options = HierarchyEnhanceOptions(
        output_dir=Path(args.output_dir),
        provider=args.provider,
        model=resolved_model,
        api_key=args.api_key,
        base_url=args.base_url,
        timeout=args.llm_timeout,
        max_retries=args.llm_retries,
        doc_id=args.doc_id,
        domain=args.domain,
        extract_dir=Path(args.extract_dir) if args.extract_dir else None,
        limit_docs=args.limit_docs,
        write_enhanced_md=not args.no_write_enhanced_md,
        resume=args.resume,
        input_price_per_1m=args.input_price_per_1m,
        output_price_per_1m=args.output_price_per_1m,
        enable_toc_filter=not args.disable_toc_filter,
        toc_max_start_page=args.toc_max_start_page,
        toc_max_follow_pages=args.toc_max_follow_pages,
    )

    print("[HIERARCHY] 启动标题层级增强")
    print(f"[HIERARCHY] output_dir={options.output_dir}")
    print(f"[HIERARCHY] provider={options.provider}, model={options.model}, mode=whole_pdf_one_request")
    if options.extract_dir:
        print(f"[HIERARCHY] extract_dir={options.extract_dir}")
    if options.domain or options.doc_id or options.limit_docs:
        print(f"[HIERARCHY] domain={options.domain}, doc_id={options.doc_id}, limit_docs={options.limit_docs}")
    print(
        "[TOC] "
        f"filter={'on' if options.enable_toc_filter else 'off'}, "
        f"max_start_page={options.toc_max_start_page}, "
        f"max_follow_pages={options.toc_max_follow_pages}"
    )

    stats = enhance_hierarchy(options)
    usage = stats.get("usage", {})
    outputs = stats.get("outputs", {})

    print(f"[DONE] 标题层级增强完成，处理 manifest 记录数：{stats.get('record_count', 0)}，PDF 分组数：{stats.get('pdf_group_count', 0)}")
    print(f"[DONE] title_hierarchy.jsonl：{outputs.get('title_hierarchy_jsonl', '')}")
    print(f"[DONE] hierarchy_stats.json：{outputs.get('hierarchy_stats_json', '')}")
    print(
        "[SUMMARY] "
        f"title_records={stats.get('title_record_count', 0)}, "
f"llm_sent={stats.get('llm_sent_candidates_total', 0)}, "
        f"toc_headings={stats.get('toc_heading_candidates_total', 0)}, "
        f"toc_entries={stats.get('toc_entry_candidates_total', 0)}, "
        f"non_titles={stats.get('non_title_candidates_total', 0)}"
    )
    print(f"[LEVEL] raw={_fmt_counts(stats.get('raw_title_level_counts', {}))}")
    print(f"[LEVEL] enhanced={_fmt_counts(stats.get('enhanced_title_level_counts', {}))}")

    documents = stats.get("documents", [])
    if isinstance(documents, list) and documents:
        print("[DOCUMENTS]")
        max_show = min(len(documents), args.print_doc_limit)
        for doc in documents[:max_show]:
            print(
                " - "
                f"group={doc.get('group_id', '')}, "
                f"parts={doc.get('part_count', 0)}, "
                f"titles={doc.get('title_candidates', 0)}, "
                f"llm_sent={doc.get('llm_sent_candidates', 0)}, "
                f"toc_ref={doc.get('toc_reference_candidates', 0)}, "
                f"toc_heading={doc.get('toc_heading_candidates', 0)}, "
                f"toc_entry={doc.get('toc_entry_candidates', 0)}, "
                f"enhanced_md_files={len(doc.get('enhanced_markdown_files', []))}"
            )
        if len(documents) > max_show:
            print(f" - ... 还有 {len(documents) - max_show} 条文档记录，详见 hierarchy_stats.json")

    print(
        "[USAGE] "
        f"requests={usage.get('requests', 0)}, "
        f"prompt_tokens={usage.get('prompt_tokens', 0)}, "
        f"completion_tokens={usage.get('completion_tokens', 0)}, "
        f"total_tokens={usage.get('total_tokens', 0)}, "
        f"prompt_cost_usd={usage.get('prompt_cost_usd', 0)}, "
        f"completion_cost_usd={usage.get('completion_cost_usd', 0)}, "
        f"total_cost_usd={usage.get('total_cost_usd', 0)}"
    )


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(description="AFAC2026 Challenge 4 文档解析工具")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="扫描数据集与题目引用关系，不调用解析 API")
    add_common_args(inspect)
    inspect.set_defaults(func=cmd_inspect)

    parse = sub.add_parser("parse", help="自动续跑解析 raw 文档，并生成文档级标准化产物")
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
    parse.add_argument("--download-block-size-mb", type=int, default=1, help="结果 zip 下载分块大小，单位 MB")
    parse.add_argument("--page-ranges", default=None, help="调试用页码范围，例如 1-3；完整解析时不传")
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

    build_manifest = sub.add_parser("build-manifest", help="基于 manifest 重建文档级解析汇总文件")
    build_manifest.add_argument("--output-dir", default="outputs/parse/mineru")
    build_manifest.set_defaults(func=cmd_build_manifest)

    enhance = sub.add_parser("enhance-hierarchy", help="基于 MinerU 产物增强标题层级并生成 full_titleEnhanced.md")
    enhance.add_argument("--output-dir", default="outputs/parse/mineru", help="文档解析汇总输出目录")
    enhance.add_argument("--provider", default="deepseek", choices=["deepseek", "qwen", "mock"], help="标题层级增强提供方；qwen 使用 DashScope/OpenAI-compatible 接口，mock 仅用于本地测试")
    enhance.add_argument("--model", default=None, help="LLM 模型名称；deepseek 默认 deepseek-v4-flash，qwen 默认 qwen-flash")
    enhance.add_argument("--api-key", default=None, help="LLM API Key；DeepSeek 读取 DEEPSEEK_API_KEY，Qwen 读取 QWEN_API_KEY 或 DASHSCOPE_API_KEY")
    enhance.add_argument("--base-url", default=None, help="OpenAI-compatible base_url；DeepSeek/Qwen 均可用环境变量或该参数覆盖")
    enhance.add_argument("--llm-timeout", type=int, default=120, help="单次 LLM 请求超时秒数")
    enhance.add_argument("--llm-retries", type=int, default=3, help="LLM 请求失败重试次数")
    enhance.add_argument("--doc-id", default=None, help="只增强指定 doc_id，便于单文档测试")
    enhance.add_argument("--domain", default=None, help="只增强指定 domain")
    enhance.add_argument("--extract-dir", default=None, help="只增强指定 MinerU 解析目录，可直接传本地 full.md 所在目录")
    enhance.add_argument("--limit-docs", type=int, default=None, help="最多处理多少条 manifest 记录，便于小样本测试")
    enhance.add_argument("--no-write-enhanced-md", action="store_true", help="不写 full_titleEnhanced.md")
    enhance.add_argument("--resume", action="store_true", help="保留参数位；后续可用于断点续跑")
    enhance.add_argument("--input-price-per-1m", type=float, default=None, help="覆盖输入 token 单价，美元/百万 token")
    enhance.add_argument("--output-price-per-1m", type=float, default=None, help="覆盖输出 token 单价，美元/百万 token")
    enhance.add_argument("--disable-toc-filter", action="store_true", help="关闭目录页目录项过滤")
    enhance.add_argument("--toc-max-start-page", type=int, default=15, help="只在前 N 页寻找目录起点")
    enhance.add_argument("--toc-max-follow-pages", type=int, default=12, help="目录起点后最多连续扩展的目录页数量")
    enhance.add_argument("--print-doc-limit", type=int, default=20, help="命令行最多打印多少条文档明细")
    enhance.set_defaults(func=cmd_enhance_hierarchy)

    return parser


def main() -> None:
    """命令行主函数。"""
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
