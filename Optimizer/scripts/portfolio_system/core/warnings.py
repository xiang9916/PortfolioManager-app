"""内核告警载体：str 子类，额外携带稳定 code（D2 从 optimize_portfolio.`_Warn` 等价搬入）。

为什么是 `str` 子类：既有结果为 "warnings"（字符串数组）与 "warning_items"（[{"code","message"}]）
两条路径共用同一批对象。str 子类在 JSON 序列化、拼接与相等比较上与普通 str 完全一致，
因此**不改变任何既有输出字节**；code 只经加法式的 warning_items 暴露。
"""

from __future__ import annotations


class CodedWarning(str):
    """带 code 的告警字符串。

    __slots__ + str 子类 + 默认参数 code="other"：与 optimize_portfolio 既有 `_Warn`
    构造签名（message, code="other"）逐字一致。
    """

    __slots__ = ("code",)

    def __new__(cls, message, code="other"):
        obj = super().__new__(cls, message)
        obj.code = code
        return obj


def warning_items(warnings):
    """加法式告警明细：[{code, message}]；warnings 本身（字符串数组）逐字节不变。

    （P3-b2 从 optimize_portfolio._warning_items 等价搬入；旧位置再导出。）
    """
    return [{"code": getattr(w, "code", "other"), "message": str(w)} for w in warnings]
