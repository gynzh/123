# 文档解析流程说明

当前文档解析流程由三部分组成：

1. **MinerU VLM**：解析 PDF、Office、图片等复杂版面文档；
2. **Jina Reader API**：解析 HTML/HTM；
3. **local_text**：解析 TXT/MD 等纯文本文件。

本阶段的目标是形成稳定、可追溯的解析产物清单，而不是构建 RAG 检索语料。chunk 切分和索引构建应在后续阶段单独完成。

## 一、文档扫描

```powershell
python -m docparse.cli inspect `
  --dataset-root public_dataset_a/public_dataset_upload
```

该命令会输出：

- raw 目录下可解析文档数量；
- 每个 domain 的文档数量；
- 题目引用的 doc_id 是否能在 raw 扫描结果中找到。

## 二、执行解析

```powershell
python -m docparse.cli parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600
```

如需重新获得更高质量 PDF 解析结果，可开启 OCR 并绕过 MinerU 缓存：

```powershell
python -m docparse.cli parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --ocr `
  --no-cache `
  --poll-interval 10 `
  --max-wait 3600
```

## 三、输出结构

```text
outputs/parse/mineru/
├── source_documents.jsonl
├── manifest.jsonl
├── manifest.json
├── doc_id_map.json
├── parsed_documents.jsonl
├── parse_stats.json
├── local_text/
└── mineru_vlm/

outputs/parse/jina/
└── jina_html/
```

### 关键文件说明

| 文件 | 说明 |
|---|---|
| `source_documents.jsonl` | 原始文件扫描结果 |
| `manifest.jsonl` | 分片级解析记录，包含解析状态和产物路径 |
| `doc_id_map.json` | 按比赛 `domain::doc_id` 聚合分片 |
| `parsed_documents.jsonl` | 文档级解析汇总 |
| `parse_stats.json` | 解析覆盖统计 |

## 四、重建解析清单

如果已经完成 MinerU/Jina 解析，只需重新汇总清单：

```powershell
python -m docparse.cli build-manifest `
  --output-dir outputs/parse/mineru
```

兼容旧命令：

```powershell
python -m docparse.cli build-index `
  --output-dir outputs/parse/mineru
```

该命令只重建解析清单，不生成 chunk 文件。

## 五、已移除内容

文档解析阶段不再包含：

- chunk 切分；
- `chunk_chars` / `chunk_overlap` 参数；
- `corpus_chunks.jsonl`；
- `corpus_documents.jsonl`；
- 向量索引或 RAG 语料构建。

后续检索问答模块应直接读取解析阶段产物，并在单独模块中完成 chunk、embedding 和索引构建。
