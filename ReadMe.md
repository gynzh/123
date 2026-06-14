# AFAC2026 Challenge 4 文档解析模块

本仓库保留文档解析相关代码和产物：扫描比赛原始文档，调用 MinerU/Jina 解析，生成文档级标准化结果，并支持基于 MinerU 结构化产物的标题层级增强。

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
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash
```

单文档增强。同一 `domain/doc_id` 下的所有 MinerU part 会按原 PDF 顺序合并，并在一次 LLM 请求中完成标题层级重建：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text03
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

标题层级增强采用完整 PDF 级别的标题序列。程序会将同一 `domain/doc_id` 下的所有 MinerU part 按原 PDF 顺序合并，目录页标题和目录项作为 `toc` 参考提供给 LLM，正文标题作为 `titles` 提供给 LLM，LLM 只返回 `id / is_title / level`。目录项不会作为正文标题返回，但会写入 `title_hierarchy.jsonl` 并在 `full_titleEnhanced.md` 中降级为普通文本；“目录/目次/Contents”这类目录页标题本身保留为 Markdown 标题。
