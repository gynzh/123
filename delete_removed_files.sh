#!/usr/bin/env bash
set -euo pipefail

if [ ! -d .git ]; then
  echo "[ERROR] 请在 Git 仓库根目录执行。" >&2
  exit 1
fi

git rm -r -f --ignore-unmatch \
  .idea \
  main.py \
  scripts/build_doc_index.py \
  scripts/inspect_questions.py \
  src/docparse/chunking.py \
  src/docparse/__pycache__ \
  outputs/parse/mineru/corpus_*.jsonl \
  delete_removed_files.sh \
  delete_removed_files_v2.sh \
  fix_after_bad_commit.ps1

find . -type d -name "__pycache__" -prune -exec rm -rf {} +
rm -rf .idea
rm -f outputs/parse/mineru/corpus_*.jsonl
rm -f delete_removed_files.sh delete_removed_files_v2.sh fix_after_bad_commit.ps1

echo "[DONE] 已清理不属于文档解析模块的文件。"
echo "[NEXT] 请执行：git status"
