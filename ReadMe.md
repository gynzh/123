# AFAC2026 Challenge 4 Agent

本仓库用于开发 **AFAC2026 Challenge 4：金融长文本 Agent 的动态记忆压缩与高效问答** 参赛系统。

当前阶段已经完成并固化的是 **文档解析与标准化语料构建**：将比赛提供的 PDF、HTML、TXT 等原始文件解析为统一的 JSONL/Markdown 中间结果，为后续 RAG、动态记忆压缩、证据抽取和问答生成提供稳定数据底座。

赛题入口：[算法大赛-天池大赛-阿里云的赛制](https://tianchi.aliyun.com/competition/entrance/532486/information)

## 目录结构

```text
public_dataset_a/public_dataset_upload/
├── raw/                         # 比赛原始文档
└── questions/group_a/           # A 榜问题集

src/docparse/                    # 文档解析模块
scripts/                         # 命令行入口
outputs/parse/mineru/            # 最终统一语料索引与 MinerU PDF 解析结果
outputs/parse/jina/jina_html/    # Jina HTML 原始解析结果
```

## 解析策略

| 文件类型 | 解析方式 | 说明 |
|---|---|---|
| PDF / Office / 图片 | MinerU VLM | 自动拆分超过 200 页的长 PDF，规避 MinerU 单文件页数限制 |
| HTML / HTM | Jina Reader API | 使用 raw HTML POST 到 Jina Reader，默认 `readerlm-v2`，不使用本地 fallback |
| TXT / Markdown | 本地读取 | 保留原始文本并统一进入 manifest/chunk 流程 |

## 环境变量

在仓库根目录准备 `.env`：

```env
MINERU_API_KEY=你的 MinerU API Key
JINA_API_KEY=你的 Jina API Key
```

也兼容：

```env
MINERU_TOKEN=你的 MinerU API Key
JINA_TOKEN=你的 Jina API Key
```

## 安装依赖

```powershell
uv add -r requirements.txt
```

或者：

```powershell
pip install -r requirements.txt
```

## 常用命令

### 1. 扫描数据集与题目引用关系

```powershell
python scripts/parse_documents.py inspect `
  --dataset-root public_dataset_a/public_dataset_upload
```

该命令只做本地扫描，不调用 MinerU/Jina。

### 2. 全量解析并自动续跑

```powershell
python scripts/parse_documents.py parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600
```

解析命令是幂等的：已经成功解析的 MinerU 上传片段和 Jina HTML 文件会自动跳过，缺失或失败的部分才会继续处理。

### 3. 仅重建索引

```powershell
python scripts/parse_documents.py build-index `
  --output-dir outputs/parse/mineru
```

用于在解析产物已经存在时重新生成 `manifest.jsonl`、`doc_id_map.json`、`corpus_documents.jsonl` 和 `corpus_chunks.jsonl`。

## 核心输出

```text
outputs/parse/mineru/
├── source_documents.jsonl       # raw 扫描得到的源文档清单
├── manifest.jsonl               # 每个解析片段的标准记录
├── manifest.json                # manifest 的 JSON 数组版本
├── doc_id_map.json              # domain/doc_id 到源文件和解析产物的映射
├── corpus_documents.jsonl       # 按原始 doc_id 聚合后的文档级语料
├── corpus_chunks.jsonl          # 后续检索使用的 chunk 级语料
└── parse_stats.json             # 解析统计信息
```

当前解析阶段的目标是保证：

1. 题目引用的 `doc_ids` 能与 raw 文档稳定对齐；
2. 所有原始文档都能进入统一语料层；
3. 长 PDF 拆分后仍能按原始 `doc_id` 聚合；
4. HTML 统一使用 Jina Reader 解析，避免混合不同解析模式；
5. 最终输出可直接供后续 RAG / Agent 使用。

## 进一步说明

更多解析模块设计和输出字段说明见：

```text
docs/MINERU_PARSE_README.md
```
