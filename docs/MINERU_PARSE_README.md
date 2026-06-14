# MinerU 文档解析说明

本说明对应 `scripts/parse_documents.py` 中的 MinerU 解析与标题层级增强流程。

## 解析流程

1. `inspect` 扫描比赛原始数据，统计各解析引擎对应的文档数量。
2. `parse` 调用 MinerU/Jina 解析文档，并保存原始解析目录。
3. `build-manifest` 基于解析目录重建文档级汇总文件。
4. `enhance-hierarchy` 基于 MinerU 的 `content_list_v2.json`、`content_list.json` 和 `full.md` 增强标题层级。

## MinerU 解析命令

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

## 标题层级增强命令

全量增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash
```

使用 Qwen/DashScope：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider qwen --model qwen-flash
```

Qwen/DashScope 标题层级增强默认关闭 thinking，以减少等待时间和无关推理输出；token 用量优先读取接口 `usage` 字段。

单文档增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text03
```

指定单个解析目录增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

本地流程测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

## 标题增强输入

增强阶段不再按 part 或标题批次拆分同一 PDF。程序会将同一 `domain/doc_id` 下的所有 MinerU part 合并，构造一个完整 PDF 级别的 LLM 请求。

发送给 LLM 的用户消息只包含两个字段：

```json
{
  "toc": [
    {"o": 1, "p": 8, "x": "目录"},
    {"o": 2, "p": 8, "x": "第一节 风险提示及说明......12"}
  ],
  "titles": [
    {"id": "text03_p1_t000060", "o": 60, "p": 12, "r": 2, "x": "第一节 风险提示及说明"},
    {"id": "text03_p1_t000061", "o": 61, "p": 12, "r": 2, "x": "一、与本期债券相关的投资风险"}
  ]
}
```

字段含义：

```text
toc.o      目录参考项在完整 PDF 标题序列中的顺序
toc.p      目录参考项在完整 PDF 中的近似页序
toc.x      目录参考文本
titles.id  本地映射用标题 ID
titles.o   正文标题候选在完整 PDF 标题序列中的顺序
titles.p   正文标题候选在完整 PDF 中的近似页序
titles.r   MinerU 原始标题层级
titles.x   正文标题文本
```

LLM 只返回正文标题候选，不返回目录参考项。返回采用压缩格式，数组第二项为标题层级，`0` 表示不是正文标题：

```json
{
  "items": [
    ["text03_p1_t000060", 1],
    ["text03_p1_t000061", 2],
    ["text03_p1_t000001", 0]
  ]
}
```

## 输出文件

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

每个被增强的 MinerU 解析目录中新增：

```text
full_titleEnhanced.md
```

`title_hierarchy.jsonl` 保存标题 ID、原始层级、增强层级、是否目录标题、是否目录项、section path、页码、bbox 和原始解析目录。`hierarchy_stats.json` 保存标题数量、目录参考数量、LLM 请求数、token 消耗和费用估算。
