# AFAC2026 Challenge 4 文档解析模块

本仓库当前保留文档解析相关代码和产物：扫描比赛原始文档，调用 MinerU/Jina 解析，生成文档级标准化结果，并支持基于 MinerU 结构化产物的标题层级增强。

## 目录结构

```text
public_dataset_a/public_dataset_upload/      # 比赛原始数据
scripts/parse_documents.py                   # 命令行入口
src/docparse/                                # 文档解析模块
docs/MINERU_PARSE_README.md                  # MinerU 解析说明
outputs/parse/mineru/                        # 解析汇总与 MinerU 结果
outputs/parse/jina/jina_html/                # Jina HTML 解析结果
```

## 环境变量

```powershell
$env:MINERU_API_KEY="你的 MinerU API Key"
$env:JINA_API_KEY="你的 Jina API Key"
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

`DEEPSEEK_API_KEY` 只在执行标题层级增强时需要。

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 常用命令

扫描数据集：

```powershell
python scripts/parse_documents.py inspect --dataset-root public_dataset_a/public_dataset_upload
```

执行文档解析：

```powershell
python scripts/parse_documents.py parse --dataset-root public_dataset_a/public_dataset_upload --output-dir outputs/parse/mineru --model-version vlm --extra-format html --ocr --no-cache --poll-interval 10 --max-wait 3600
```

重建文档级汇总：

```powershell
python scripts/parse_documents.py build-manifest --output-dir outputs/parse/mineru
```

全量增强 MinerU 标题层级：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --hierarchy-batch-size 120
```

单文档增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text01
```

指定单个 MinerU 解析目录增强：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

无 API Key 时做本地流程测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --extract-dir "C:\AFAC_2026\afac2026_chanllenge4_agent\outputs\parse\mineru\mineru_vlm\batch_1_5e462ff7\financial_contracts__text01__part001__86cde1fd08"
```

## 核心输出

文档解析输出：

```text
outputs/parse/mineru/source_documents.jsonl
outputs/parse/mineru/manifest.jsonl
outputs/parse/mineru/manifest.json
outputs/parse/mineru/doc_id_map.json
outputs/parse/mineru/parsed_documents.jsonl
outputs/parse/mineru/parse_stats.json
```

标题层级增强输出：

```text
outputs/parse/mineru/title_hierarchy.jsonl
outputs/parse/mineru/hierarchy_stats.json
```

每个被增强的 MinerU 解析目录中新增：

```text
full_titleEnhanced.md
```

`full.md` 不会被覆盖；`full_titleEnhanced.md` 仅调整 Markdown 标题井号数量，便于人工检查增强效果。

标题层级增强默认会识别并过滤前置目录区间中的目录项。程序会先在前置页面寻找“目录/目次/Contents”等目录起点，再向后连续扫描目录项密度较高的页面，因此可处理多页目录。目录项不会发送给 LLM 作为正文标题，写入 `title_hierarchy.jsonl` 时会标记 `is_toc_entry=true`、`is_title=false`，并在 `full_titleEnhanced.md` 中降级为普通文本，避免目录页污染正文 section_path。若需要调试原始行为，可添加 `--disable-toc-filter`。目录区间范围可用 `--toc-max-start-page` 和 `--toc-max-follow-pages` 调整。

> 目录过滤说明：增强阶段会保留“目录/目次/Contents”这类目录页标题本身，只降级其下方带页码或点线的目录项；正文中再次出现的同名标题仍正常参与层级增强。
