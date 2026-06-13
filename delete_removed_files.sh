#!/usr/bin/env bash
set -euo pipefail

# 必须在仓库根目录执行。本脚本会把非文档解析范围的文件从 Git 索引和工作区中移除。
# 使用 git rm 可以确保删除操作会被 git status 识别，并能随下一次提交推送到 GitHub。

if [ ! -d .git ]; then
  echo "[ERROR] 当前目录不是 Git 仓库根目录，请先 cd 到项目根目录。" >&2
  exit 1
fi

remove_tracked() {
  git rm -r --ignore-unmatch "$@"
}

remove_tracked \
  .idea \
  main.py \
  scripts/build_doc_index.py \
  scripts/inspect_questions.py \
  src/docparse/chunking.py \
  src/docparse/__pycache__ \
  outputs/parse/mineru/corpus_*.jsonl

# 清理未被 Git 跟踪的 Python 缓存目录。
find . -type d -name "__pycache__" -prune -exec rm -rf {} +

# 清理未被 Git 跟踪的旧语料文件。
rm -f outputs/parse/mineru/corpus_*.jsonl

echo "[DONE] 删除命令已执行。请运行：git status"
echo "[NEXT] 确认无误后执行：git add -A && git commit -m 'Keep document parsing module only' && git push"
