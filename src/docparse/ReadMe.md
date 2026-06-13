## 1. 整体架构

`src/docparse` 的核心目标不是单纯“解析 PDF”，而是把多种原始文档统一处理成可索引、可检索的数据结构。大致流程是：

```
原始文件 raw/
   ↓
dataset.py 扫描、识别 domain/doc_id/engine
   ↓
cli.py parse 调度
   ↓
不同类型走不同解析路径：
   - txt/md        → normalize.py 本地转 full.md
   - html/htm      → jina_client.py 调 Jina Reader
   - pdf/doc/ppt等 → mineru_client.py 调 MinerU
   ↓
artifacts.py 发现解析产物
   ↓
normalize.py 生成 manifest、doc_id_map、corpus_documents
   ↓
chunking.py 生成 corpus_chunks
```

其中 `cli.py` 是总入口；`dataset.py` 负责“找到并识别文档”；`mineru_client.py` 和 `jina_client.py` 负责外部解析服务；`artifacts.py` 负责从解析结果目录里找 `full.md`、`content_list.json` 等产物；`chunking.py` 负责把全文或结构化解析结果切成小块；`normalize.py` 负责把前面的结果汇总成标准输出文件。

下面是对仓库 `main` 分支中 `src/docparse` 目录的完整代码解读。这个目录一共包含 8 个文件：`__init__.py`、`artifacts.py`、`chunking.py`、`cli.py`、`dataset.py`、`jina_client.py`、`mineru_client.py`、`normalize.py`。整体来看，它是一个**文档解析流水线工具包**：扫描原始文档 → 判断解析引擎 → 调用 MinerU 或 Jina 解析 → 发现解析产物 → 归一化成统一 manifest → 切分成检索用 chunk。([GitHub](https://github.com/gynzh/123/tree/main/src/docparse))

## 1. 整体架构

`src/docparse` 的核心目标不是单纯“解析 PDF”，而是把多种原始文档统一处理成可索引、可检索的数据结构。大致流程是：

```text
原始文件 raw/
   ↓
dataset.py 扫描、识别 domain/doc_id/engine
   ↓
cli.py parse 调度
   ↓
不同类型走不同解析路径：
   - txt/md        → normalize.py 本地转 full.md
   - html/htm      → jina_client.py 调 Jina Reader
   - pdf/doc/ppt等 → mineru_client.py 调 MinerU
   ↓
artifacts.py 发现解析产物
   ↓
normalize.py 生成 manifest、doc_id_map、corpus_documents
   ↓
chunking.py 生成 corpus_chunks
```

其中 `cli.py` 是总入口；`dataset.py` 负责“找到并识别文档”；`mineru_client.py` 和 `jina_client.py` 负责外部解析服务；`artifacts.py` 负责从解析结果目录里找 `full.md`、`content_list.json` 等产物；`chunking.py` 负责把全文或结构化解析结果切成小块；`normalize.py` 负责把前面的结果汇总成标准输出文件。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/dataset.py))

------

## 2. `__init__.py`

这个文件很短，主要作用是把 `docparse` 标记成一个 Python 包，并声明包的用途：

```python
"""AFAC2026 Challenge 4 document parsing helpers."""
```

它的 `__all__` 导出了：

```python
["dataset", "mineru_client", "artifacts", "chunking", "normalize"]
```

也就是说，如果使用 `from docparse import *`，默认只暴露这几个模块；`cli.py` 和 `jina_client.py` 没有放进 `__all__`，但仍然可以通过显式导入使用。([GitHub](https://raw.githubusercontent.com/gynzh/123/main/src/docparse/__init__.py))

------

## 3. `dataset.py`：扫描数据集、识别文档类型

`dataset.py` 是整个流水线的“入口数据层”。它负责从原始目录中找出可处理文件，并给每个文件生成统一的元信息。

它定义了三类文件扩展名：

```python
MINERU_FILE_EXTS
JINA_HTML_EXTS
LOCAL_TEXT_EXTS
```

其中 MinerU 支持 `.pdf`、Office 文档、图片等复杂格式；Jina 只处理 `.html` / `.htm`；本地文本处理 `.txt` / `.md` / `.markdown`。最后通过 `ALL_SUPPORTED_EXTS` 合并成完整支持列表。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/dataset.py))

核心数据结构是 `SourceDocument`。它保存：

```python
path        # 原始文件绝对路径
raw_root    # 原始数据根目录
domain      # 领域/子目录
doc_id      # 文档 ID
rel_path    # 相对 raw_root 的路径
engine      # 使用哪个解析引擎
```

它还有一个重要属性 `data_id`，用于生成安全的 API 上传 ID。代码会基于相对路径生成 sha1 短哈希，并通过 `safe_path_component` 处理路径字符，避免 API 或文件系统不兼容。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/dataset.py))

主要函数包括：

| 函数                         | 作用                                       |
| ---------------------------- | ------------------------------------------ |
| `slugify`                    | 把字符串转成安全的短标识                   |
| `exact_doc_id_from_stem`     | 从文件名 stem 中提取精确 doc_id            |
| `safe_path_component`        | 生成安全路径片段                           |
| `infer_domain`               | 根据相对路径推断文档领域                   |
| `infer_doc_id`               | 根据文件名推断 doc_id                      |
| `detect_engine`              | 根据扩展名判断走 MinerU、Jina 还是本地文本 |
| `collect_source_documents`   | 扫描 raw 目录并返回 `SourceDocument` 列表  |
| `group_by_engine`            | 按解析引擎分组                             |
| `load_question_doc_ids`      | 从问题 JSON 中读取题目关联的 doc_id        |
| `write_jsonl` / `read_jsonl` | JSONL 文件读写工具                         |

这个模块的设计重点是：**尽量保留原始 `doc_id`，但另行生成安全的 `data_id` 用于 API 和文件路径**。这对中文文件名、空格、特殊符号路径尤其重要。

------

## 4. `artifacts.py`：发现和读取解析产物

`artifacts.py` 负责从解析结果目录中找出有价值的文件，例如：

```text
full.md
content_list.json
middle.json
model.json
layout.json
*.html
```

核心函数 `discover_artifacts` 会在某个输出目录下寻找 Markdown、content list、middle/model/layout/html 等文件，并返回它们的相对路径。配套函数 `resolve_artifact`、`load_json_if_exists`、`read_markdown` 用于根据记录中的相对路径读取实际内容。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/artifacts.py))

这个文件还定义了文本清洗和结构化内容转文本的逻辑：

| 函数                   | 作用                                             |
| ---------------------- | ------------------------------------------------ |
| `normalize_ws`         | 压缩多余空白，统一文本格式                       |
| `_append_value`        | 安全地把字符串、数字、列表、字典内容加入文本数组 |
| `content_item_to_text` | 把 MinerU 的 content item 转成可索引文本         |

`content_item_to_text` 比较关键。它会尽量从 MinerU 的结构化结果中提取：

```text
text
content
html
latex
caption
footnote
table_body
image_caption
code_body
```

如果是图片、图表、表格，并且只有路径没有文本，它会回退成类似：

```text
[image: xxx.png]
[chart: xxx.png]
[table: xxx.png]
```

这样即使解析产物不完整，也能在 chunk 中保留某种占位信息。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/artifacts.py))

------

## 5. `chunking.py`：把文档切成检索 chunk

`chunking.py` 是索引构建中最重要的模块之一。它把 Markdown 或 MinerU 的结构化 content list 切成较短文本块，用于后续检索、RAG 或问答。

它的 token 估算函数 `estimate_tokens` 是一个启发式算法：中文字符按约 `1.6` 字/Token 估算，其他字符按约 `4` 字/Token 估算。这不是严格 tokenizer，而是轻量估算。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

主要切分逻辑包括：

### `sliding_window_text`

它会把长文本按字符长度切分，默认大约：

```python
max_chars = 1800
overlap = 180
```

切分时不是粗暴截断，而是尽量在中文句号、英文句号、分号、换行等位置断开。如果找不到合适标点，才按窗口长度硬切。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

### `iter_content_items`

MinerU 的 JSON 结构可能很深，比如：

```text
pages
blocks
content
children
items
pdf_info
layout_blocks
preproc_blocks
```

`iter_content_items` 会递归展开这些结构，把真正像“内容项”的 dict 取出来。这使得它能兼容不同版本或不同格式的 MinerU 产物。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

### `chunks_from_content_list`

这是针对 MinerU `content_list.json` 的切块函数。它会：

- 读取每个 content item；
- 提取文本；
- 识别页码；
- 维护标题层级 `heading_stack`；
- 对表格、图片、图表、公式等内容加前缀；
- 缓冲文本，达到长度后 flush 成 chunk；
- 保留 `pages`、`content_types`、`heading_path` 等元信息。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

### `chunks_from_markdown`

这是针对普通 Markdown 的切块函数。它会按标题切分，再对过长段落使用滑动窗口。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

### `build_chunks_for_doc`

这是统一入口。它优先使用结构化的 `content_list_path`，其次尝试 `content_list_v2_path`，如果都没有，则回退到 Markdown。最终每个 chunk 会包含：

```text
chunk_id
domain
doc_id
source_file
artifact_path
pages
heading_path
content_types
char_count
token_estimate
text
```

这就是后续 `corpus_chunks.jsonl` 的基础。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

------

## 6. `jina_client.py`：调用 Jina Reader 解析 HTML

`jina_client.py` 专门处理 HTML 文件。它通过 Jina Reader 把本地 HTML 转成 Markdown 风格文本。

它定义了：

```python
JINA_READER_URL = "https://r.jina.ai/"
```

并通过 `make_retry_session` 创建带重试机制的 HTTP session。它会对 `500`、`502`、`503`、`504` 等服务端错误重试，也有 rate limit 检测逻辑。Jina token 会从环境变量 `JINA_API_KEY` 或 `JINA_TOKEN` 读取。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/jina_client.py))

核心函数是 `jina_read_html`。它会：

1. 读取本地 HTML；
2. 构造一个伪 URL，例如 `https://afac.local/...`；
3. 向 Jina Reader 发送 POST；
4. 请求返回 Markdown / text；
5. 对 rate limit 和服务端错误做重试；
6. 返回解析后的文本。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/jina_client.py))

批处理入口是 `run_jina_html_docs`。它会为每个 HTML 文档创建输出目录，保存：

```text
source.html
full.md
jina_meta.json
```

同时写入 `jina_parse_summary.json`，并且支持缓存：如果已有可用 `full.md`，它会复用已有结果，不重复请求 API。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/jina_client.py))

------

## 7. `mineru_client.py`：调用 MinerU 解析 PDF、Office、图片等复杂文档

`mineru_client.py` 是整个目录里最复杂的文件。它负责调用 MinerU v4 API，完成上传、轮询、下载、解压、缓存复用等完整流程。

它定义的 API 地址包括：

```python
API_BASE = "https://mineru.net/api/v4"
APPLY_UPLOAD_URL
BATCH_RESULT_URL
```

Token 从 `MINERU_API_KEY` 或 `MINERU_TOKEN` 中读取；如果没有 token，会直接报错。它还实现了 rate limit 检测，能识别 HTTP 429、`too many requests`、`50 files/min`、中文“限流”等错误信息。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

### 关键数据结构

`MinerUUploadDocument` 是对待上传文档的封装。它不仅保存原始 `SourceDocument`，还支持“拆分 PDF 后的子文件”。如果一个 PDF 被拆成多个 part，它的 `data_id` 会附带页码范围和 hash，避免不同分片冲突。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

`MinerUBatchOptions` 是批处理参数集合，包含模型版本、语言、公式识别、表格识别、是否 OCR、是否 no-cache、轮询间隔、超时时间、最大页数、是否自动拆分 PDF 等配置。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

### PDF 拆分逻辑

MinerU 对 PDF 页数有限制，因此代码实现了 `split_pdf_for_mineru`。它会用 `pypdf` 读取 PDF，如果页数超过阈值，就拆成多个 part 文件，并记录页码范围。默认最大页数逻辑在 options 中控制，相关函数包括：

```python
get_pdf_page_count
split_pdf_for_mineru
prepare_upload_documents
```

它还处理了加密 PDF、AES 依赖缺失等情况。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

### MinerU 批处理流程

完整流程大致是：

```text
prepare_upload_documents
   ↓
apply_upload_urls
   ↓
upload_files_to_presigned_urls
   ↓
poll_batch_result
   ↓
download_file_with_retries
   ↓
safe_extract_zip
   ↓
写 mineru_parse_summary.json
```

`apply_upload_urls` 会先向 MinerU 申请预签名上传 URL；`upload_files_to_presigned_urls` 再把文件 PUT 到这些 URL；`poll_batch_result` 轮询解析结果；如果完成，就下载 zip；`safe_extract_zip` 负责安全解压。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

`safe_extract_zip` 写得比较谨慎：它会拒绝绝对路径、`..` 路径和试图逃出目标目录的 zip 条目，防止 zip slip 类路径穿越问题。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

### 缓存和失败状态

`run_mineru_batches` 是主入口。它会：

- 复用已完成且有文本产物的记录；
- 对待处理文档分批；
- 控制批量大小和 rate limit；
- 下载并解压结果；
- 给每个记录标记状态，例如：
  - `done`
  - `download_or_extract_failed`
  - `skip_failed`
  - `skip_not_done_or_no_zip`

最终会写出 `mineru_parse_summary.json`。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

------

## 8. `normalize.py`：统一 manifest、doc_id_map 和语料文件

`normalize.py` 负责把 MinerU、Jina、本地文本三类来源的结果统一成最终索引文件。

### 本地文本处理

`handle_local_text_docs` 用于处理 `.txt`、`.md`、`.markdown` 文件。它会把本地文本直接写成标准的 `full.md`，并生成一条和 MinerU/Jina 类似的 manifest record。这样后续流程就不用关心它原来是不是外部 API 解析来的。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/normalize.py))

### 产物增强

`enrich_records_with_artifacts` 会对每条解析记录调用 `discover_artifacts`，把发现到的 `full.md`、`content_list.json` 等加入 `artifacts` 字段，并根据是否有可用文本设置：

```text
index_status = usable
index_status = usable_with_partial_extract
index_status = missing_text_artifact
```

同时生成：

```text
manifest.jsonl
manifest.json
```

([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/normalize.py))

### doc_id 映射

`build_doc_id_map` 会按 `domain::doc_id` 建立映射。如果一个原始 PDF 被拆成多个 part，它会把这些 part 归并到同一个逻辑文档下；如果发现同一个 doc_id 对应多个不同源文件，会记录 collision。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/normalize.py))

### 构建最终语料

`build_corpus_files` 会读取可用 records，分组到文档级别，然后调用 `build_chunks_for_doc` 生成 chunk。最终输出包括：

```text
doc_id_map.json
corpus_documents.jsonl
corpus_chunks.jsonl
parse_stats.json
```

其中 `corpus_documents.jsonl` 是文档级索引，`corpus_chunks.jsonl` 是检索粒度的文本块。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/normalize.py))

------

## 9. `cli.py`：命令行入口和总调度

`cli.py` 把前面所有模块串起来，提供命令行接口。它定义了公共参数：

```text
--dataset-root
--raw-dir
--question-dir
--output-dir
--domain
--no-recursive
```

并提供三个子命令：

```text
inspect
parse
build-index
```

([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/cli.py))

### `inspect`

`cmd_inspect` 用来检查数据集。它会扫描 raw 文档、统计各 engine 和 domain 的数量，并读取 question 目录中的题目 doc_id，检查哪些题目引用的文档没有在 raw 中找到。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/cli.py))

### `parse`

`cmd_parse` 是主流程。它会：

1. 加载 `.env`；
2. 扫描原始文档；
3. 写出 `source_documents.jsonl`；
4. 按 engine 分组；
5. 处理本地文本；
6. 调 MinerU 处理 PDF / Office / 图片；
7. 调 Jina 处理 HTML；
8. enrich artifacts；
9. 构建 `doc_id_map`；
10. 构建 corpus documents 和 chunks。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/cli.py))

### `build-index`

`cmd_build_index` 不重新解析原始文件，而是基于已有的 `manifest.jsonl` 重新构建索引文件。这适合解析结果已经存在，但想重新生成 chunk 或统计文件的情况。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/cli.py))

------

## 10. 最终输出文件总结

运行完整 `parse` 后，通常会得到这些关键产物：

| 文件                        | 含义                              |
| --------------------------- | --------------------------------- |
| `source_documents.jsonl`    | 扫描到的原始文档列表              |
| `manifest.jsonl`            | 每个解析结果的标准记录            |
| `manifest.json`             | manifest 的 JSON 数组版本         |
| `doc_id_map.json`           | `domain::doc_id` 到解析产物的映射 |
| `corpus_documents.jsonl`    | 文档级语料                        |
| `corpus_chunks.jsonl`       | chunk 级语料，供检索使用          |
| `parse_stats.json`          | 解析和切块统计                    |
| `mineru_parse_summary.json` | MinerU 批处理摘要                 |
| `jina_parse_summary.json`   | Jina HTML 解析摘要                |

这些文件的生成主要由 `cli.py` 调度，`normalize.py` 和 `chunking.py` 具体完成。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/cli.py))

------

## 11. 代码设计上的几个重点

第一，代码把“原始文档 ID”和“安全上传 ID”分开。`doc_id` 尽量保留原始文件名含义，而 `data_id` 会进行路径安全处理和 hash，适合传给外部 API。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/dataset.py))

第二，解析服务都支持缓存复用。Jina 会检查已有 `full.md`，MinerU 会检查已有完整 record 和文本产物，避免重复调用外部 API。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/jina_client.py))

第三，MinerU 部分很重视稳定性：有重试、限流等待、PDF 自动拆分、安全 zip 解压、失败状态记录等机制。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/mineru_client.py))

第四，chunking 兼容两种来源：结构化 `content_list.json` 和普通 Markdown。如果有结构化内容，会优先用结构化信息保留页码、标题路径、内容类型；否则退回 Markdown 标题和滑动窗口切分。([GitHub](https://github.com/gynzh/123/blob/main/src/docparse/chunking.py))

总体来说，`src/docparse` 是一个比较完整的“文档解析到 RAG 语料”的工程模块，重点不在算法模型本身，而在**多格式文档接入、外部解析 API 编排、缓存容错、产物标准化和检索 chunk 构建**。