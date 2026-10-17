"""敏感性分析 CLI 薄壳（骨架）。契约与既有 sensitivity_analysis.py 一致；P3 切换。"""

from __future__ import annotations

import argparse
from typing import Sequence

FROZEN_FLAGS = ("--targets", "--work-dir", "--json", "--steps")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="敏感性分析（薄壳骨架）")
    p.add_argument("extract")
    p.add_argument("--targets", default="")
    p.add_argument("--steps", type=int, default=0)
    p.add_argument("--work-dir", default="")
    p.add_argument("--json", dest="out_path", default="")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    raise SystemExit("P0 骨架：请继续使用既有脚本入口 Optimizer/scripts/sensitivity_analysis.py")
