# src/docparse 文档解析模块说明

`src/docparse` 是本项目的文档解析工具包，负责把比赛原始文件统一解析为文档级标准化产物。模块只覆盖文档解析阶段，不包含问答生成、向量库构建、训练流程或应用服务代码。

## 处理流程

```text
原始文件 raw/
   ↓
dataset.py 扫描文件，识别 domain、doc_id 和解析引擎
   ↓
cli.py 调度 inspect、parse、build-manifest 命令
   ↓
按文件类型进入不同解析路径：
   - TXT / Markdown  → normalize.py 本地读取并生成 full.md
   - HTML / HTM      → jina_client.py 调用 Jina Reader
   - PDF / Office / 图片 → mineru_client.py 调用 MinerU
   ↓
artifacts.py 发现 full.md、content_list.json、middle.json、HTML 等解析产物
   ↓
normalize.py 生成 manifest、doc_id_map、parsed_documents 和 parse_stats
```

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 声明 `docparse` 为文档解析包，并导出解析阶段使用的模块。 |
| `dataset.py` | 扫描 `raw` 目录，推断领域、文档 ID、文件类型和解析引擎。 |
| `mineru_client.py` | 调用 MinerU 解析 PDF、Office、图片等复杂文档，并下载解析结果。 |
| `jina_client.py` | 调用 Jina Reader 将 HTML / HTM 文件转换为 Markdown。 |
| `artifacts.py` | 在解析目录中发现、读取和整理 Markdown、JSON、HTML 等产物。 |
| `normalize.py` | 生成 `manifest.jsonl`、`manifest.json`、`doc_id_map.json`、`parsed_documents.jsonl` 和 `parse_stats.json`。 |
| `cli.py` | 提供 `inspect`、`parse`、`build-manifest` 三个命令行入口。 |

## 命令入口

扫描数据集：

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

执行解析：

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

重建汇总文件：

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

## 输出文件

| 文件 | 作用 |
|---|---|
| `source_documents.jsonl` | 原始文件扫描清单。 |
| `manifest.jsonl` | 每个解析片段的状态、源文件信息和产物路径。 |
| `manifest.json` | `manifest.jsonl` 的 JSON 数组版本。 |
| `doc_id_map.json` | 按 `domain::doc_id` 聚合源文件与解析片段。 |
| `parsed_documents.jsonl` | 按原始 `doc_id` 聚合后的文档级解析结果。 |
| `parse_stats.json` | 解析覆盖统计。 |

## 设计约束

- `doc_id` 尽量保留原始文件名中的业务标识，避免破坏题目引用关系。
- `data_id` 由相对路径和哈希生成，用于上传、目录名和解析记录去重。
- 长 PDF 按页数拆分上传，汇总阶段仍按原始 `doc_id` 合并。
- HTML 统一使用 Jina Reader，避免同一类文件混用不同解析方式。
- 汇总文件只描述文档解析阶段的结果，不包含文档解析以外的业务流程产物。
