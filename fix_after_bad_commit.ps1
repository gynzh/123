$ErrorActionPreference = "Stop"

if (-not (Test-Path ".git")) {
    Write-Error "请在 Git 仓库根目录执行本脚本。"
    exit 1
}

git rm -r -f --ignore-unmatch `
  .idea `
  main.py `
  scripts/build_doc_index.py `
  scripts/inspect_questions.py `
  src/docparse/chunking.py `
  src/docparse/__pycache__ `
  outputs/parse/mineru/corpus_*.jsonl `
  delete_removed_files.sh `
  delete_removed_files_v2.sh `
  fix_after_bad_commit.ps1

Get-ChildItem -Path . -Directory -Recurse -Force -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .idea -ErrorAction SilentlyContinue
Remove-Item -Force outputs/parse/mineru/corpus_*.jsonl -ErrorAction SilentlyContinue
Remove-Item -Force delete_removed_files.sh, delete_removed_files_v2.sh, fix_after_bad_commit.ps1 -ErrorAction SilentlyContinue

Write-Host "[DONE] 已清理不属于文档解析模块的文件。"
Write-Host "[NEXT] 请执行：git status"
