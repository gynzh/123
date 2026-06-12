# 文档解析模块说明

本文档说明当前项目的文档解析流程、解析引擎分工、输出文件含义和维护原则。虽然文件名中保留了 MinerU，但当前流程已经包含 MinerU、Jina 和本地文本三类解析方式。

## 1. 目标

文档解析阶段的目标不是简单地把 PDF/HTML 转成文本，而是为后续金融长文本问答系统构建稳定、可追溯、可自动续跑的语料底座。

最终需要得到：

- 文档级语料：`corpus_documents.jsonl`
- chunk 级语料：`corpus_chunks.jsonl`
- 文档 ID 映射：`doc_id_map.json`
- 解析过程清单：`manifest.jsonl`
- 解析统计：`parse_stats.json`

## 2. 输入数据

默认数据根目录：

```text
public_dataset_a/public_dataset_upload
```

默认 raw 目录：

```text
public_dataset_a/public_dataset_upload/raw
```

默认问题目录：

```text
public_dataset_a/public_dataset_upload/questions/group_a
```

`inspect` 命令会检查题目中引用的 `doc_ids` 是否能在 raw 文档中找到对应文件。

## 3. 解析引擎分流

| engine | 文件类型 | 处理方式 |
|---|---|---|
| `mineru_vlm` | PDF、Office、图片 | 调用 MinerU API |
| `jina_html` | HTML/HTM | 调用 Jina Reader API |
| `local_text` | TXT/Markdown | 本地读取 |

### 3.1 MinerU VLM

MinerU 用于处理版面复杂的 PDF、Office 和图片文档。长 PDF 会在本地拆分为不超过 200 页的上传片段，解析完成后仍按原始 `domain/doc_id` 聚合。

有效产物位于：

```text
outputs/parse/mineru/mineru_vlm/
```

其中 `mineru_parse_summary.json` 是断点续跑和索引重建的重要依据。

### 3.2 Jina HTML

HTML 文件统一使用 Jina Reader API 解析。当前实现采用 raw HTML POST 方式：读取本地 HTML 内容，放入请求体的 `html` 字段，并提供稳定伪 URL 作为 reference URL。

Jina 原始产物位于：

```text
outputs/parse/jina/jina_html/
```

每个 HTML 文档通常包含：

```text
source.html      # 原始 HTML 快照
full.md          # Jina Reader 输出的 Markdown
jina_meta.json   # Jina 请求元信息
```

当前项目不启用本地 HTML fallback。Jina 失败时命令会停止，避免最终语料混入不同解析模式。

### 3.3 本地文本

TXT/Markdown 文件不调用外部服务，直接生成标准 manifest 记录并参与 chunk 构建。

## 4. 幂等解析和自动续跑

`parse` 命令会自动检查已有解析结果：

- 已完成的 MinerU 上传片段会跳过；
- 已完成的 Jina HTML 会跳过；
- 缺失或失败的解析结果会继续处理；
- 每次运行结束都会重新生成统一索引。

因此，正常情况下不需要手动删除结果目录，也不需要手动指定覆盖模式。

## 5. 输出文件说明

| 文件 | 说明 |
|---|---|
| `source_documents.jsonl` | raw 目录扫描得到的源文档列表 |
| `manifest.jsonl` | 每个解析片段或本地文档的标准记录 |
| `manifest.json` | `manifest.jsonl` 的数组形式，便于查看 |
| `doc_id_map.json` | `domain/doc_id` 到源文档和解析产物的映射 |
| `corpus_documents.jsonl` | 按原始 `domain/doc_id` 聚合后的文档级语料 |
| `corpus_chunks.jsonl` | 检索与问答使用的 chunk 级语料 |
| `parse_stats.json` | 文档数、上传片段数、chunk 数等统计 |

## 6. 维护原则

1. `doc_id` 必须使用原始文件名 stem，不能使用 API 安全化名称；
2. 外部 API 的 `data_id` 可以安全化，但不能反向影响比赛 doc_id；
3. PDF 拆分只影响上传片段，不影响文档级聚合；
4. HTML 解析必须统一使用 Jina Reader，不启用本地 fallback；
5. `outputs/parse/mineru` 保存最终统一索引，`outputs/parse/jina` 保存 Jina 原始产物；
6. 开发调试目录、过期 batch 和临时上传分片不应作为后续算法逻辑依赖。

## 7. 常见命令

扫描：

```powershell
python scripts/afac_parse_documents.py inspect `
  --dataset-root public_dataset_a/public_dataset_upload
```

解析：

```powershell
python scripts/afac_parse_documents.py parse `
  --dataset-root public_dataset_a/public_dataset_upload `
  --output-dir outputs/parse/mineru `
  --model-version vlm `
  --extra-format html `
  --poll-interval 10 `
  --max-wait 3600
```

重建索引：

```powershell
python scripts/afac_parse_documents.py build-index `
  --output-dir outputs/parse/mineru
```
