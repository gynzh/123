# MinerU 文档解析与标题层级增强说明

本说明对应 `scripts/parse_documents.py` 中与 MinerU 相关的解析、汇总和标题层级增强流程。

## 解析流程

MinerU 解析阶段负责将本地 PDF 上传至 MinerU，下载解析结果，并在 `outputs/parse/mineru` 下生成文档级汇总文件。

常用命令：

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

解析完成后可重建汇总：

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

## 主要产物

```text
outputs/parse/mineru/manifest.jsonl
outputs/parse/mineru/manifest.json
outputs/parse/mineru/parsed_documents.jsonl
outputs/parse/mineru/parse_stats.json
outputs/parse/mineru/mineru_vlm/...
```

每个 MinerU 解析目录通常包含：

```text
full.md
full.html
*_content_list.json
*_content_list_v2.json
*_model.json
layout.json
mineru_result.zip
```

## 标题层级增强

默认使用本地规则增强，不调用 LLM，不需要 API Key：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule
```

单文档调试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule --domain financial_contracts --doc-id text01
```

指定单个 MinerU 解析目录：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider rule --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

增强阶段会生成：

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

并在每个解析目录中生成：

```text
full_titleEnhanced.md
```

`full.md` 不会被覆盖；`full_titleEnhanced.md` 用于检查规则或 LLM 增强后的 Markdown 标题层级。

## 规则增强逻辑

规则增强以“目录锚点 + 编号模式 + MinerU 原始块类型”联合判断标题层级：

- 目录页自动识别，目录项用于提供高层级参考，但目录项本身不会进入正文标题树。
- “第X章/第X节/第X部分”通常为一级标题。
- “第X条”通常作为法规类文档的二级标题。
- “一、二、三、”通常为二级标题。
- “（一）（二）”通常为三级标题。
- “1、2、1.1”通常为四级标题。
- “（1）（2）①②”通常为五级标题。
- “a、b、A.、B.”通常为六级标题。

增强阶段会从普通文本中补充识别符合标题特征的行，并过滤封面机构、债券名称、承销商标签、表格字段、页眉页脚和正文列表项等非标题噪声。对于 MinerU 在 `full.md` 中输出的同一行多个内联 Markdown 标题，例如 `## 二、标题 ## （一）子标题 正文`，增强阶段会拆分并按规则重写每个标题，后续正文仍保留为普通文本。

## LLM 对照模式

现有 LLM 增强路径仍保留，可用于与规则结果进行对照。LLM 模式只向模型发送标题候选和目录参考信息，不发送整篇正文。

Qwen 示例：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider qwen --model qwen-flash --domain financial_contracts --doc-id text01
```

DeepSeek 示例：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text01
```

本地 mock 验证：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --limit-docs 1 --no-write-enhanced-md
```

LLM 与 mock 模式会生成同样的 `title_hierarchy.jsonl`、`hierarchy_stats.json` 和可选的 `full_titleEnhanced.md`。规则模式的 `hierarchy_stats.json` 中 `usage.requests`、`usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens` 和费用字段均为 0。
