# 文档解析输出说明

本文档说明文档解析阶段的输入、流程和输出字段。项目当前范围仅包含文档解析，不包含文档解析以外的处理模块。

## 输入目录

默认数据集目录为：

```text
public_dataset_a/public_dataset_upload
```

默认原始文档目录为：

```text
public_dataset_a/public_dataset_upload/raw
```

默认题目目录为：

```text
public_dataset_a/public_dataset_upload/questions/group_a
```

## 解析流程

1. `dataset.py` 扫描 raw 目录，收集支持格式的源文件。
2. 根据扩展名将源文件分流到 MinerU、Jina Reader 或本地文本处理。
3. `parse` 命令调用对应解析流程并写入 `manifest.jsonl`。
4. `build-manifest` 命令可基于已有 `manifest.jsonl` 重建文档级汇总文件。
5. 汇总阶段按 `domain::doc_id` 聚合解析片段，生成文档级结果。

## 命令

扫描数据集：

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

执行解析：

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

重建文档级汇总文件：

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

## 输出目录

默认汇总输出目录：

```text
outputs/parse/mineru
```

默认 MinerU 原始解析目录：

```text
outputs/parse/mineru/mineru_vlm
```

默认 Jina HTML 原始解析目录：

```text
outputs/parse/jina/jina_html
```

## 汇总文件

| 文件 | 说明 |
|---|---|
| `source_documents.jsonl` | raw 源文档扫描清单。 |
| `manifest.jsonl` | 每个解析片段的标准记录。 |
| `manifest.json` | `manifest.jsonl` 的 JSON 数组版本。 |
| `doc_id_map.json` | `domain::doc_id` 到源文件和解析片段的映射。 |
| `parsed_documents.jsonl` | 文档级解析结果清单。 |
| `parse_stats.json` | 解析覆盖统计。 |

## `manifest.jsonl` 主要字段

| 字段 | 说明 |
|---|---|
| `domain` | 文档所属领域。 |
| `doc_id` | 原始文档标识。 |
| `source_file` | 原始文件相对路径。 |
| `engine` | 解析引擎：`mineru_vlm`、`jina_html` 或 `local_text`。 |
| `status` | 解析流程状态。 |
| `index_status` | 文本产物可用状态。 |
| `output_dir` | 解析产物目录。 |
| `artifacts` | 已发现的解析产物路径集合。 |
| `upload_part_index` | 上传分片序号。 |
| `upload_total_parts` | 同一原始文档的上传分片总数。 |
| `upload_page_start` / `upload_page_end` | 拆分 PDF 时该分片对应的页码范围。 |

## `parsed_documents.jsonl` 主要字段

| 字段 | 说明 |
|---|---|
| `domain` | 文档所属领域。 |
| `doc_id` | 原始文档标识。 |
| `source_files` | 聚合后的源文件列表。 |
| `engines` | 使用过的解析引擎。 |
| `parse_status` | 文档级解析状态。 |
| `upload_total_parts` | 文档上传分片数量。 |
| `upload_total_pages` | 文档页数；无法获取时为空。 |
| `parts` | 每个解析片段的产物路径和状态。 |
| `markdown_char_count` | 已发现 Markdown 文本的字符数。 |
| `markdown_preview` | 文档级文本预览。 |

## 运行结果判定

- `manifest.jsonl` 存在，说明解析片段记录已经生成。
- `doc_id_map.json` 存在，说明源文档与解析片段已经完成聚合映射。
- `parsed_documents.jsonl` 存在，说明文档级解析结果已经生成。
- `parse_stats.json` 中的 `num_documents` 应与成功聚合的文档数量一致。
