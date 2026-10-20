#!/usr/bin/env python3
"""资产类别，以及「预期收益 μ / 波动率 σ」的构造规则 —— 本模块是唯一事实来源。

2026-10-13 定稿（用户拍板），起因是当时所有标的都用同一套
`0.5×历史年化 + 0.5×先验`（截断 [0%, 15%]），对个股/主动基金过重地注入了人工判断，
对策略指数又不够尊重历史。

三个类别
--------
- **broad 宽基/大类**：宽基被动指数、债券、货币、黄金、商品、大中华 INDEX 桶…
    μ = clamp(0.5×历史年化 + 0.5×先验, 0%, 20%)；σ = 历史年化波动（**无下限、无上限**）
    历史窗口 = **全历史**
- **strategy 策略指数**：smart beta / 因子 / 主题：红利低波、价值、动量、高股息、恒生科技、
    REIT、缓冲期权…
    μ = clamp(0.67×历史年化 + 0.33×先验, −5%, 30%)；σ = 历史年化波动（无下限、无上限）
    历史窗口 = **20 年**（取不到那么长就用可得全历史）
- **active 个股/主动基金**：单只股票、主动管理基金
    μ = clamp(1.00×历史年化, −10%, 40%)，**不使用先验**；σ = 历史年化波动
    历史窗口 = **10 年**（取不到那么长就用可得全历史）

σ **没有人工下限**（2026-10-14 用户定稿，此前 broad/strategy 有一张人工下限表）：
历史波动多低就是多低——人工下限会系统性地把「真实低波资产」抬成「中等波资产」，
并让过短的样本看起来比实际更温和。样本不足的问题改用「长历史代理」解决（见 PROXY_MAP）。

特例
----
- `O_BTC`：μ 截断放宽到 **−20% ~ 60%**（其余同 broad）
- `D_HSBC_MMF`：货币基金完全写死 μ=2%、σ=1%（净值本身不反映货币收益）

兜底矩阵（数据缺失时）
----------------------
| 情况 | broad/strategy | active |
|---|---|---|
| 有写死 + 有行情 | 按上表混合 | 100% 历史 |
| 有写死 + 无行情 | 用写死值（μ/σ 都写死）并报警 | — |
| 无写死 + 有行情 | 用纯历史并报警 | 100% 历史 |
| 无写死 + 无行情 | 丢弃并报警（不再编造 σ） | 丢弃并报警 |

其它约定
--------
- σ > `SIGMA_SANITY_MAX`（150%）视为数据毛刺 → **丢弃该标的并报警**
- `active` 类单个标的组合权重上限 `ACTIVE_CAP`（10%），触顶时合并成一条报警
- `O_US_CORE` 是聚合项（由美股内部标的动态合成），不参与本表的类别判定
"""

# ---------------------------------------------------------------- 类别规则

CLASS_RULES = {
    "broad": {
        "label": "宽基/大类",
        "mu_hist_weight": 0.50,
        "mu_lo": 0.00,
        "mu_hi": 0.20,
        "window_years": None,          # None = 全历史
    },
    "strategy": {
        "label": "策略指数",
        "mu_hist_weight": 2.0 / 3.0,   # 0.67×历史 + 0.33×先验
        "mu_lo": -0.05,
        "mu_hi": 0.30,
        "window_years": 20,
    },
    "active": {
        "label": "个股/主动",
        "mu_hist_weight": 1.00,        # 100% 历史，不用先验
        "mu_lo": -0.10,
        "mu_hi": 0.40,
        "window_years": 10,
    }
}

# 单标的覆盖：比特币的截断线放宽到 −20% ~ 60%
CLASS_OVERRIDES = {
    "O_BTC": {"mu_lo": -0.20, "mu_hi": 0.60}
}

# 完全写死的标的（货币基金：净值不反映货币收益）
SPECIAL_FIXED = {
    "D_HSBC_MMF": {"mu": 0.02, "vol": 0.01}
}

ACTIVE_CAP = 0.10          # active 类单标的的组合权重上限
SIGMA_SANITY_MAX = 1.50    # σ 超过这个值按数据毛刺处理（丢弃 + 报警）

# 类别参数的对外说明（CLI --test-class 与 App 下拉共用同一段文字）
CLASS_PARAM_HELP = (
    "标的类别：broad=宽基/大类（预期收益 50% 历史 + 50% 人工先验）、"
    "strategy=策略指数（67% 历史 + 33% 人工先验）、active=个股/主动基金（100% 历史，不用先验）。"
    "类别同时决定历史窗口（全历史 / 20 年 / 10 年）与 μ 截断线（0~20% / −5~30% / −10~40%）；"
    "σ 一律取历史年化波动，没有人工下限。默认 active。"
    "\n"
    "债券大类（国开债、长久期美债）的预期收益不用写死值，而是动态公式："
    "先验 =（5 年远期收益回归 μ + 当期同久期国债收益率）/2；回归自变量固定为 10 年期国债收益率，"
    "锚定期限与久期匹配（国开债用中国 5 年期、长久期美债用美国 20 年期），取不到就回落到静态兜底值并提示。"
)

# ---------------------------------------------------------------- 类别表
# 每个在用的标的都要在这里，且只能属于一个类别。改类别 = 改这里一行。
ASSET_CLASS = {
    # ---- 大类槽位（BROAD_ASSETS）----
    "D_HSBC_SP500": "broad",          # 标普500 联接（宽基被动）
    "D_HSBC_CDB": "broad",            # 国开债指数（债券）
    "D_HSBC_MMF": "broad",            # 货币（另有写死值，见 SPECIAL_FIXED）
    "D_CN_CREDIT_BOND": "broad",      # 双债增强（债券）
    "D_CN_QDII_STOCK": "broad",       # QDII 全球股票（宽基股票大类）
    "D_CN_QDII_STABLE": "broad",      # QDII 稳健配置（债性）
    "D_CN_QDII_COMMODITY": "broad",   # QDII 商品/原油（商品）
    "D_GREATER_CN": "broad",          # 大中华权益一级大类（沪深+港股混合，取 2800.HK 总回报长历史作为代表）
    "O_GOLD": "broad",                # 黄金
    "O_BTC": "broad",                 # 比特币（截断线特例）
    "O_US_CORE": "broad",             # 美国核心权益（聚合项，运行时用内部持仓动态合成）
    "O_HYLB": "broad",                # 高收益信用债 ETF
    "O_JP_EQ": "strategy",            # 日本权益：执行标的是 1698 高股息策略
    "O_SG_EQ": "broad",               # 新加坡 STI 宽基
    "O_US_TLT": "broad",              # 美国长久期国债
    "O_US_REIT": "strategy",          # 住宅 REIT（被动但行业/策略属性）
    "O_US_ENERGY": "active",          # 能源生产商 XOM —— 名字像大类，实际是个股
    # ---- 美股内部（US_INTERNAL）----
    "SPY": "broad",                   # 标普500 宽基（SPDR S&P 500 ETF Trust）
    "VTV": "strategy",                # 价值因子
    "SPMO": "strategy",               # 动量因子
    "BUFFER": "strategy",             # 四只季度错峰 Max Buffer 的等权篮子（成员见 BUFFER_FAMILY）
    "UNH": "active", "GOOG": "active", "AAPL": "active", "MSFT": "active",
    "NVDA": "active", "AMZN": "active", "META": "active", "TSLA": "active",
    # ---- 日本内部（JP_INTERNAL）----
    "1364": "broad",                  # JPX-Nikkei 400（宽基）
    "2516": "broad",                  # TSE Growth 250（细分市场宽基）
    "1698": "strategy",               # 日本高股息
    "1477": "strategy",               # 日本最小波动
    "1478": "strategy",               # 日本高股息
    "1490": "strategy",               # 日本高息低波
    "2529": "strategy",               # 股东收益 70
    # ---- 大中华内部（GC_INTERNAL）----
    "CSI300": "broad",                # 沪深300
    "INDEX": "broad",                 # 其他指数（用户指定按桶名归 class1）
    "HK_BROAD": "broad",              # 港股宽基
    "DIVLOWVOL": "strategy", "DIVLOWVOL100": "strategy",
    "HK_DIV": "strategy", "HK_TECH": "strategy",
    "STOCK": "active",                # 主动股票/行业
    "MIXED": "active",                # 主动混合
}

# 缓冲期权篮子的成员（ADR 0006）：同一系列、按季度错峰的四只 Max Buffer ETF。
# 顺序按 outcome period 起始月：3/6/9/12 月；篮子等权、每季度初再平衡。
BUFFER_FAMILY = ("MMAX", "MAXJ", "SMAX", "DMAX")

# ---------------------------------------------------------------- 写死的先验
# 只保留 broad / strategy（active 类不用先验）。
# 缺省先验 0.07 用于"表里没写但需要先验"的 broad/strategy 标的。
# ---------------------------------------------------------------------------
# 债券大类先验：**动态公式**（2026-10-13 用户定稿，不是写死数值）
#
#     先验 = ( 回归 μ  +  当期锚定收益率 ) / 2
#            回归 μ = a·(当期 **10 年期**国债收益率) + b   ← 回归的自变量固定是 10 年期
#            当期锚定收益率 = 与资产久期匹配的期限（CDB 用 5 年期、TLT 用 20 年期）
#
#   · 回归 μ = a·r + b，来自"y_t（该资产持有 5 年的年化收益）~ x_t（同币种 10 年期国债收益率）"
#     的历史拟合；t 取距现在 > 5 年的所有时点（这样 y 才观测得到）。系数由
#     `bond_prior.py --write-fits` 拟合并缓存在 `Optimizer/data/bond_prior_fit.json`
#     （系数变化很慢，几个月刷一次即可）；**不预设斜率符号** —— 利率高时买债后续收益
#     更高还是更低，由数据决定。
#   · 锚定收益率 = 与该资产久期匹配的当期国债收益率（CDB 用中国 5 年期，TLT 用美国 20 年期）。
#
# 任一部分取不到（离线、基金太年轻、拟合失败）→ 回落到 PRIOR 里的静态兜底值并报警，
# 这样"公式是主路径、写死值只是保险"，不会再出现"人工数字假装成模型输出"的情况。
# ---------------------------------------------------------------------------
BOND_PRIOR_FORMULA = {
    "D_HSBC_CDB": {
        "market": "cn", "reg_yield": "cn_10y", "anchor": "cn_5y", "fallback": 0.035,
        "desc": "国开债 3-5 年：先验 =（5 年远期收益回归 μ + 中国国债 5 年期收益率）/2"
    },
    "O_US_TLT": {
        "market": "us", "reg_yield": "us_10y", "anchor": "us_20y", "fallback": 0.040,
        "desc": "美国长久期国债：先验 =（5 年远期收益回归 μ + 美国国债 20 年期收益率）/2"
    }
}

# 锚定收益率的数据来源。东财 RPTA_WEB_TREASURYYIELD 有中/美各期限，但**没有美国 20 年档**
# （FRED 在本机不可达），因此 20 年期用 10Y/30Y 线性插值合成，并如实标注。
ANCHOR_SPECS = {
    "cn_5y": {"label": "中国国债 5 年期", "source": "eastmoney", "field": "EMM00166462"},
    "cn_10y": {"label": "中国国债 10 年期", "source": "eastmoney", "field": "EMM00166466"},
    "us_10y": {"label": "美国国债 10 年期", "source": "eastmoney", "field": "EMG00001310"},
    "us_30y": {"label": "美国国债 30 年期", "source": "eastmoney", "field": "EMG00001312"},
    "us_20y": {"label": "美国国债 20 年期（10Y/30Y 线性插值合成）",
               "source": "synth_mid", "of": ("us_10y", "us_30y")}
}

# 运行期动态先验（由 calibrate_params 算出后注入；不在仓库里写死）
PRIOR_OVERRIDE = {}


def set_prior_overrides(mapping):
    """注入动态先验（如债券公式的结果）。传空 dict 可清除。"""
    PRIOR_OVERRIDE.clear()
    PRIOR_OVERRIDE.update({k: float(v) for k, v in (mapping or {}).items() if v is not None})


def prior_for(key):
    """该标的当前生效的先验，优先级：
    ① PRIOR_OVERRIDE（校准阶段解析出的债券公式值等）
    ② RF_PREMIUM：本币无风险利率 + 溢价（0.5% 粒度）
    ③ PRIOR：利率不可用时的兜底表
    """
    if key in PRIOR_OVERRIDE:
        return PRIOR_OVERRIDE[key]
    spec = RF_PREMIUM.get(key)
    if spec is not None:
        rf = _RF.get(spec[1])
        if rf is not None:
            return rf + spec[0]
    return PRIOR.get(key)


def resolve_anchor(name, anchors):
    """按 ANCHOR_SPECS 解析一个锚定收益率；synth_mid 走线性插值。"""
    spec = ANCHOR_SPECS.get(name)
    if spec is None or not anchors:
        return None
    if spec["source"] == "synth_mid":
        vals = [anchors.get(k) for k in spec["of"]]
        if any(v is None for v in vals):
            return None
        return sum(vals) / len(vals)
    return anchors.get(name)


def bond_prior(key, anchors=None, fits=None):
    """按动态公式算债券先验，返回 (prior, detail)。

    detail 里带回归系数/锚定值/公式，便于在原位核对；任一部分缺失时返回 (None, {"reason": ...})。
    """
    spec = BOND_PRIOR_FORMULA.get(key)
    if spec is None:
        return None, {"reason": "not_formula_key"}
    anchors = anchors or {}
    fits = fits or {}
    fit = fits.get(key) or {}
    a, b = fit.get("a"), fit.get("b")
    reg_name = spec.get("reg_yield", "us_10y")
    anchor_name = spec["anchor"]
    reg_yield_val = resolve_anchor(reg_name, anchors)
    anchor_val = resolve_anchor(anchor_name, anchors)
    detail = {"formula": "(reg_mu + anchor) / 2",
              "reg_yield_name": reg_name, "reg_yield_label": ANCHOR_SPECS[reg_name]["label"],
              "reg_yield_value": reg_yield_val,
              "anchor_name": anchor_name, "anchor_label": ANCHOR_SPECS[anchor_name]["label"],
              "anchor_value": anchor_val,
              "a": a, "b": b, "r2": fit.get("r2"), "n_years": fit.get("n_years"),
              "fit_adjusted": fit.get("adjusted"),
              "fit_source": fit.get("source"), "fit_window": fit.get("window"),
              "desc": spec["desc"], "fallback": spec["fallback"]}
    if a is None or b is None:
        detail["reason"] = "missing_fit"
        return None, detail
    if reg_yield_val is None or anchor_val is None:
        detail["reason"] = "missing_anchor"
        return None, detail
    reg_mu = a * reg_yield_val + b         # 回归自变量固定为 10 年期
    detail["reg_mu"] = reg_mu
    detail["prior"] = (reg_mu + anchor_val) / 2.0
    return detail["prior"], detail


DEFAULT_PRIOR = 0.07

# 先验的**正常路径**：本币无风险利率 + 溢价（2026-10-13 定稿）。
#   · 溢价写死（0.5% 粒度，见 RF_PREMIUM）；RF 由校准阶段从东财「中美国债收益率」取来，
#     经 set_rf() 注入，并随解析结果写进校准 JSON 的 priors 字段 → 离线也能复现。
#   · 债券大类另有更细的动态公式（BOND_PRIOR_FORMULA），优先级高于本表。
#   · 🔴 RF **只来自数据源**（东财「中美国债收益率」：美元 10 年期 / 中国 10 年期），
#     程序里**不写死利率**；校准阶段取到后经 set_rf() 注入，并写进校准 JSON 的 prior_rf 字段，
#     运行时由 params.py 再注入 → 离线也能复现当时那组利率。
#   · RF 取不到时回落到下面的 PRIOR 兜底表（同样 0.5% 粒度），并报警。
_RF = {}


def set_rf(values):
    """注入本币无风险利率 {"usd": x, "cny": y}（校准阶段调用）。

    传 None 表示**清除**该币种（clear_rf() 清空全部），供测试与异常路径使用。
    """
    for k, v in (values or {}).items():
        if v is None:
            _RF.pop(k, None)
        else:
            _RF[k] = float(v)


def reset_rf():
    """清空注入的利率（回到"无数据 → 用兜底先验"的状态）。"""
    _RF.clear()


def rf_for(ccy):
    """当前生效的本币无风险利率；未注入（无数据）时返回 None。"""
    return _RF.get(ccy)

RF_PREMIUM = {
    # 先验 = 本币 10 年期国债收益率(RF) + 溢价；溢价写死且**统一 0.5% 粒度**。
    # 币种按标的**实际敞口**判定：境内 A 股/债券基金 = cny，其余（含港币、美元资产）= usd。
    # 人民币敞口
    "CSI300": (+0.060, "cny"), "DIVLOWVOL": (+0.055, "cny"), "DIVLOWVOL100": (+0.060, "cny"),
    "D_CN_CREDIT_BOND": (+0.015, "cny"), "D_GREATER_CN": (+0.060, "cny"), "D_HSBC_CDB": (+0.020, "cny"),
    "D_HSBC_MMF": (+0.005, "cny"), "INDEX": (+0.060, "cny"),
    # 美元敞口
    "1364": (+0.020, "usd"), "1477": (+0.015, "usd"), "1478": (+0.020, "usd"),
    "1490": (+0.015, "usd"), "1698": (+0.020, "usd"), "2516": (+0.030, "usd"),
    "2529": (+0.025, "usd"), "BUFFER": (+0.020, "usd"), "D_CN_QDII_COMMODITY": (-0.010, "usd"),
    "D_CN_QDII_STABLE": (+0.000, "usd"), "D_CN_QDII_STOCK": (+0.035, "usd"), "D_HSBC_SP500": (+0.040, "usd"),
    "HK_BROAD": (+0.020, "usd"), "HK_DIV": (+0.020, "usd"), "HK_TECH": (+0.030, "usd"),
    "O_BTC": (+0.100, "usd"), "O_GOLD": (-0.010, "usd"), "O_HYLB": (+0.025, "usd"),
    "O_JP_EQ": (+0.020, "usd"), "O_SG_EQ": (+0.015, "usd"), "O_US_CORE": (+0.040, "usd"),
    "O_US_REIT": (+0.015, "usd"), "O_US_TLT": (-0.010, "usd"), "SPY": (+0.040, "usd"),
    "SPMO": (+0.045, "usd"), "VTV": (+0.035, "usd"),
}

PRIOR = {
    # 公式/利率不可用时的**兜底**先验（0.5% 粒度）。正常路径见 RF_PREMIUM + set_rf()。
    # 人民币敞口
    "CSI300": 0.075, "DIVLOWVOL": 0.070, "DIVLOWVOL100": 0.075, "D_CN_CREDIT_BOND": 0.030,
    "D_GREATER_CN": 0.075, "D_HSBC_CDB": 0.035, "D_HSBC_MMF": 0.020, "INDEX": 0.075,
    # 美元敞口（含港币；日元/新元暂按美元基准建模）
    "1364": 0.070, "1477": 0.065, "1478": 0.070, "1490": 0.065,
    "1698": 0.070, "2516": 0.080, "2529": 0.075, "BUFFER": 0.070,
    "D_CN_QDII_COMMODITY": 0.040, "D_CN_QDII_STABLE": 0.050, "D_CN_QDII_STOCK": 0.085, "D_HSBC_SP500": 0.090,
    "HK_BROAD": 0.070, "HK_DIV": 0.070, "HK_TECH": 0.080, "O_BTC": 0.150,
    "O_GOLD": 0.040, "O_HYLB": 0.075, "O_JP_EQ": 0.070, "O_SG_EQ": 0.065,
    "O_US_CORE": 0.090, "O_US_REIT": 0.065, "O_US_TLT": 0.040, "SPY": 0.090,
    "SPMO": 0.095, "VTV": 0.085,
}

# 波动率：**没有人工下限表**（2026-10-14 用户定稿）。σ = 窗口内历史年化波动，
# 只在超过 SIGMA_SANITY_MAX 时按数据毛刺丢弃。样本太短的问题靠长历史代理解决
# （见 market_data.PROXY_MAP），不再靠"抬到某个下限"掩盖。

# ---------------------------------------------------------------- 规则函数


def asset_class(key):
    """返回标的类别；未登记返回 None（调用方按"无类别"报警处理）。"""
    return ASSET_CLASS.get(key)


def rule_for_class(cls, key=None):
    """按类别取规则（key 用于套用单标的覆盖，如比特币的截断线）。"""
    base = CLASS_RULES.get(cls)
    if base is None:
        return None
    rule = dict(base)
    rule["class"] = cls
    if key:
        rule.update(CLASS_OVERRIDES.get(key, {}))
    return rule


def class_rule(key):
    """返回该标的适用的规则（含单标的覆盖），未登记返回 None。"""
    return rule_for_class(asset_class(key), key)


def window_years(key):
    """历史窗口年数（None = 全历史）。"""
    rule = class_rule(key)
    return None if rule is None else rule["window_years"]


def window_years_for_class(cls):
    """按类别取历史窗口年数（None = 全历史）。新标的测试用这个。"""
    rule = rule_for_class(cls)
    return None if rule is None else rule["window_years"]


def slice_window(prices, years):
    """取序列末尾 `years` 年；窗口内数据不足则原样返回（= 可得全历史）。"""
    if not prices or years is None:
        return prices
    last = prices[-1][0]
    cutoff = last - _timedelta_days(int(years * 365.25))
    out = [p for p in prices if p[0] >= cutoff]
    return out if len(out) >= 2 else prices


def _timedelta_days(days):
    import datetime
    return datetime.timedelta(days=days)


def build_mu(key, mu_hist):
    """按标的类别构造 μ，返回 (mu, note)。见 mu_for_class 的 note 说明。

    先验取 `prior_for(key)`：债券大类可能是运行期注入的动态公式值，其余走静态表。
    """
    if key in SPECIAL_FIXED:
        return SPECIAL_FIXED[key]["mu"], "fixed"
    return mu_for_class(asset_class(key), mu_hist, prior_for(key), key)


def mu_for_class(cls, mu_hist, prior=None, key=None):
    """按类别构造 μ，返回 (mu, note)。

    note 取值：None（正常混合）/ "prior_fallback"（无行情，用先验）/
    "history_only"（无先验，用纯历史）/ "no_data"（两者皆无 → 丢弃）/
    "unregistered"（没登记类别）。
    """
    rule = rule_for_class(cls, key)
    if rule is None:
        return None, "unregistered"
    w = rule["mu_hist_weight"]
    if mu_hist is None:
        if prior is None:
            return None, "no_data"
        return _clamp(prior, rule["mu_lo"], rule["mu_hi"]), "prior_fallback"
    if w >= 1.0:
        return _clamp(mu_hist, rule["mu_lo"], rule["mu_hi"]), None
    if prior is None:
        return _clamp(mu_hist, rule["mu_lo"], rule["mu_hi"]), "history_only"
    mu = w * mu_hist + (1.0 - w) * prior
    return _clamp(mu, rule["mu_lo"], rule["mu_hi"]), None


def build_vol(key, vol_hist):
    """按标的类别构造 σ，返回 (vol, note)。见 vol_for_class 的 note 说明。"""
    if key in SPECIAL_FIXED:
        return SPECIAL_FIXED[key]["vol"], "fixed"
    return vol_for_class(asset_class(key), vol_hist, key)


def vol_for_class(cls, vol_hist, key=None):
    """按类别构造 σ = 窗口内历史年化波动（**没有人工下限**），返回 (vol, note)。

    note: None（历史值直接用）/ "no_data"（没有行情，构造不出 σ → 调用方丢弃）/
    "sigma_glitch"（历史波动超过 SIGMA_SANITY_MAX，按数据毛刺丢弃）/ "unregistered"。
    """
    rule = rule_for_class(cls, key)
    if rule is None:
        return None, "unregistered"
    if vol_hist is None:
        return None, "no_data"
    if vol_hist > SIGMA_SANITY_MAX:
        return None, "sigma_glitch"
    return vol_hist, None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def describe_class(cls):
    """人类可读的类别说明（CLI/UI 用）。"""
    r = CLASS_RULES.get(cls)
    if r is None:
        return "未登记类别"
    win = "全历史" if r["window_years"] is None else f"近 {r['window_years']} 年"
    w = r["mu_hist_weight"]
    if w >= 1.0:
        blend = "100% 历史年化（不用先验）"
    else:
        blend = f"{w:.0%} 历史年化 + {1 - w:.0%} 人工先验"
    return (f"{r['label']}：μ = {blend}，截断 {r['mu_lo']:+.0%}~{r['mu_hi']:.0%}，"
            f"历史窗口 {win}；σ = 历史年化波动（无人工下限、无上限）")


def audit_table():
    """自检：返回 (error_list, stats)。用于单测与启动时的口径巡查。"""
    errors = []
    for key, cls in ASSET_CLASS.items():
        if cls not in CLASS_RULES:
            errors.append(f"{key}: 未知类别 {cls}")
        if cls == "active":
            if key in PRIOR:
                errors.append(f"{key}: active 类不应有先验")
        else:
            if key not in PRIOR:
                errors.append(f"{key}: {cls} 类缺少先验（可用缺省 {DEFAULT_PRIOR:.0%}，但建议显式登记）")
    for key in PRIOR:
        if key not in ASSET_CLASS:
            errors.append(f"{key}: 先验表里的 key 未登记类别")
    counts = {}
    for cls in ASSET_CLASS.values():
        counts[cls] = counts.get(cls, 0) + 1
    stats = {"assets": len(ASSET_CLASS), "prior": len(PRIOR), "by_class": counts}
    return errors, stats


if __name__ == "__main__":
    errs, stats = audit_table()
    print(f"标的 {stats['assets']} 个：",
          "、".join(f"{CLASS_RULES[c]['label']} {n}" for c, n in sorted(stats['by_class'].items())))
    print(f"先验 {stats['prior']} 条（σ 无人工下限表）")
    for cls in ("broad", "strategy", "active"):
        print(" -", describe_class(cls))
    print("\n自检:", "通过" if not errs else f"{len(errs)} 个问题")
    for e in errs:
        print("  !", e)
