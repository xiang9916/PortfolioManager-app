"""内核容差常量的唯一来源（P2）。

旧位置（optimize_portfolio.py）只做再导出；任何地方都不得再写一份字面量。
语义彼此不同，因此各自命名（见 task-39 的教训）。
"""

from __future__ import annotations

DELIVERY_EPS = 1e-4        # 交付保证的唯一语义 ε：交付收益 ≥ 目标 − ε
SLEEVE_ROOM_EPS = 1e-4     # 袖尚未达到可达上限的余量筛选阈值（与交付保证无关）
CHECK_SUM_EPS = 1e-4       # 数值守恒/求和自检容差（权重合计等）
CONSTRAINT_EPS = 1e-4      # 多起点求解器内部的约束校验容差（满仓/池权重/收益下限）
SOLVER_EPS = 1e-7          # 纯数值松弛（SLSQP 末位噪声）
SLEEVE_FLOOR_EPS = DELIVERY_EPS   # 兼容旧名；语义等同 DELIVERY_EPS

BOUNDS_TOL = 1e-6               # 多起点解 bounds 校验容差（_respects_bounds 默认值）
FEASIBILITY_EPS = 1e-9          # feasible_start 的可行性判定容差
SCAN_TARGET_MARGIN_EPS = 1e-4   # 敏感性扫描点定位：放在最大可行收益略下方，避开不可行边界
SCAN_LOOP_EPS = 1e-9            # 敏感性扫描目标清单的闭合容差（浮点累加，避免漏掉上界那一点）
FALLBACK_MARGIN_EPS = 1e-6      # 阶段一末级回退的生效目标 = 最大可行收益 − ε（避开不可行边界）
POOL_CONFLICT_EPS = 1e-9        # active 上限与境内池权重的冲突判定容差（apply_active_caps）
