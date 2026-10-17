"""结果页 CLI 薄壳（骨架）。

契约冻结：参数名与语义必须与既有 Optimizer/scripts/optimize_portfolio.py 完全一致
（--target-return / --test-tickers / --work-dir / --json / --result-json / --log ...）。
本轮 P0 只落骨架，不做行为切换；真正切换在 P3（ADR §7）。
"""

from __future__ import annotations

import argparse
from typing import Sequence

FROZEN_FLAGS = ("--target-return", "--work-dir", "--json", "--result-json", "--log",
                "--test-tickers", "--test-class")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="结果页优化（薄壳骨架）")
    p.add_argument("extract")
    p.add_argument("--target-return", type=float, default=0.20)
    p.add_argument("--work-dir", default="")
    p.add_argument("--json", dest="out_path", default="")
    p.add_argument("--result-json", dest="result_path", default="")
    p.add_argument("--log", dest="log_path", default="")
    p.add_argument("--test-tickers", default="")
    p.add_argument("--test-class", default="")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    raise SystemExit("P0 骨架：请继续使用既有脚本入口 Optimizer/scripts/optimize_portfolio.py")
