#!/usr/bin/env python
"""文档解析命令行入口脚本。"""

from __future__ import annotations

from pathlib import Path
import sys

# 允许直接在仓库根目录执行：python scripts/parse_documents.py ...
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docparse.cli import main


if __name__ == "__main__":
    main()
