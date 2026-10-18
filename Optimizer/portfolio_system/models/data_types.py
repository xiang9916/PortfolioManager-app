"""冻结 DTO：内核唯一输入/输出（ADR 0003 §4.1）。无行为、无 I/O。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Tuple

#: 与 optimize_portfolio.SLEEVE_KEYS 一致（P0 镜像；P2 由 core 持有唯一来源）
SLEEVE_KEYS: Tuple[str, ...] = ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")

SEVERITIES: Tuple[str, ...] = ("info", "warning", "critical")
#: 池权重之和自检容差（models 不得 import core —— 依赖方向是 core → models，
#: 因此本常量留在 models 侧；core/constants.py 的常量是内核容差，两者语义不同）。
POOL_SUM_EPS = 1e-6


@dataclass(frozen=True)
class Warning:
    """一条带稳定 code 的告警（code 取自既有 WARNING_CODES）。"""

    code: str
    message: str
    severity: str = "warning"

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError("severity must be one of %s, got %r" % (SEVERITIES, self.severity))


@dataclass(frozen=True)
class AssetSpec:
    """一个候选资产：所有数字都必须是**已经算好的数**（μ/σ/上下界）。"""

    key: str
    mu: float
    sigma: float
    market: str = ""
    asset_class: str = "broad"
    upper: float = 1.0
    lower: float = 0.0
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def pool(self) -> str:
        return str(dict(self.extra).get("pool", "overseas"))

    @property
    def name(self) -> str:
        return str(dict(self.extra).get("name", self.key))


@dataclass(frozen=True)
class PoolSpec:
    """境内/境外池权重（对应既有 dom_w / ov_w）。"""

    domestic_weight: float
    overseas_weight: float

    def __post_init__(self):
        total = float(self.domestic_weight) + float(self.overseas_weight)
        if abs(total - 1.0) > POOL_SUM_EPS:
            raise ValueError("pool weights must sum to 1, got %r" % (total,))


@dataclass(frozen=True)
class GoalSpec:
    """目标与求解超参：一律显式传入，内核不读模块级全局。"""

    target_return: float
    rf: float
    active_cap: float = 0.10
    max_sleeve_iter: int = 10
    damping: float = 0.5


@dataclass(frozen=True)
class ProblemSpec:
    """内核唯一输入：已算好的资产 + 相关性 + 池 + 目标。"""

    assets: Tuple[AssetSpec, ...]
    goal: GoalSpec
    corr_pairs: Mapping[Tuple[str, str], float] = field(default_factory=dict)
    pools: PoolSpec = field(default_factory=lambda: PoolSpec(0.0, 1.0))
    total_assets: float = 0.0
    extract: Mapping[str, Any] = field(default_factory=dict)
    stage1_starts: int = 160
    stage2_starts: int = 80

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(a.key for a in self.assets)

    @property
    def sleeve_keys(self) -> Tuple[str, ...]:
        return tuple(k for k in self.keys if k in SLEEVE_KEYS)

    @property
    def is_single_layer(self) -> bool:
        """无袖 = 单层问题 → 必须走真正的纯求解路径（不得整段转发 solve_portfolio）。"""
        return not self.sleeve_keys


@dataclass(frozen=True)
class SolveResult:
    """内核输出：权重 + 统计 + 袖审计 + 告警 + 交付来源。"""

    weights: Mapping[str, float]
    stats: Mapping[str, float]
    warnings: Tuple[Warning, ...] = ()
    sleeves: Mapping[str, Any] = field(default_factory=dict)
    selected_round: int = 1
    deliverable: bool = True
    final_source: str = "delivered"
    audit: Mapping[str, Any] = field(default_factory=dict)
