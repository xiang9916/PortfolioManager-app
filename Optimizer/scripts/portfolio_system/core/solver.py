"""求解内核壳：**委托** optimize_common（ADR 0003 §7 P0）。"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence, Tuple

from . import errors


def _oc():
    import optimize_common as OC
    return OC


def solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target, **kw):
    return _oc().solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                               ret_target, **kw)


def solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target, **kw):
    return _oc().solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                                  ret_target, **kw)


def max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w):
    return _oc().max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)


def solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf,
                 n_starts: int, warnings_out: List[Any]):
    """阶段一三级回退链（与 optimize_portfolio._solve_stage1 同序、同 seed=23 末级）。

    返回 (解, 生效目标)；三级都失败时返回 (None, target)，由调用方决定整体失败。
    """
    OC = _oc()
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target,
                              n_starts=n_starts, rf=rf)
    if solved is None:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=rf)
    if solved is not None:
        return solved, float(target)
    max_ret = float(max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w))
    from ..models.data_types import Warning
    warnings_out.append(Warning(
        code="target.stage1_fallback",
        message="%s 目标不可行，已按最大可行收益 %.2f%% 交付。" % (
            ("%.0f%%" % (100.0 * float(target))), 100.0 * max_ret),
        severity="critical"))
    eff = max_ret - 1e-6
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, eff,
                              seed=23, n_starts=n_starts, rf=rf)
    return solved, (eff if solved is not None else float(target))
