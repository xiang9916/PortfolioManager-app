#!/usr/bin/env python3
"""资产身份的唯一规范化入口（与 Swift 侧 AssetIdentity 同一张规则矩阵）。

纯逻辑：不 import params / market_data，不联网、不落盘。
接口：
  canonical_ticker(raw) -> str     去空白 → 大写 → 6 位境内基金代码去掉 .CN_FUND/.SZ/.SH
  fund_code(raw) -> str | None     规范化后是 6 位境内基金代码 → 该代码
  classify_source(raw) -> (kind, symbol)   6 位境内基金 → ("fund", code)；其余 → ("yahoo", code)

.SS 是 A 股 Yahoo 后缀，故意不在基金后缀表里（与 held_assets 的既有口径一致）。
"""

_FUND_SUFFIXES = (".CN_FUND", ".SZ", ".SH")


def canonical_ticker(raw):
    """规范化后的代码（基金 = 6 位数字；其余 = 大写原文）。"""
    if raw is None:
        return ""
    text = str(raw).strip().upper()
    if not text:
        return ""
    code = fund_code(text)
    return code if code else text


def fund_code(raw):
    """规范化后是 6 位境内基金代码 → 该代码；否则 None。"""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    for suffix in _FUND_SUFFIXES:
        if text.endswith(suffix):
            body = text[: -len(suffix)]
            return body if _is_six_digits(body) else None
    return text if _is_six_digits(text) else None


def classify_source(raw):
    """(kind, symbol)：6 位境内基金 → ("fund", code)；其余 → ("yahoo", canonical)。"""
    code = fund_code(raw)
    if code:
        return "fund", code
    return "yahoo", canonical_ticker(raw)


def _is_six_digits(text):
    return len(text) == 6 and text.isascii() and text.isdigit()
