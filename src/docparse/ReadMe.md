# docparse 模块说明

`src/docparse` 包含文档解析阶段的核心代码。模块负责扫描比赛原始文档、调用解析服务、整理解析产物，并基于 MinerU 结构化结果增强标题层级。

## 文件职责

```text
cli.py             命令行入口实现
dataset.py         数据集扫描与题目引用关系读取
mineru_client.py   MinerU 批量解析、轮询、下载与解压
jina_client.py     Jina HTML 文档解析
artifacts.py       MinerU/Jina 解析产物发现与文本读取
normalize.py       文档级汇总产物生成
hierarchy.py       LLM 标题层级增强与 full_titleEnhanced.md 生成
llm_client.py      OpenAI-compatible LLM 调用与 token/费用统计
```

## 标题层级增强

`hierarchy.py` 从 MinerU 的 `content_list_v2.json`、`content_list.json` 或 `full.md` 中抽取标题候选，并执行完整 PDF 级别的标题层级增强。

核心策略：

```text
1. 按 domain/doc_id 将同一 PDF 的多个 MinerU part 合并。
2. 识别前置目录区间，将“目录/目次/Contents”和目录项作为 toc 参考。
3. 将正文标题候选作为 titles 发送给 LLM。
4. LLM 只返回 id、is_title、level。
5. 本地代码校验返回结果，生成 section_path。
6. 写出 title_hierarchy.jsonl、hierarchy_stats.json 和 full_titleEnhanced.md。
```

## 增强命令

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash
```

单文档测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider deepseek --model deepseek-v4-flash --domain financial_contracts --doc-id text03
```

本地 mock 测试：

```powershell
python scripts/parse_documents.py enhance-hierarchy --output-dir outputs/parse/mineru --provider mock --domain financial_contracts --doc-id text03
```

## LLM 输入输出

发送给 LLM 的用户 JSON 只包含 `toc` 和 `titles`。`toc` 只用于参考目录结构，LLM 不返回其中的项目；`titles` 是必须返回的正文标题候选。

返回格式：

```json
{
  "items": [
    {"id": "text03_p1_t000060", "is_title": true, "level": 1},
    {"id": "text03_p1_t000061", "is_title": true, "level": 2}
  ]
}
```

`level=0` 表示该候选不是正文标题。有效标题层级范围为 1-6。
