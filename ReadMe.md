# AFAC2026 Challenge4 文档解析模块

本项目当前阶段只负责 **文档解析**：扫描比赛原始文件，调用对应解析引擎生成可追溯的解析产物，并输出文档级清单。chunk 切分、向量索引、RAG、Agent 编排属于后续阶段，不在本模块中实现。

## 解析范围

默认数据根目录：

```text
public_dataset_a/public_dataset_upload
```

源文件目录：

```text
public_dataset_a/public_dataset_upload/raw
```

题目目录：

```text
public_dataset_a/public_dataset_upload/questions/group_a
```

## 解析引擎分流

| 文件类型 | 解析方式 | 说明 |
|---|---|---|
| PDF / Office / 图片 | MinerU VLM | 支持长 PDF 自动拆分，doc_id 保留原始文件名 stem |
| HTML / HTM | Jina Reader API | 通过 raw HTML POST 转 Markdown，不使用本地 fallback |
| TXT / MD | local_text | 直接标准化为 `full.md` |

## 环境变量

```powershell
$env:MINERU_API_KEY="你的 MinerU Key"
$env:JINA_API_KEY="你的 Jina Key"
```

也可以在 `.env` 中配置：

```env
MINERU_API_KEY=你的 MinerU Key
JINA_API_KEY=你的 Jina Key
```

## 常用命令

### 1. 检查数据覆盖

```powershell
python -m docparse.cli inspect `
  --dataset-root public_dataset_a/public_dataset_upload
```

### 2. 执行文档解析

```powershell
python -m docparse.cli parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600
```

金融 PDF 若需要更高识别质量，建议开启 OCR 并绕过缓存：

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

### 3. 基于已有解析结果重建汇总文件

```powershell
python -m docparse.cli build-manifest `
  --output-dir outputs/parse/mineru
```

`build-index` 作为兼容别名仍可使用，但当前阶段不再生成 chunk 文件。

## 输出文件

文档解析汇总目录默认位于：

```text
outputs/parse/mineru
```

核心输出：

| 文件 | 作用 |
|---|---|
| `source_documents.jsonl` | raw 源文档扫描清单 |
| `manifest.jsonl` / `manifest.json` | 每个解析分片的状态与产物路径 |
| `doc_id_map.json` | 按 `domain::doc_id` 聚合解析分片 |
| `parsed_documents.jsonl` | 文档级解析结果清单 |
| `parse_stats.json` | 解析覆盖统计 |

Jina HTML 原始解析产物默认位于：

```text
outputs/parse/jina/jina_html
```

MinerU VLM 原始解析产物默认位于：

```text
outputs/parse/mineru/mineru_vlm
```

## 阶段边界

本模块不再生成：

```text
corpus_chunks.jsonl
corpus_documents.jsonl
```

如需 chunk 切分、检索索引或 RAG 语料构建，应在后续模块中基于 `manifest.jsonl`、`doc_id_map.json` 和各解析目录中的 `full.md` / `content_list*.json` 另行实现。
