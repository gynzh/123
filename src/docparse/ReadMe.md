# src/docparse 文档解析模块说明

`src/docparse` 是项目的文档解析工具包，只负责把比赛原始文档转换为可追溯的文档级解析产物。模块边界包括：数据集扫描、解析引擎分流、MinerU 解析、Jina HTML 解析、本地文本标准化、解析产物发现、manifest 构建、doc_id 映射和文档级结果汇总。

## 模块流程

```text
raw 原始文档
   ↓
dataset.py 扫描文件并识别 domain / doc_id / engine
   ↓
cli.py 按文件类型调度解析流程
   ↓
mineru_client.py / jina_client.py / normalize.py 生成解析产物
   ↓
artifacts.py 发现 full.md、content_list.json、HTML 等解析文件
   ↓
normalize.py 生成 manifest、doc_id_map、parsed_documents 和 parse_stats
```

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 声明文档解析包导出的模块。 |
| `dataset.py` | 扫描 `raw` 目录，识别文件类型，推断 `domain`、`doc_id` 和解析引擎。 |
| `mineru_client.py` | 调用 MinerU v4 API，完成上传、轮询、下载、解压和长 PDF 自动拆分。 |
| `jina_client.py` | 调用 Jina Reader API，将 HTML / HTM 文件解析为 Markdown 文本。 |
| `artifacts.py` | 在解析结果目录中发现和读取 `full.md`、`content_list.json`、`middle.json`、HTML 等产物。 |
| `normalize.py` | 生成 `manifest.jsonl`、`manifest.json`、`doc_id_map.json`、`parsed_documents.jsonl` 和 `parse_stats.json`。 |
| `cli.py` | 提供 `inspect`、`parse`、`build-manifest` 三个命令行入口。 |

## 命令入口

推荐从仓库根目录执行脚本入口：

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

PowerShell 中不要使用 `\` 作为换行符；本说明中的命令均为单行，可直接复制执行。

## 输出文件

| 文件 | 说明 |
|---|---|
| `source_documents.jsonl` | 原始文件扫描清单。 |
| `manifest.jsonl` | 每个解析片段的状态、来源、解析目录和解析产物路径。 |
| `manifest.json` | `manifest.jsonl` 的 JSON 数组版本。 |
| `doc_id_map.json` | 按 `domain::doc_id` 聚合源文件与解析片段。 |
| `parsed_documents.jsonl` | 按原始 `doc_id` 聚合后的文档级解析结果。 |
| `parse_stats.json` | 文档数量、分片数量、领域分布和输出文件清单。 |

## 设计约束

- `doc_id` 保留原始文件名中的业务标识，便于和题目引用关系对齐。
- PDF 超过 MinerU 单文件页数限制时自动拆分，汇总阶段仍归并到同一个原始 `doc_id`。
- HTML 文件统一通过 Jina Reader 解析，输出目录独立于 MinerU 原始解析目录。
- TXT、Markdown 文件不调用外部解析服务，直接写入标准 `full.md`。
- 汇总文件只描述文档解析阶段的结果，不包含文档解析以外的索引或问答产物。
