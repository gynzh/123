# docparse 文档解析模块

`src/docparse` 负责把比赛原始文档解析为稳定的文档级中间产物。模块当前包含数据集扫描、MinerU/Jina 解析调用、解析产物发现、文档级汇总，以及基于 MinerU 产物的标题层级增强。

## 主要命令

### 扫描数据集

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

### 执行文档解析

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

### 重建文档级汇总

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

### 增强 MinerU 标题层级

对全量 MinerU 解析产物执行标题层级增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --hierarchy-batch-size 120
```

单文档测试可指定 `doc_id`：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text01
```

也可以直接指定某一个 MinerU 解析目录，适合抽查一个文件：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

没有 API Key 时可用 `mock` 做本地流程测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

## 标题层级增强输出

增强命令不会覆盖 MinerU 原始 `full.md`，而是在每个解析目录中写出：

```text
full_titleEnhanced.md
```

该文件仅调整 Markdown 标题井号数量，用于人工直观看标题层级增强效果。

全局结构化输出位于：

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

`title_hierarchy.jsonl` 记录每个标题的原始层级、增强层级、页码、bbox、section_path 和来源解析目录。`hierarchy_stats.json` 记录原始/增强层级分布、LLM 请求数、token 消耗和估算费用。

## 环境变量

使用 DeepSeek 时需要设置：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

可选覆盖：

```powershell
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
```


## 目录页过滤

标题层级增强默认会识别并过滤前置目录区间中的目录项。程序会先在前置页面寻找“目录/目次/Contents”等目录起点，再向后连续扫描目录项密度较高的页面，因此可处理多页目录。目录项不会发送给 LLM 作为正文标题，写入 `title_hierarchy.jsonl` 时会标记 `is_toc_entry=true`、`is_title=false`，并在 `full_titleEnhanced.md` 中降级为普通文本。若需要调试原始行为，可添加 `--disable-toc-filter`。目录区间范围可用 `--toc-max-start-page` 和 `--toc-max-follow-pages` 调整。
