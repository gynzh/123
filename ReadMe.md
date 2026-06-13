# AFAC2026 Challenge 4 文档解析

本项目用于完成 AFAC2026 Challenge 4 原始文档解析与文档级标准化产物构建。项目范围仅包含文档解析：扫描数据集原始文件，按文件类型调用 MinerU、Jina Reader 或本地文本读取，生成统一的 manifest、doc_id 映射和文档级解析结果。

## 目录结构

```text
public_dataset_a/public_dataset_upload/
├── raw/                         # 比赛原始文档
└── questions/group_a/           # A 榜问题集

src/docparse/                    # 文档解析模块
scripts/parse_documents.py       # 命令行入口
outputs/parse/mineru/            # 文档解析汇总输出目录
outputs/parse/jina/jina_html/    # Jina HTML 原始解析结果目录
```

## 解析策略

| 文件类型 | 解析方式 | 说明 |
|---|---|---|
| PDF / Office / 图片 | MinerU VLM | 自动拆分超过 200 页的长 PDF，规避 MinerU 单文件页数限制。 |
| HTML / HTM | Jina Reader API | 使用原始 HTML 请求 Jina Reader，默认 `readerlm-v2`。 |
| TXT / Markdown | 本地读取 | 保留原始文本并统一进入 manifest 与文档级解析结果。 |

## 环境变量

PowerShell 当前会话配置方式：

```powershell
$env:MINERU_API_KEY="你的 MinerU API Key"
$env:JINA_API_KEY="你的 Jina API Key"
```

也可以在仓库根目录创建 `.env` 文件：

```text
MINERU_API_KEY=你的 MinerU API Key
JINA_API_KEY=你的 Jina API Key
```

也支持以下变量名：

```text
MINERU_TOKEN=你的 MinerU API Key
JINA_TOKEN=你的 Jina API Key
```

## 安装依赖

```powershell
pip install -r requirements.txt
```

使用 uv 时执行：

```powershell
uv add -r requirements.txt
```

## 常用命令

以下命令全部采用单行写法，可直接在 Windows PowerShell、命令提示符、Git Bash 和 Linux/macOS Shell 中执行。不要在 PowerShell 中使用 `\` 作为换行符。

### 1. 扫描数据集与题目引用关系

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

该命令只做本地扫描，不调用 MinerU 或 Jina。

### 2. 全量解析并自动续跑

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

解析命令是幂等的：已经成功解析并且存在可用文本产物的 MinerU 上传片段和 Jina HTML 文件会自动跳过；缺失或失败的部分会继续处理。

### 3. 基于已有解析结果重建文档级汇总文件

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

该命令基于已有 `manifest.jsonl` 重新生成 `doc_id_map.json`、`parsed_documents.jsonl` 和 `parse_stats.json`，不重新调用外部解析服务。

## 核心输出

```text
outputs/parse/mineru/
├── source_documents.jsonl       # raw 扫描得到的源文档清单
├── manifest.jsonl               # 每个解析片段的标准记录
├── manifest.json                # manifest 的 JSON 数组版本
├── doc_id_map.json              # domain/doc_id 到源文件和解析产物的映射
├── parsed_documents.jsonl       # 按原始 doc_id 聚合后的文档级解析结果
└── parse_stats.json             # 解析统计信息
```

Jina HTML 原始解析产物默认位于：

```text
outputs/parse/jina/jina_html
```

MinerU VLM 原始解析产物默认位于：

```text
outputs/parse/mineru/mineru_vlm
```

## 解析阶段目标

1. 题目引用的 `doc_ids` 能与 raw 文档稳定对齐。
2. 所有支持格式的原始文档都能进入统一解析记录。
3. 长 PDF 拆分后仍按原始 `doc_id` 聚合。
4. HTML 统一使用 Jina Reader 解析。
5. 最终输出保持文档级结构，可作为后续处理的数据输入。

## 进一步说明

解析模块设计和输出字段见：

```text
docs/MINERU_PARSE_README.md
```
