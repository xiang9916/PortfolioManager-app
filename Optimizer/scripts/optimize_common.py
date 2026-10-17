#!/usr/bin/env python3
"""薄壳再导出（P2，2026-10-17）：纯数学实现的唯一来源已搬进 portfolio_system/core/。

既有 from optimize_common import X 一律不破：公开函数与私有名
（_pool_constraints / _respects_bounds）+ upper_bounds_array / feasible_start 全部再导出。
本模块不得再持有实现（避免两处各写一份）。
"""

from __future__ import annotations

import os as _os
import sys

# P4（2026-10-17）：包已上提到 Optimizer/portfolio_system（scripts 的**兄弟目录**）——
# 这里把包根（= scripts 的父目录）加进 sys.path。bundle 内目录同构，路径同样成立。
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from portfolio_system.core.constraints import (  # noqa: F401
    _pool_constraints, _respects_bounds, feasible_start, stage1_bounds, upper_bounds_array)
from portfolio_system.core.linalg import (  # noqa: F401
    build_corr_matrix, higham_psd, portfolio_stats, project_psd)
from portfolio_system.core.solver import (  # noqa: F401
    max_feasible_return, solve_max_sharpe, solve_min_var)
