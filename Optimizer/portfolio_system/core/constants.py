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
# ── P3-b3 常量收口：core 内残余裸容差（值一律不变，只做语义命名）────────────────
TARGET_REACH_EPS = 1e-9     # 「目标/需求是否达成」的比较容差（回退判定、二分可行性、下限自检）
MU_REACH_EPS = 1e-9         # μ 标量比较容差（切点 μ 是否高于当前假设）
ZERO_EPS = 1e-9             # 通用「视为 0」阈值（缺口/权重合计/上限之和的末位噪声）
WEIGHT_ZERO_EPS = 1e-6      # 权重视为 0 的阈值（袖是否参与、阶段二早退、权重输出过滤）
TINY_WEIGHT_EPS = 1e-8      # 展示/输出层「权重太小」阈值（结果 JSON 里不列出来）
CAP_HIT_EPS = 1e-6          # 触顶判定容差（有效权重 ≥ 上限 − ε 才算压到上限）
CONSERVATION_EPS = 1e-6     # 权重大类内部守恒自检容差（Σx 与目标合计之差）
BISECT_TOL = 1e-6           # 袖下限二分：区间宽度收敛阈值
BISECT_EPS = 1e-12          # 袖下限二分：上下界重合判定
SLACK_EPS = 1e-12           # 求和不变量松弛（缺口补足/剩余分配）
PSD_JITTER_EPS = 1e-10      # 协方差矩阵对角抖动（保证正定，纯数值）
ALLOC_REMAINDER_EPS = 1e-8  # 剩余权重分配循环的终止阈值（防死循环）
RESEED_TOL = 1e-5           # 袖重新播种：判定「参数是否已被解出」的容差（默认参数）
