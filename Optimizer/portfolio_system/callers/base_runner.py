"""Runner 接口 + RunRequest/RunOutcome + 共享装配辅助函数（ADR 0003 §4.3）。

调用器之间不得互相 import：共享装配放在这里的**函数**里，不靠继承调用器。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..models.data_types import (AssetSpec, GoalSpec, PoolSpec, ProblemSpec, SLEEVE_KEYS,
                                 SolveResult)
from ..models import serialization


@dataclass(frozen=True)
class RunRequest:
    """一次运行的输入（全部显式；不读模块级全局）。"""

    assets: Tuple[AssetSpec, ...]
    target_return: float
    rf: float
    domestic_weight: float
    overseas_weight: float
    corr_pairs: Mapping[Tuple[str, str], float] = field(default_factory=dict)
    total_assets: float = 0.0
    extract: Mapping[str, Any] = field(default_factory=dict)
    active_cap: float = 0.10
    max_sleeve_iter: int = 10
    damping: float = 0.5
    stage1_starts: int = 160
    stage2_starts: int = 80
    targets: Tuple[float, ...] = ()
    #: 内部池清单（params 的 US/JP/GC_INTERNAL 的 key 全集）→ 进 ProblemSpec.extract，
    #: 供 core 的 active 上限表覆盖内部标的（core 不得 import params）。
    internal_pool_keys: Tuple[str, ...] = ()
    #: P3-b2：core 两层引擎唯一的"外部数据表"入口（core.sleeve_params.SleeveUniverse）。
    #: 装配职责在本层（core 不 import params）；缺省 None → 不写进 extract，
    #: core 侧 solve_sleeves 会显式报错而不是静默跑空。
    sleeve_universe: Any = None
    #: P4 收尾（ADR 0003 §5）编排模式：给出 deps 时由 PresetRunner 负责
    #: 「取数 → 装配 → 求解 → 结果汇总」，main() 只做 argparse / 写文件。
    #: 依赖一律由装配层注入（callers 不得 import params / asset_classes / 脚本）。
    deps: Any = None
    #: 编排模式的运行时输入（仅在 deps 非空时使用）。
    test_tickers: Optional[str] = None
    test_class: str = "active"
    work_dir: Optional[str] = None
    hsbc_timeout: float = 60.0
    request_timeout: float = 12.0
    check_timeout: float = 300.0
    #: log(step, message, level="info")；编排模式的步骤日志回调。
    log: Any = None
    #: 调用方的 warnings 列表（**同一对象**）：错误路径据此打印已累积的告警。
    warnings_out: Any = None


@dataclass(frozen=True)
class RunOutcome:
    """一次运行的结果（DTO + 既有键名载荷）。

    编排模式（PresetRunner._run_pipeline）下 result 为 None、payload 是结果页 JSON；
    context 带出调用方写作时需要的少量装配产物（如 hsbc_pool → build_summary）。
    """

    result: Optional[SolveResult] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def warnings(self):
        return getattr(self.result, "warnings", ())


def assemble_problem(request: RunRequest, target_return: Optional[float] = None) -> ProblemSpec:
    """共享装配：RunRequest → ProblemSpec（三个调用器都只走这里）。"""
    target = float(request.target_return if target_return is None else target_return)
    extract = dict(request.extract)
    if request.internal_pool_keys:
        extract["internal_pool_keys"] = tuple(request.internal_pool_keys)
    if request.sleeve_universe is not None:
        extract["sleeve_universe"] = request.sleeve_universe
    return ProblemSpec(
        assets=tuple(request.assets),
        goal=GoalSpec(target_return=target, rf=float(request.rf),
                      active_cap=float(request.active_cap),
                      max_sleeve_iter=int(request.max_sleeve_iter),
                      damping=float(request.damping)),
        corr_pairs=dict(request.corr_pairs),
        pools=PoolSpec(float(request.domestic_weight), float(request.overseas_weight)),
        total_assets=float(request.total_assets),
        extract=extract,
        stage1_starts=int(request.stage1_starts),
        stage2_starts=int(request.stage2_starts),
    )


def problem_from_legacy_rows(assets: Sequence[Mapping[str, Any]],
                             corr_pairs: Mapping[Tuple[str, str], float], *,
                             dom_w: float, ov_w: float, target: float, rf: float,
                             total_assets: float = 0.0,
                             extract: Optional[Mapping[str, Any]] = None,
                             active_cap: float = 0.10, max_sleeve_iter: int = 10,
                             damping: float = 0.5, stage1_starts: int = 160,
                             stage2_starts: int = 80,
                             internal_pool_keys: Sequence[str] = (),
                             sleeve_universe: Any = None) -> ProblemSpec:
    """既有 asset 行 → ProblemSpec（等价性门用：旧输入 → 新 DTO，一处映射）。"""
    specs = []
    for row in assets:
        specs.append(AssetSpec(
            key=str(row["key"]),
            mu=float(row["mu"]),
            sigma=float(row["vol"] if "vol" in row else row["sigma"]),
            market=str(row.get("market", "")),
            asset_class=str(row.get("test_class", row.get("asset_class", "broad"))),
            upper=float(row.get("upper", 1.0)),
            lower=float(row.get("lower", 0.0)),
            extra={"pool": str(row.get("pool", "overseas")),
                   "name": str(row.get("name", row["key"])),
                   "test_class": str(row.get("test_class", "broad"))},
        ))
    extract_map = dict(extract or {})
    if internal_pool_keys:
        extract_map["internal_pool_keys"] = tuple(internal_pool_keys)
    if sleeve_universe is not None:
        extract_map["sleeve_universe"] = sleeve_universe
    return ProblemSpec(
        assets=tuple(specs),
        goal=GoalSpec(target_return=float(target), rf=float(rf), active_cap=float(active_cap),
                      max_sleeve_iter=int(max_sleeve_iter), damping=float(damping)),
        corr_pairs=dict(corr_pairs),
        pools=PoolSpec(float(dom_w), float(ov_w)),
        total_assets=float(total_assets),
        extract=extract_map,
        stage1_starts=int(stage1_starts),
        stage2_starts=int(stage2_starts),
    )


class Runner(ABC):
    """调用器接口：run(request) → RunOutcome。"""

    @abstractmethod
    def run(self, request: RunRequest) -> RunOutcome:
        raise NotImplementedError

    def _outcome(self, result: SolveResult) -> RunOutcome:
        return RunOutcome(result=result, payload=serialization.result_payload(result))
