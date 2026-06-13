# 文档解析模块说明

`src/docparse` 是文档解析流水线工具包，负责把多种原始文档统一处理为可追踪、可复用的文档级解析产物。模块不包含文档解析以外的流程。

## 处理流程

```text
raw 原始文件
   ↓
dataset.py 扫描文件，识别 domain、doc_id 和解析引擎
   ↓
cli.py parse 调度不同解析路径
   ↓
TXT/MD        → normalize.py 本地写入 full.md
HTML/HTM      → jina_client.py 调用 Jina Reader
PDF/Office/图像 → mineru_client.py 调用 MinerU
   ↓
artifacts.py 发现 full.md、content_list.json 等产物
   ↓
normalize.py 生成 manifest、doc_id_map、parsed_documents、parse_stats
```

## 模块职责

| 模块 | 职责 |
|---|---|
| `dataset.py` | 扫描 raw 目录，识别支持的文件类型，生成 `SourceDocument` |
| `mineru_client.py` | 调用 MinerU v4，完成上传、轮询、下载、解压和续跑复用 |
| `jina_client.py` | 调用 Jina Reader，把 HTML 解析为 Markdown 文本 |
| `artifacts.py` | 在解析目录中发现和读取 Markdown、JSON、HTML 等产物 |
| `normalize.py` | 统一解析记录，生成文档级标准化产物 |
| `cli.py` | 提供 `inspect`、`parse`、`build-manifest` 命令 |

## 输出字段

### source_documents.jsonl

每行代表一个 raw 源文档，主要字段包括：

| 字段 | 说明 |
|---|---|
| `path` | 源文件绝对路径 |
| `raw_root` | raw 根目录绝对路径 |
| `domain` | raw 下第一级目录 |
| `doc_id` | 原始文件名 stem，保持与题目 `doc_ids` 对齐 |
| `rel_path` | 相对 raw 根目录的路径 |
| `engine` | `mineru_vlm`、`jina_html` 或 `local_text` |
| `data_id` | 外部 API 与本地目录使用的安全 ID |

### manifest.jsonl / manifest.json

每条记录对应一个解析片段。长 PDF 会拆分为多个上传片段，但这些片段保留相同的 `domain` 和 `doc_id`。

| 字段 | 说明 |
|---|---|
| `data_id` | 当前解析片段的稳定 ID |
| `engine` | 解析引擎 |
| `local_extract_dir` | 本地解析产物目录 |
| `download_status` | 下载或本地写入状态 |
| `index_status` | `usable`、`missing_text_artifact` 或 `parse_failed` |
| `artifacts` | 解析产物相对路径集合 |
| `upload_part_no` | PDF 拆分片段序号 |
| `upload_total_parts` | PDF 拆分总片段数 |
| `upload_page_start` / `upload_page_end` | 当前片段页码范围 |

### doc_id_map.json

以 `domain::doc_id` 为键，记录同一逻辑文档对应的源文件、解析片段和状态。该文件用于确认题目引用的 doc_id 是否已经有可用解析结果。

### parsed_documents.jsonl

每行代表一个按原始 `domain/doc_id` 聚合后的文档级解析结果。

| 字段 | 说明 |
|---|---|
| `domain` | 文档所属领域 |
| `doc_id` | 原始文档 ID |
| `source_files` | 聚合到该文档的源文件路径 |
| `engines` | 使用过的解析引擎 |
| `parts` | 解析片段和产物路径列表 |
| `char_count` | 聚合文本字符数 |
| `text` | 文档级标准化文本 |

### parse_stats.json

记录解析统计信息和当前解析阶段生成的标准输出文件清单。

## 设计约束

1. `doc_id` 不做安全化处理，保持与题目文件的引用一致。
2. `data_id` 用于外部 API 和本地目录，避免中文路径、空格和特殊字符引起接口问题。
3. MinerU 结果 zip 使用安全解压，拒绝绝对路径和 `..` 路径。
4. PDF 自动拆分只改变上传粒度，不改变文档级聚合逻辑。
5. HTML 解析统一走 Jina Reader，避免同一类型文件混合多种解析模式。
6. 当前代码只生成文档级解析产物。
