# MinerU 解析与标题层级增强说明

## 解析产物

MinerU 每个解析目录通常包含：

```text
full.md
full.html
*_content_list.json
*_content_list_v2.json
*_model.json
layout.json
mineru_result.zip
```

`full.md` 适合人工阅读；`content_list_v2/content_list` 更适合后续结构化处理，因为其中包含标题候选、页码、bbox 和 MinerU 原始标题层级。

## 标题层级问题

当前 MinerU VLM 输出的 `title.level/text_level` 对中文金融、合同、年报、保险条款等文档通常只有 1、2 两级。后续分块和检索不应直接把 MinerU 原始层级当作最终章节树。

## 增强策略

新增 `enhance-hierarchy` 命令以 MinerU 的标题候选为输入，调用 OpenAI-compatible LLM 纠正标题层级。LLM 只返回 `title_id -> enhanced_level/is_title`，本地代码负责校验、生成 `section_path`，并重写可视化 Markdown。

## 命令

全量增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash
```

单文档增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text01
```

指定解析目录增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

本地测试流程：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

## 输出

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

每个 MinerU 解析目录中新增：

```text
full_titleEnhanced.md
```

`hierarchy_stats.json` 中的 `usage` 字段记录：

```json
{
  "requests": 1,
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "total_tokens": 0,
  "prompt_cost_usd": 0.0,
  "completion_cost_usd": 0.0,
  "total_cost_usd": 0.0
}
```


## 目录页过滤

标题层级增强默认会识别并过滤前置目录区间中的目录项。程序会先在前置页面寻找“目录/目次/Contents”等目录起点，再向后连续扫描目录项密度较高的页面，因此可处理多页目录。目录项不会发送给 LLM 作为正文标题，写入 `title_hierarchy.jsonl` 时会标记 `is_toc_entry=true`、`is_title=false`，并在 `full_titleEnhanced.md` 中降级为普通文本。若需要调试原始行为，可添加 `--disable-toc-filter`。目录区间范围可用 `--toc-max-start-page` 和 `--toc-max-follow-pages` 调整。
