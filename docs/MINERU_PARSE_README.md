# AFAC2026 Challenge4：MinerU 文档解析

## 1. 先扫描数据集

```bash
python scripts/afac_parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

这个命令不会调用 MinerU，只会检查 raw 文档、domain 分布、题目引用的 doc_id 是否能和文件名对应。

```powershell
(base) (afac2026-chanllenge4-agent) PS C:\AFAC_2026\afac2026_chanllenge4_agent> python scripts/afac_parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
[DATASET] raw_root=C:\AFAC_2026\afac2026_chanllenge4_agent\public_dataset_a\public_dataset_upload\raw
[DATASET] collected_docs=573
  - local_text: 6
  - mineru_html: 377
  - mineru_vlm: 190
[DOMAINS]
  - financial_contracts: 14
  - financial_reports: 10
  - insurance: 16
  - regulatory: 513
  - research: 20
[QUESTIONS]
  - financial_contracts: qids=20, referenced_docs=13, missing_in_raw_scan=0
  - financial_reports: qids=20, referenced_docs=9, missing_in_raw_scan=0
  - insurance: qids=20, referenced_docs=16, missing_in_raw_scan=0
  - regulatory: qids=20, referenced_docs=15, missing_in_raw_scan=0
  - research: qids=20, referenced_docs=15, missing_in_raw_scan=0
```

## 2. 正式解析

推荐先小范围测试一个 domain：

```bash
python scripts/afac_parse_documents.py parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --domain financial_contracts `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600 `
```

确认输出正常后再全量解析：

```bash
python scripts/afac_parse_documents.py parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600
```

说明：

- PDF / Office / 图片默认走 MinerU `vlm`。
- HTML 文件自动单独分组，使用 `MinerU-HTML`。
- TXT / Markdown 文件不调用 MinerU，直接本地标准化为 `full.md`。
- MinerU 批量接口单批最多 50 个文件，本工具默认按 50 个自动分批。

## 3. 输出文件

解析完成后，`outputs/parse/mineru` 下会生成：

- `source_documents.jsonl`：扫描到的原始文件清单。
- `manifest.jsonl` / `manifest.json`：每个文档的解析状态、MinerU zip、本地解压目录、markdown/content_list/middle/model 等 artifact 路径。
- `doc_id_map.json`：`domain::doc_id` 到源文件和解析结果的映射，后续按题目 `doc_ids` 召回时优先用它。
- `corpus_documents.jsonl`：文档级语料索引。
- `corpus_chunks.jsonl`：chunk 级语料索引，包含 `domain`、`doc_id`、`chunk_id`、`pages`、`heading_path`、`content_types`、`text`。
- `parse_stats.json`：文档数、chunk 数、domain 统计。

## 5. 只重建索引，不重新调用 MinerU

如果 MinerU 结果已经下载好，只想调整 chunk 大小，可以执行：

```bash
python scripts/afac_parse_documents.py build-index `
  --output-dir outputs/parse/mineru `
  --chunk-chars 1800 `
  --chunk-overlap 180
```

## 6. 建议的下一步

文档解析完成后，下一步应该基于 `corpus_chunks.jsonl` 建立检索索引，并让每道题只召回其 `doc_ids` 对应文档的 chunk。这样能减少无关上下文，适合这个赛题强调的长文本 Agent 动态记忆压缩与高效问答。
