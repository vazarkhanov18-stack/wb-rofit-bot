from __future__ import annotations

import sys
from pathlib import Path

from wb_profit import analyze_report, build_messages


if len(sys.argv) != 2:
    raise SystemExit("Использование: python test_local.py /путь/к/отчёту.xlsx")

base = Path(__file__).resolve().parent
result = analyze_report(Path(sys.argv[1]), base / "data" / "costs.xlsx")
for message in build_messages(result):
    print(message)
    print("\n" + "=" * 70 + "\n")
