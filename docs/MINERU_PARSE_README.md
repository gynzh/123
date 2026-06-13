# 文档解析产物说明

本文说明 AFAC2026 Challenge 4 文档解析阶段的输入、解析流程和输出文件。当前项目只覆盖文档解析模块，不包含问答生成、向量库构建、训练流程或应用服务代码。

## 输入目录

默认数据集目录：

```text
public_dataset_a/public_dataset_upload
```

核心输入：

```text
public_dataset_a/public_dataset_upload/raw
public_dataset_a/public_dataset_upload/questions/group_a
```

## 解析流程

1. `inspect` 扫描 raw 文档，并统计题目引用的 `doc_id` 是否能在 raw 中匹配。
2. `parse` 根据文件类型选择解析路径：
   - PDF / Office / 图片使用 MinerU VLM。
   - HTML / HTM 使用 Jina Reader。
   - TXT / Markdown 使用本地文本读取。
3. MinerU 长 PDF 自动拆分上传，下载后保留每个分片的解析目录。
4. `build-manifest` 可基于已有 `manifest.jsonl` 重建文档级汇总文件。

## 常用命令

扫描数据集：

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

执行解析：

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

重建文档级汇总：

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

## 输出目录

```text
outputs/parse/mineru
```

核心输出文件：

| 文件 | 说明 |
|---|---|
| `source_documents.jsonl` | 从 raw 扫描得到的源文档清单。 |
| `manifest.jsonl` | 每个解析片段的标准记录，包含源文件、解析引擎、状态和产物路径。 |
| `manifest.json` | `manifest.jsonl` 的 JSON 数组版本。 |
| `doc_id_map.json` | 按 `domain::doc_id` 汇总源文件和解析片段。 |
| `parsed_documents.jsonl` | 文档级解析结果清单。 |
| `parse_stats.json` | 解析统计信息。 |

MinerU 原始解析结果默认保存在：

```text
outputs/parse/mineru/mineru_vlm
```

Jina HTML 原始解析结果默认保存在：

```text
outputs/parse/jina/jina_html
```

## `manifest.jsonl` 主要字段

| 字段 | 说明 |
|---|---|
| `domain` | 文档所属领域。 |
| `doc_id` | 从源文件名中推断出的文档 ID。 |
| `rel_path` | 相对 raw 目录的路径。 |
| `engine` | 使用的解析方式。 |
| `data_id` | 用于上传和本地目录的安全标识。 |
| `download_status` | 解析结果下载状态。 |
| `local_extract_dir` | 本地解析结果目录。 |
| `artifacts` | 已发现的 Markdown、JSON、HTML 等产物路径。 |
| `index_status` | 当前解析片段是否具备可用文本产物。 |

## `parsed_documents.jsonl` 主要字段

| 字段 | 说明 |
|---|---|
| `domain` | 文档所属领域。 |
| `doc_id` | 原始文档 ID。 |
| `source_files` | 对应的源文件路径列表。 |
| `engines` | 参与该文档解析的解析方式。 |
| `parts` | 长文档拆分后的解析片段列表。 |
| `char_count` | 合并文本字符数。 |
| `text` | 文档级合并文本。 |

## 完成标准

- `inspect` 中 `missing_in_raw_scan=0`，说明题目引用文档可以在 raw 中匹配。
- `manifest.jsonl` 存在，说明解析片段记录已经生成。
- `doc_id_map.json` 存在，说明源文件和解析片段已经完成聚合。
- `parsed_documents.jsonl` 存在，说明文档级解析结果已经生成。
- `parse_stats.json` 存在，说明解析覆盖统计已经生成。
