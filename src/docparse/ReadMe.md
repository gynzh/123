# docparse 文档解析模块

`src/docparse` 负责把比赛原始文档解析为稳定的文档级中间产物。模块包含数据集扫描、MinerU/Jina 解析调用、解析产物发现、文档级汇总，以及基于 MinerU 产物的标题层级增强。

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

### 增强标题层级

默认使用本地规则：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule
```

单文档测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule --domain financial_contracts --doc-id text01
```

指定单个 MinerU 解析目录：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

LLM 对照测试仍可使用：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider qwen --model qwen-flash --domain financial_contracts --doc-id text01
```

## 标题层级增强输出

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

每个被增强的 MinerU 解析目录中新增：

```text
full_titleEnhanced.md
```

`full.md` 不会被覆盖；`full_titleEnhanced.md` 用于检查增强后的 Markdown 标题层级。

## 规则增强说明

规则增强不会调用外部模型。它先识别目录区间并从目录项提取高层级锚点，再结合中文标题编号规则恢复正文标题层级，同时补充识别 MinerU 漏掉的普通文本标题，并过滤正文列表、表格字段、封面机构、债券名称和承销商标签等噪声。

`full_titleEnhanced.md` 的写入逻辑会处理 MinerU 常见的内联标题情况，例如同一行出现多个 `##` 标记并夹带正文。增强后每个结构性标题会独立成行，正文内容保留为普通文本。

## LLM 增强说明

`deepseek`、`qwen` 和 `mock` 提供方仍保留。LLM 模式会沿用现有标题候选、目录参考、JSON 校验和 token/费用统计逻辑；规则模式的 token 和费用统计为 0。
