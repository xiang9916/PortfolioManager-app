"""Shared assumptions for portfolio optimization."""

import datetime
import json

import numpy as np

# 类别规则与人工先验的唯一事实来源（见 asset_classes.py 的 docstring）
import asset_classes as _AC

# 无风险利率（夏普分子用）。🔴 程序里**不写死利率**：正常路径由调用方注入
# （App 侧按境内/境外池比例加权中美 10 年期国债收益率，写进提取文件的 "rf" 字段；
#  optimize_portfolio.py / sensitivity_analysis.py 收到后覆盖这里）。
# 提取文件没带 rf 时按 0 计（= 不假设任何无风险收益），只影响夏普的相对排序。
RF = 0.0
TARGET_RETURN = 0.10

US_INTERNAL = {
    # 标普500 宽基这一档用 SPY（SPDR S&P 500 ETF Trust）：最标准、数据最稳的那只
    "SPY": {"name": "SPDR S&P 500 ETF Trust"},
    "VTV": {"name": "Vanguard Value ETF"},
    "SPMO": {"name": "Invesco S&P 500 Momentum ETF"},
    "UNH": {"name": "UnitedHealth Group"},
    "GOOG": {"name": "Alphabet Inc."},
    "AAPL": {"name": "Apple Inc."},
    "MSFT": {"name": "Microsoft Corp."},
    "NVDA": {"name": "NVIDIA Corp."},
    "AMZN": {"name": "Amazon.com Inc."},
    "META": {"name": "Meta Platforms Inc."},
    "TSLA": {"name": "Tesla Inc."},
    "BUFFER": {"name": "MAXJ/DMAX 缓冲期权"},
}

US_INTERNAL_CORR = {
    ("SPY", "VTV"): 0.85,
    ("SPY", "SPMO"): 0.75,
    ("SPY", "UNH"): 0.75,
    ("SPY", "GOOG"): 0.75,
    ("SPY", "AAPL"): 0.70,
    ("SPY", "MSFT"): 0.70,
    ("SPY", "NVDA"): 0.65,
    ("SPY", "AMZN"): 0.70,
    ("SPY", "META"): 0.70,
    ("SPY", "TSLA"): 0.55,
    ("VTV", "SPMO"): 0.70,
    ("VTV", "UNH"): 0.75,
    ("VTV", "GOOG"): 0.70,
    ("VTV", "AAPL"): 0.65,
    ("VTV", "MSFT"): 0.65,
    ("VTV", "NVDA"): 0.55,
    ("VTV", "AMZN"): 0.60,
    ("VTV", "META"): 0.60,
    ("VTV", "TSLA"): 0.45,
    ("SPMO", "UNH"): 0.75,
    ("SPMO", "GOOG"): 0.75,
    ("SPMO", "AAPL"): 0.70,
    ("SPMO", "MSFT"): 0.70,
    ("SPMO", "NVDA"): 0.65,
    ("SPMO", "AMZN"): 0.70,
    ("SPMO", "META"): 0.70,
    ("SPMO", "TSLA"): 0.55,
    ("UNH", "GOOG"): 0.70,
    ("UNH", "AAPL"): 0.60,
    ("UNH", "MSFT"): 0.60,
    ("UNH", "NVDA"): 0.50,
    ("UNH", "AMZN"): 0.55,
    ("UNH", "META"): 0.55,
    ("UNH", "TSLA"): 0.40,
    ("GOOG", "AAPL"): 0.65,
    ("GOOG", "MSFT"): 0.65,
    ("GOOG", "NVDA"): 0.60,
    ("GOOG", "AMZN"): 0.65,
    ("GOOG", "META"): 0.65,
    ("GOOG", "TSLA"): 0.50,
    ("AAPL", "MSFT"): 0.70,
    ("AAPL", "NVDA"): 0.60,
    ("AAPL", "AMZN"): 0.65,
    ("AAPL", "META"): 0.65,
    ("AAPL", "TSLA"): 0.50,
    ("MSFT", "NVDA"): 0.65,
    ("MSFT", "AMZN"): 0.65,
    ("MSFT", "META"): 0.65,
    ("MSFT", "TSLA"): 0.50,
    ("NVDA", "AMZN"): 0.55,
    ("NVDA", "META"): 0.55,
    ("NVDA", "TSLA"): 0.45,
    ("AMZN", "META"): 0.65,
    ("AMZN", "TSLA"): 0.50,
    ("META", "TSLA"): 0.50,
    ("BUFFER", "SPY"): 0.60,
    ("BUFFER", "VTV"): 0.55,
    ("BUFFER", "SPMO"): 0.60,
    ("BUFFER", "UNH"): 0.50,
    ("BUFFER", "GOOG"): 0.50,
    ("BUFFER", "AAPL"): 0.50,
    ("BUFFER", "MSFT"): 0.50,
    ("BUFFER", "NVDA"): 0.45,
    ("BUFFER", "AMZN"): 0.45,
    ("BUFFER", "META"): 0.45,
    ("BUFFER", "TSLA"): 0.40,
}

# 日本权益大类内部：策略/因子二级候选池
JP_INTERNAL = {
    "1364": {"name": "iShares JPX-Nikkei 400"},
    "1698": {"name": "Listed Index Fund Japan High Dividend"},
    "2516": {"name": "TSE Growth 250"},
    "1477": {"name": "iShares MSCI Japan Min Vol ex-REITs"},
    "1478": {"name": "iShares MSCI Japan High Dividend"},
    "1490": {"name": "MSCI Japan High Div Low Vol"},
    "2529": {"name": "Nomura Shareholder Yield 70"},
}

JP_INTERNAL_CORR = {
    ("1364", "1698"): 0.80,
    ("1364", "2516"): 0.85,
    ("1364", "1477"): 0.80,
    ("1364", "1478"): 0.85,
    ("1364", "1490"): 0.80,
    ("1364", "2529"): 0.85,
    ("1698", "2516"): 0.60,
    ("1698", "1477"): 0.70,
    ("1698", "1478"): 0.90,
    ("1698", "1490"): 0.85,
    ("1698", "2529"): 0.85,
    ("2516", "1477"): 0.55,
    ("2516", "1478"): 0.60,
    ("2516", "1490"): 0.55,
    ("2516", "2529"): 0.60,
    ("1477", "1478"): 0.75,
    ("1477", "1490"): 0.85,
    ("1477", "2529"): 0.70,
    ("1478", "1490"): 0.85,
    ("1478", "2529"): 0.85,
    ("1490", "2529"): 0.80,
}


# 大中华权益一级大类内部：9 个二级风格/因子
GC_INTERNAL = {
    "CSI300": {"name": "沪深300"},
    "DIVLOWVOL": {"name": "红利低波"},
    "DIVLOWVOL100": {"name": "中证红利低波100"},
    "STOCK": {"name": "主动股票/行业"},
    "MIXED": {"name": "混合"},
    "INDEX": {"name": "其他指数"},
    "HK_BROAD": {"name": "港股宽基"},
    "HK_DIV": {"name": "港股高股息"},
    "HK_TECH": {"name": "港股科技"},
}

GC_INTERNAL_CORR = {
    ("CSI300", "DIVLOWVOL"): 0.85,
    ("CSI300", "DIVLOWVOL100"): 0.85,
    ("CSI300", "STOCK"): 0.80,
    ("CSI300", "MIXED"): 0.80,
    ("CSI300", "INDEX"): 0.90,
    ("CSI300", "HK_BROAD"): 0.65,
    ("CSI300", "HK_DIV"): 0.60,
    ("CSI300", "HK_TECH"): 0.55,
    ("DIVLOWVOL", "DIVLOWVOL100"): 0.90,
    ("DIVLOWVOL", "STOCK"): 0.70,
    ("DIVLOWVOL", "MIXED"): 0.75,
    ("DIVLOWVOL", "INDEX"): 0.85,
    ("DIVLOWVOL", "HK_BROAD"): 0.55,
    ("DIVLOWVOL", "HK_DIV"): 0.60,
    ("DIVLOWVOL", "HK_TECH"): 0.45,
    ("DIVLOWVOL100", "STOCK"): 0.70,
    ("DIVLOWVOL100", "MIXED"): 0.75,
    ("DIVLOWVOL100", "INDEX"): 0.85,
    ("DIVLOWVOL100", "HK_BROAD"): 0.55,
    ("DIVLOWVOL100", "HK_DIV"): 0.60,
    ("DIVLOWVOL100", "HK_TECH"): 0.45,
    ("STOCK", "MIXED"): 0.80,
    ("STOCK", "INDEX"): 0.80,
    ("STOCK", "HK_BROAD"): 0.55,
    ("STOCK", "HK_DIV"): 0.50,
    ("STOCK", "HK_TECH"): 0.50,
    ("MIXED", "INDEX"): 0.80,
    ("MIXED", "HK_BROAD"): 0.55,
    ("MIXED", "HK_DIV"): 0.55,
    ("MIXED", "HK_TECH"): 0.50,
    ("INDEX", "HK_BROAD"): 0.55,
    ("INDEX", "HK_DIV"): 0.50,
    ("INDEX", "HK_TECH"): 0.45,
    ("HK_BROAD", "HK_DIV"): 0.85,
    ("HK_BROAD", "HK_TECH"): 0.70,
    ("HK_DIV", "HK_TECH"): 0.55,
}

BROAD_ASSETS = [
    # 境内：HSBC 代销/可投基金（非大中华权益/黄金类保留独立大类）
    {"key": "D_HSBC_SP500", "name": "博时标普500联接A", "fund_code": "050025", "pool": "domestic"},
    {"key": "D_HSBC_CDB", "name": "博时中债3-5年国开行A", "fund_code": "007485", "pool": "domestic"},
    {"key": "D_HSBC_MMF", "name": "博时现金宝货币B", "fund_code": "000891", "pool": "domestic"},
    # 境内：补充的债券 / 全球类保持独立大类
    {"key": "D_CN_CREDIT_BOND", "name": "易方达双债增强债券A", "fund_code": "110035", "pool": "domestic"},
    # QDII 细分三小类 (Bug C): 股票 / 稳健债性 / 商品, 各自独立 mu/vol 与校准代理
    {"key": "D_CN_QDII_STOCK", "name": "QDII全球股票", "fund_code": None, "pool": "domestic"},
    {"key": "D_CN_QDII_STABLE", "name": "QDII稳健配置(债性FOF)", "fund_code": None, "pool": "domestic"},
    {"key": "D_CN_QDII_COMMODITY", "name": "QDII商品/原油", "fund_code": None, "pool": "domestic"},
    # 大中华权益一级大类（跨池：内地权益 + 香港权益，内部再拆 9 类二级）
    {"key": "D_GREATER_CN", "name": "大中华权益（沪深300/红利低波/主动/港股等）", "fund_code": None, "pool": "cross"},
    # 黄金一级大类（跨池：境内黄金基金 + 境外黄金ETF 合并）
    {"key": "O_GOLD", "name": "黄金（境内/境外合并）", "fund_code": None, "pool": "cross"},
    # 境外：核心与已有
    {"key": "O_US_CORE", "name": "美国核心权益（文件内部权重）", "pool": "overseas", "mu": None, "vol": None},
    {"key": "O_BTC", "name": "比特币", "pool": "overseas"},
    {"key": "O_HYLB", "name": "HYLB 高收益信用", "pool": "overseas"},
    {"key": "O_JP_EQ", "name": "日本权益ETF 1698.T", "pool": "overseas"},
    {"key": "O_SG_EQ", "name": "新加坡权益ETF G3B.SI", "pool": "overseas"},
    {"key": "O_US_TLT", "name": "美国长久期国债TLH", "pool": "overseas"},
    {"key": "O_US_REIT", "name": "美国住宅REIT ETF REZ", "pool": "overseas"},
    {"key": "O_US_ENERGY", "name": "能源生产商XOM", "pool": "overseas"},
]

BROAD_CORR = {
    # 原有核心相关
    ("D_HSBC_SP500", "D_HSBC_CSI300"): 0.35,
    ("D_HSBC_SP500", "D_HSBC_DIVLOWVOL"): 0.35,
    ("D_HSBC_SP500", "D_HSBC_CDB"): 0.10,
    ("D_HSBC_SP500", "D_HSBC_GOLD"): 0.05,
    ("D_HSBC_SP500", "D_HSBC_HSTECH"): 0.40,
    ("D_HSBC_SP500", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_SP500", "O_US_CORE"): 0.90,
    ("D_HSBC_SP500", "O_BTC"): 0.25,
    ("D_HSBC_SP500", "O_HYLB"): 0.45,
    ("D_HSBC_CSI300", "D_HSBC_DIVLOWVOL"): 0.85,
    ("D_HSBC_CSI300", "D_HSBC_CDB"): 0.10,
    ("D_HSBC_CSI300", "D_HSBC_GOLD"): 0.10,
    ("D_HSBC_CSI300", "D_HSBC_HSTECH"): 0.60,
    ("D_HSBC_CSI300", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_CSI300", "O_US_CORE"): 0.35,
    ("D_HSBC_CSI300", "O_BTC"): 0.20,
    ("D_HSBC_CSI300", "O_HYLB"): 0.35,
    ("D_HSBC_DIVLOWVOL", "D_HSBC_CDB"): 0.15,
    ("D_HSBC_DIVLOWVOL", "D_HSBC_GOLD"): 0.05,
    ("D_HSBC_DIVLOWVOL", "D_HSBC_HSTECH"): 0.50,
    ("D_HSBC_DIVLOWVOL", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_DIVLOWVOL", "O_US_CORE"): 0.35,
    ("D_HSBC_DIVLOWVOL", "O_BTC"): 0.15,
    ("D_HSBC_DIVLOWVOL", "O_HYLB"): 0.30,
    # 新增：博时中证红利低波动100ETF联接A
    ("D_CN_DIVLOWVOL100", "D_HSBC_SP500"): 0.35,
    ("D_CN_DIVLOWVOL100", "D_HSBC_CSI300"): 0.75,
    ("D_CN_DIVLOWVOL100", "D_HSBC_DIVLOWVOL"): 0.85,
    ("D_CN_DIVLOWVOL100", "D_HSBC_CDB"): 0.15,
    ("D_CN_DIVLOWVOL100", "D_HSBC_GOLD"): 0.05,
    ("D_CN_DIVLOWVOL100", "D_HSBC_HSTECH"): 0.50,
    ("D_CN_DIVLOWVOL100", "D_HSBC_MMF"): 0.00,
    ("D_CN_DIVLOWVOL100", "D_CN_CREDIT_BOND"): 0.15,
    ("D_CN_DIVLOWVOL100", "O_US_CORE"): 0.35,
    ("D_CN_DIVLOWVOL100", "O_BTC"): 0.15,
    ("D_CN_DIVLOWVOL100", "O_HYLB"): 0.30,
    ("D_CN_DIVLOWVOL100", "O_HK_HSTECH"): 0.50,
    ("D_CN_DIVLOWVOL100", "O_HK_HIGHDIV"): 0.50,
    ("D_CN_DIVLOWVOL100", "O_JP_EQ"): 0.35,
    ("D_CN_DIVLOWVOL100", "O_SG_EQ"): 0.30,
    ("D_CN_DIVLOWVOL100", "O_US_TLT"): -0.05,
    ("D_CN_DIVLOWVOL100", "O_US_REIT"): 0.30,
    ("D_CN_DIVLOWVOL100", "O_US_ENERGY"): 0.25,
    ("D_CN_DIVLOWVOL100", "O_HK_GOLD"): 0.00,
    ("D_HSBC_CDB", "D_HSBC_GOLD"): -0.10,
    ("D_HSBC_CDB", "D_HSBC_HSTECH"): 0.10,
    ("D_HSBC_CDB", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_CDB", "O_US_CORE"): 0.00,
    ("D_HSBC_CDB", "O_BTC"): -0.10,
    ("D_HSBC_CDB", "O_HYLB"): 0.20,
    ("D_HSBC_GOLD", "D_HSBC_HSTECH"): 0.05,
    ("D_HSBC_GOLD", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_GOLD", "O_US_CORE"): 0.00,
    ("D_HSBC_GOLD", "O_BTC"): 0.15,
    ("D_HSBC_GOLD", "O_HYLB"): 0.10,
    ("D_HSBC_HSTECH", "D_HSBC_MMF"): 0.00,
    ("D_HSBC_HSTECH", "O_US_CORE"): 0.45,
    ("D_HSBC_HSTECH", "O_BTC"): 0.20,
    ("D_HSBC_HSTECH", "O_HYLB"): 0.35,
    ("D_HSBC_MMF", "O_US_CORE"): 0.00,
    ("D_HSBC_MMF", "O_BTC"): 0.00,
    ("D_HSBC_MMF", "O_HYLB"): 0.00,
    ("O_US_CORE", "O_BTC"): 0.25,
    ("O_US_CORE", "O_HYLB"): 0.45,
    ("O_BTC", "O_HYLB"): 0.10,
    # 新增：境内信用债
    ("D_CN_CREDIT_BOND", "D_HSBC_SP500"): 0.15,
    ("D_CN_CREDIT_BOND", "D_HSBC_CSI300"): 0.15,
    ("D_CN_CREDIT_BOND", "D_HSBC_DIVLOWVOL"): 0.15,
    ("D_CN_CREDIT_BOND", "D_HSBC_CDB"): 0.60,
    ("D_CN_CREDIT_BOND", "D_HSBC_GOLD"): 0.05,
    ("D_CN_CREDIT_BOND", "D_HSBC_HSTECH"): 0.15,
    ("D_CN_CREDIT_BOND", "D_HSBC_MMF"): 0.00,
    ("D_CN_CREDIT_BOND", "O_US_CORE"): 0.20,
    ("D_CN_CREDIT_BOND", "O_BTC"): 0.00,
    ("D_CN_CREDIT_BOND", "O_HYLB"): 0.50,
    ("D_CN_CREDIT_BOND", "O_HK_HSTECH"): 0.15,
    ("D_CN_CREDIT_BOND", "O_HK_HIGHDIV"): 0.15,
    ("D_CN_CREDIT_BOND", "O_JP_EQ"): 0.15,
    ("D_CN_CREDIT_BOND", "O_SG_EQ"): 0.15,
    ("D_CN_CREDIT_BOND", "O_US_TLT"): 0.30,
    ("D_CN_CREDIT_BOND", "O_US_REIT"): 0.20,
    ("D_CN_CREDIT_BOND", "O_US_ENERGY"): 0.15,
    ("D_CN_CREDIT_BOND", "O_HK_GOLD"): 0.05,
    # 新增：港股/日本/新加坡权益
    ("O_HK_HSTECH", "D_HSBC_SP500"): 0.40,
    ("O_HK_HSTECH", "D_HSBC_CSI300"): 0.60,
    ("O_HK_HSTECH", "D_HSBC_DIVLOWVOL"): 0.50,
    ("O_HK_HSTECH", "D_HSBC_CDB"): 0.10,
    ("O_HK_HSTECH", "D_HSBC_GOLD"): 0.05,
    ("O_HK_HSTECH", "D_HSBC_HSTECH"): 0.95,
    ("O_HK_HSTECH", "D_HSBC_MMF"): 0.00,
    ("O_HK_HSTECH", "O_US_CORE"): 0.40,
    ("O_HK_HSTECH", "O_BTC"): 0.20,
    ("O_HK_HSTECH", "O_HYLB"): 0.35,
    ("O_HK_HSTECH", "O_HK_HIGHDIV"): 0.55,
    ("O_HK_HSTECH", "O_JP_EQ"): 0.45,
    ("O_HK_HSTECH", "O_SG_EQ"): 0.40,
    ("O_HK_HSTECH", "O_US_TLT"): -0.10,
    ("O_HK_HSTECH", "O_US_REIT"): 0.30,
    ("O_HK_HSTECH", "O_US_ENERGY"): 0.25,
    ("O_HK_HSTECH", "O_HK_GOLD"): 0.05,
    ("O_HK_HIGHDIV", "D_HSBC_SP500"): 0.30,
    ("O_HK_HIGHDIV", "D_HSBC_CSI300"): 0.50,
    ("O_HK_HIGHDIV", "D_HSBC_DIVLOWVOL"): 0.50,
    ("O_HK_HIGHDIV", "D_HSBC_CDB"): 0.10,
    ("O_HK_HIGHDIV", "D_HSBC_GOLD"): 0.05,
    ("O_HK_HIGHDIV", "D_HSBC_HSTECH"): 0.50,
    ("O_HK_HIGHDIV", "D_HSBC_MMF"): 0.00,
    ("O_HK_HIGHDIV", "O_US_CORE"): 0.40,
    ("O_HK_HIGHDIV", "O_BTC"): 0.15,
    ("O_HK_HIGHDIV", "O_HYLB"): 0.35,
    ("O_HK_HIGHDIV", "O_JP_EQ"): 0.40,
    ("O_HK_HIGHDIV", "O_SG_EQ"): 0.45,
    ("O_HK_HIGHDIV", "O_US_TLT"): -0.10,
    ("O_HK_HIGHDIV", "O_US_REIT"): 0.30,
    ("O_HK_HIGHDIV", "O_US_ENERGY"): 0.25,
    ("O_HK_HIGHDIV", "O_HK_GOLD"): 0.05,
    ("O_JP_EQ", "D_HSBC_SP500"): 0.35,
    ("O_JP_EQ", "D_HSBC_CSI300"): 0.40,
    ("O_JP_EQ", "D_HSBC_DIVLOWVOL"): 0.35,
    ("O_JP_EQ", "D_HSBC_CDB"): 0.00,
    ("O_JP_EQ", "D_HSBC_GOLD"): 0.00,
    ("O_JP_EQ", "D_HSBC_HSTECH"): 0.40,
    ("O_JP_EQ", "D_HSBC_MMF"): 0.00,
    ("O_JP_EQ", "O_US_CORE"): 0.50,
    ("O_JP_EQ", "O_BTC"): 0.20,
    ("O_JP_EQ", "O_HYLB"): 0.35,
    ("O_JP_EQ", "O_SG_EQ"): 0.45,
    ("O_JP_EQ", "O_US_TLT"): -0.10,
    ("O_JP_EQ", "O_US_REIT"): 0.40,
    ("O_JP_EQ", "O_US_ENERGY"): 0.35,
    ("O_JP_EQ", "O_HK_GOLD"): 0.00,
    ("O_SG_EQ", "D_HSBC_SP500"): 0.30,
    ("O_SG_EQ", "D_HSBC_CSI300"): 0.35,
    ("O_SG_EQ", "D_HSBC_DIVLOWVOL"): 0.30,
    ("O_SG_EQ", "D_HSBC_CDB"): 0.00,
    ("O_SG_EQ", "D_HSBC_GOLD"): 0.00,
    ("O_SG_EQ", "D_HSBC_HSTECH"): 0.35,
    ("O_SG_EQ", "D_HSBC_MMF"): 0.00,
    ("O_SG_EQ", "O_US_CORE"): 0.50,
    ("O_SG_EQ", "O_BTC"): 0.15,
    ("O_SG_EQ", "O_HYLB"): 0.30,
    ("O_SG_EQ", "O_US_TLT"): -0.10,
    ("O_SG_EQ", "O_US_REIT"): 0.40,
    ("O_SG_EQ", "O_US_ENERGY"): 0.35,
    ("O_SG_EQ", "O_HK_GOLD"): 0.00,
    # 新增：美债/REIT/能源/黄金
    ("O_US_TLT", "D_HSBC_SP500"): -0.10,
    ("O_US_TLT", "D_HSBC_CSI300"): -0.10,
    ("O_US_TLT", "D_HSBC_DIVLOWVOL"): -0.05,
    ("O_US_TLT", "D_HSBC_CDB"): 0.30,
    ("O_US_TLT", "D_HSBC_GOLD"): 0.10,
    ("O_US_TLT", "D_HSBC_HSTECH"): -0.10,
    ("O_US_TLT", "D_HSBC_MMF"): 0.00,
    ("O_US_TLT", "O_US_CORE"): -0.30,
    ("O_US_TLT", "O_BTC"): -0.05,
    ("O_US_TLT", "O_HYLB"): 0.30,
    ("O_US_TLT", "O_US_REIT"): -0.10,
    ("O_US_TLT", "O_US_ENERGY"): -0.10,
    ("O_US_TLT", "O_HK_GOLD"): 0.10,
    ("O_US_REIT", "D_HSBC_SP500"): 0.50,
    ("O_US_REIT", "D_HSBC_CSI300"): 0.30,
    ("O_US_REIT", "D_HSBC_DIVLOWVOL"): 0.30,
    ("O_US_REIT", "D_HSBC_CDB"): 0.10,
    ("O_US_REIT", "D_HSBC_GOLD"): 0.00,
    ("O_US_REIT", "D_HSBC_HSTECH"): 0.30,
    ("O_US_REIT", "D_HSBC_MMF"): 0.00,
    ("O_US_REIT", "O_US_CORE"): 0.65,
    ("O_US_REIT", "O_BTC"): 0.20,
    ("O_US_REIT", "O_HYLB"): 0.40,
    ("O_US_REIT", "O_US_ENERGY"): 0.40,
    ("O_US_REIT", "O_HK_GOLD"): 0.00,
    ("O_US_ENERGY", "D_HSBC_SP500"): 0.45,
    ("O_US_ENERGY", "D_HSBC_CSI300"): 0.30,
    ("O_US_ENERGY", "D_HSBC_DIVLOWVOL"): 0.25,
    ("O_US_ENERGY", "D_HSBC_CDB"): 0.00,
    ("O_US_ENERGY", "D_HSBC_GOLD"): 0.15,
    ("O_US_ENERGY", "D_HSBC_HSTECH"): 0.25,
    ("O_US_ENERGY", "D_HSBC_MMF"): 0.00,
    ("O_US_ENERGY", "O_US_CORE"): 0.60,
    ("O_US_ENERGY", "O_BTC"): 0.15,
    ("O_US_ENERGY", "O_HYLB"): 0.35,
    ("O_US_ENERGY", "O_HK_GOLD"): 0.15,
    ("O_HK_GOLD", "D_HSBC_SP500"): 0.00,
    ("O_HK_GOLD", "D_HSBC_CSI300"): 0.00,
    ("O_HK_GOLD", "D_HSBC_DIVLOWVOL"): 0.00,
    ("O_HK_GOLD", "D_HSBC_CDB"): -0.10,
    ("O_HK_GOLD", "D_HSBC_GOLD"): 0.90,
    ("O_HK_GOLD", "D_HSBC_HSTECH"): 0.05,
    ("O_HK_GOLD", "D_HSBC_MMF"): 0.00,
    ("O_HK_GOLD", "O_US_CORE"): 0.00,
    ("O_HK_GOLD", "O_BTC"): 0.15,
    ("O_HK_GOLD", "O_HYLB"): 0.10,
    # 新增：基金搜索易开放基金类别（主动股票/混合/指数/港股/QDII全球）
    ("D_CN_STOCK", "D_HSBC_SP500"): 0.30,
    ("D_CN_STOCK", "D_HSBC_CSI300"): 0.80,
    ("D_CN_STOCK", "D_HSBC_DIVLOWVOL"): 0.70,
    ("D_CN_STOCK", "D_HSBC_CDB"): 0.10,
    ("D_CN_STOCK", "D_HSBC_GOLD"): 0.05,
    ("D_CN_STOCK", "D_HSBC_HSTECH"): 0.60,
    ("D_CN_STOCK", "D_HSBC_MMF"): 0.00,
    ("D_CN_STOCK", "D_CN_CREDIT_BOND"): 0.15,
    ("D_CN_STOCK", "D_CN_DIVLOWVOL100"): 0.70,
    ("D_CN_STOCK", "O_US_CORE"): 0.30,
    ("D_CN_STOCK", "O_BTC"): 0.15,
    ("D_CN_STOCK", "O_HYLB"): 0.30,
    ("D_CN_STOCK", "O_HK_HSTECH"): 0.60,
    ("D_CN_STOCK", "O_HK_HIGHDIV"): 0.50,
    ("D_CN_STOCK", "O_JP_EQ"): 0.40,
    ("D_CN_STOCK", "O_SG_EQ"): 0.35,
    ("D_CN_STOCK", "O_US_TLT"): -0.10,
    ("D_CN_STOCK", "O_US_REIT"): 0.30,
    ("D_CN_STOCK", "O_US_ENERGY"): 0.30,
    ("D_CN_STOCK", "O_HK_GOLD"): 0.05,
    ("D_CN_MIXED", "D_HSBC_SP500"): 0.30,
    ("D_CN_MIXED", "D_HSBC_CSI300"): 0.70,
    ("D_CN_MIXED", "D_HSBC_DIVLOWVOL"): 0.70,
    ("D_CN_MIXED", "D_HSBC_CDB"): 0.20,
    ("D_CN_MIXED", "D_HSBC_GOLD"): 0.05,
    ("D_CN_MIXED", "D_HSBC_HSTECH"): 0.50,
    ("D_CN_MIXED", "D_HSBC_MMF"): 0.00,
    ("D_CN_MIXED", "D_CN_CREDIT_BOND"): 0.40,
    ("D_CN_MIXED", "D_CN_DIVLOWVOL100"): 0.70,
    ("D_CN_MIXED", "O_US_CORE"): 0.30,
    ("D_CN_MIXED", "O_BTC"): 0.10,
    ("D_CN_MIXED", "O_HYLB"): 0.30,
    ("D_CN_MIXED", "O_HK_HSTECH"): 0.50,
    ("D_CN_MIXED", "O_HK_HIGHDIV"): 0.50,
    ("D_CN_MIXED", "O_JP_EQ"): 0.35,
    ("D_CN_MIXED", "O_SG_EQ"): 0.30,
    ("D_CN_MIXED", "O_US_TLT"): -0.05,
    ("D_CN_MIXED", "O_US_REIT"): 0.30,
    ("D_CN_MIXED", "O_US_ENERGY"): 0.25,
    ("D_CN_MIXED", "O_HK_GOLD"): 0.05,
    ("D_CN_INDEX", "D_HSBC_SP500"): 0.30,
    ("D_CN_INDEX", "D_HSBC_CSI300"): 0.90,
    ("D_CN_INDEX", "D_HSBC_DIVLOWVOL"): 0.80,
    ("D_CN_INDEX", "D_HSBC_CDB"): 0.10,
    ("D_CN_INDEX", "D_HSBC_GOLD"): 0.05,
    ("D_CN_INDEX", "D_HSBC_HSTECH"): 0.60,
    ("D_CN_INDEX", "D_HSBC_MMF"): 0.00,
    ("D_CN_INDEX", "D_CN_CREDIT_BOND"): 0.15,
    ("D_CN_INDEX", "D_CN_DIVLOWVOL100"): 0.85,
    ("D_CN_INDEX", "O_US_CORE"): 0.30,
    ("D_CN_INDEX", "O_BTC"): 0.15,
    ("D_CN_INDEX", "O_HYLB"): 0.30,
    ("D_CN_INDEX", "O_HK_HSTECH"): 0.55,
    ("D_CN_INDEX", "O_HK_HIGHDIV"): 0.50,
    ("D_CN_INDEX", "O_JP_EQ"): 0.40,
    ("D_CN_INDEX", "O_SG_EQ"): 0.35,
    ("D_CN_INDEX", "O_US_TLT"): -0.10,
    ("D_CN_INDEX", "O_US_REIT"): 0.30,
    ("D_CN_INDEX", "O_US_ENERGY"): 0.30,
    ("D_CN_INDEX", "O_HK_GOLD"): 0.05,
    ("D_CN_HK", "D_HSBC_SP500"): 0.35,
    ("D_CN_HK", "D_HSBC_CSI300"): 0.60,
    ("D_CN_HK", "D_HSBC_DIVLOWVOL"): 0.50,
    ("D_CN_HK", "D_HSBC_CDB"): 0.10,
    ("D_CN_HK", "D_HSBC_GOLD"): 0.05,
    ("D_CN_HK", "D_HSBC_HSTECH"): 0.80,
    ("D_CN_HK", "D_HSBC_MMF"): 0.00,
    ("D_CN_HK", "D_CN_CREDIT_BOND"): 0.15,
    ("D_CN_HK", "D_CN_DIVLOWVOL100"): 0.50,
    ("D_CN_HK", "O_US_CORE"): 0.35,
    ("D_CN_HK", "O_BTC"): 0.15,
    ("D_CN_HK", "O_HYLB"): 0.30,
    ("D_CN_HK", "O_HK_HSTECH"): 0.80,
    ("D_CN_HK", "O_HK_HIGHDIV"): 0.70,
    ("D_CN_HK", "O_JP_EQ"): 0.40,
    ("D_CN_HK", "O_SG_EQ"): 0.40,
    ("D_CN_HK", "O_US_TLT"): -0.10,
    ("D_CN_HK", "O_US_REIT"): 0.30,
    ("D_CN_HK", "O_US_ENERGY"): 0.25,
    ("D_CN_HK", "O_HK_GOLD"): 0.05,
    # QDII 股票小类: 跟随全球股市, 与美股核心高度相关
    ("D_CN_QDII_STOCK", "D_HSBC_SP500"): 0.85,
    ("D_CN_QDII_STOCK", "D_HSBC_CSI300"): 0.40,
    ("D_CN_QDII_STOCK", "D_HSBC_DIVLOWVOL"): 0.35,
    ("D_CN_QDII_STOCK", "D_HSBC_CDB"): 0.05,
    ("D_CN_QDII_STOCK", "D_HSBC_GOLD"): 0.10,
    ("D_CN_QDII_STOCK", "D_HSBC_HSTECH"): 0.40,
    ("D_CN_QDII_STOCK", "D_HSBC_MMF"): 0.00,
    ("D_CN_QDII_STOCK", "D_CN_CREDIT_BOND"): 0.10,
    ("D_CN_QDII_STOCK", "D_CN_DIVLOWVOL100"): 0.35,
    ("D_CN_QDII_STOCK", "O_US_CORE"): 0.85,
    ("D_CN_QDII_STOCK", "O_BTC"): 0.20,
    ("D_CN_QDII_STOCK", "O_HYLB"): 0.40,
    ("D_CN_QDII_STOCK", "O_HK_HSTECH"): 0.55,
    ("D_CN_QDII_STOCK", "O_HK_HIGHDIV"): 0.45,
    ("D_CN_QDII_STOCK", "O_JP_EQ"): 0.55,
    ("D_CN_QDII_STOCK", "O_SG_EQ"): 0.50,
    ("D_CN_QDII_STOCK", "O_US_TLT"): -0.05,
    ("D_CN_QDII_STOCK", "O_US_REIT"): 0.45,
    ("D_CN_QDII_STOCK", "O_US_ENERGY"): 0.35,
    ("D_CN_QDII_STOCK", "O_HK_GOLD"): 0.10,
    # QDII 稳健小类: 债性, 与信用债/高收益债/长久期国债相关
    ("D_CN_QDII_STABLE", "D_HSBC_SP500"): 0.25,
    ("D_CN_QDII_STABLE", "D_HSBC_CSI300"): 0.10,
    ("D_CN_QDII_STABLE", "D_HSBC_DIVLOWVOL"): 0.10,
    ("D_CN_QDII_STABLE", "D_HSBC_CDB"): 0.30,
    ("D_CN_QDII_STABLE", "D_HSBC_GOLD"): 0.05,
    ("D_CN_QDII_STABLE", "D_HSBC_HSTECH"): 0.15,
    ("D_CN_QDII_STABLE", "D_HSBC_MMF"): 0.05,
    ("D_CN_QDII_STABLE", "D_CN_CREDIT_BOND"): 0.45,
    ("D_CN_QDII_STABLE", "D_CN_DIVLOWVOL100"): 0.10,
    ("D_CN_QDII_STABLE", "O_US_CORE"): 0.25,
    ("D_CN_QDII_STABLE", "O_BTC"): 0.05,
    ("D_CN_QDII_STABLE", "O_HYLB"): 0.55,
    ("D_CN_QDII_STABLE", "O_HK_HSTECH"): 0.15,
    ("D_CN_QDII_STABLE", "O_HK_HIGHDIV"): 0.15,
    ("D_CN_QDII_STABLE", "O_JP_EQ"): 0.15,
    ("D_CN_QDII_STABLE", "O_SG_EQ"): 0.15,
    ("D_CN_QDII_STABLE", "O_US_TLT"): 0.55,
    ("D_CN_QDII_STABLE", "O_US_REIT"): 0.20,
    ("D_CN_QDII_STABLE", "O_US_ENERGY"): 0.05,
    ("D_CN_QDII_STABLE", "O_HK_GOLD"): 0.05,
    # QDII 商品小类: 原油/大宗, 与能源股和黄金相关
    ("D_CN_QDII_COMMODITY", "D_HSBC_SP500"): 0.25,
    ("D_CN_QDII_COMMODITY", "D_HSBC_CSI300"): 0.15,
    ("D_CN_QDII_COMMODITY", "D_HSBC_CDB"): 0.05,
    ("D_CN_QDII_COMMODITY", "D_HSBC_GOLD"): 0.35,
    ("D_CN_QDII_COMMODITY", "D_HSBC_MMF"): 0.00,
    ("D_CN_QDII_COMMODITY", "D_CN_CREDIT_BOND"): 0.05,
    ("D_CN_QDII_COMMODITY", "O_US_CORE"): 0.25,
    ("D_CN_QDII_COMMODITY", "O_BTC"): 0.10,
    ("D_CN_QDII_COMMODITY", "O_HYLB"): 0.20,
    ("D_CN_QDII_COMMODITY", "O_HK_HSTECH"): 0.20,
    ("D_CN_QDII_COMMODITY", "O_US_TLT"): -0.05,
    ("D_CN_QDII_COMMODITY", "O_US_REIT"): 0.20,
    ("D_CN_QDII_COMMODITY", "O_US_ENERGY"): 0.50,
    ("D_CN_QDII_COMMODITY", "O_HK_GOLD"): 0.45,
    ("D_CN_QDII_COMMODITY", "O_GOLD"): 0.45,
    ("D_CN_QDII_COMMODITY", "D_CN_QDII_STOCK"): 0.25,
    ("D_CN_QDII_COMMODITY", "D_CN_QDII_STABLE"): 0.05,
    ("D_CN_STOCK", "D_CN_MIXED"): 0.70,
    ("D_CN_STOCK", "D_CN_INDEX"): 0.80,
    ("D_CN_STOCK", "D_CN_HK"): 0.55,
    ("D_CN_STOCK", "D_CN_QDII_STOCK"): 0.45,
    ("D_CN_STOCK", "D_CN_QDII_STABLE"): 0.15,
    ("D_CN_STOCK", "D_CN_QDII_COMMODITY"): 0.20,
    ("D_CN_MIXED", "D_CN_INDEX"): 0.70,
    ("D_CN_MIXED", "D_CN_HK"): 0.50,
    ("D_CN_MIXED", "D_CN_QDII_STOCK"): 0.45,
    ("D_CN_MIXED", "D_CN_QDII_STABLE"): 0.15,
    ("D_CN_MIXED", "D_CN_QDII_COMMODITY"): 0.20,
    ("D_CN_INDEX", "D_CN_HK"): 0.50,
    ("D_CN_INDEX", "D_CN_QDII_STOCK"): 0.40,
    ("D_CN_INDEX", "D_CN_QDII_STABLE"): 0.12,
    ("D_CN_INDEX", "D_CN_QDII_COMMODITY"): 0.18,
    ("D_CN_HK", "D_CN_QDII_STOCK"): 0.50,
    ("D_CN_HK", "D_CN_QDII_STABLE"): 0.15,
    ("D_CN_HK", "D_CN_QDII_COMMODITY"): 0.20,
}


def higham_psd(matrix, max_iter=200):
    """Project a symmetric matrix to the nearest PSD correlation matrix."""
    a = (matrix + matrix.T) / 2.0
    x = a.copy()
    y = a.copy()
    for _ in range(max_iter):
        r = x - y
        vals, vecs = np.linalg.eigh(x)
        vals = np.clip(vals, 0.0, None)
        xn = vecs @ np.diag(vals) @ vecs.T
        y = xn + r
        x = y.copy()
    x = (x + x.T) / 2.0
    vals, vecs = np.linalg.eigh(x)
    vals = np.clip(vals, 0.0, None)
    x = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(x))
    corr = x / np.outer(d, d)
    return (corr + corr.T) / 2.0


def us_core_params(internal_weights):
    """Aggregate US core equity mu/vol from current internal weights."""
    keys = [k for k in US_INTERNAL if k in internal_weights and internal_weights[k] > 0]
    weights = np.array([internal_weights[k] for k in keys])
    weights = weights / weights.sum()
    mus = np.array([US_INTERNAL[k]["mu"] for k in keys])
    vols = np.array([US_INTERNAL[k]["vol"] for k in keys])
    corr = np.eye(len(keys))
    for i, ki in enumerate(keys):
        for j, kj in enumerate(keys):
            if i != j:
                corr[i, j] = US_INTERNAL_CORR.get((ki, kj), US_INTERNAL_CORR.get((kj, ki), 0.5))
    cov = np.outer(vols, vols) * corr
    mu = float(weights @ mus)
    vol = float(np.sqrt(weights @ cov @ weights))
    return mu, vol


def build_broad_cov(assets, corr_pairs):
    """Build covariance matrix for broad asset keys."""
    keys = [a["key"] for a in assets]
    vols = np.array([a["vol"] for a in assets])
    corr = np.eye(len(keys))
    for (i, j), v in corr_pairs.items():
        if i in keys and j in keys:
            ii, jj = keys.index(i), keys.index(j)
            corr[ii, jj] = corr[jj, ii] = v
    corr = higham_psd(corr)
    return np.outer(vols, vols) * corr


# 基金搜索易（HSBC China 内地代销开放式基金）的简单类别映射。
# 用于把 500 只开放申购基金归入第一阶段的大类，并在第二阶段作为境内候选池。
DOMESTIC_FUND_KEYWORDS = {
    "D_HSBC_SP500": ["标普500", "纳斯达克100", "美国成长", "美国股票", "美股"],
    "D_HSBC_CSI300": ["沪深300", "中证100", "上证50", "中证500", "中证800", "创业板"],
    "D_HSBC_DIVLOWVOL": ["红利低波", "红利低波动", "红利指数", "高股息", "股息"],
    "D_HSBC_CDB": ["国开", "利率债", "政策性金融债", "金融债"],
    "D_HSBC_GOLD": ["黄金", "贵金属", "商品"],
    "D_HSBC_HSTECH": ["恒生科技", "恒生互联网", "港股科技"],
    "D_HSBC_MMF": ["货币", "现金"],
    "D_CN_CREDIT_BOND": ["纯债", "信用债", "双债", "债券", "短债", "中短债", "定开债", "债"],
    "D_CN_DIVLOWVOL100": ["红利低波动100", "红利低波100"],
    "D_CN_STOCK": ["股票", "行业", "消费", "医药", "科技", "新能源", "制造", "金融", "地产", "环保", "量化", "增强"],
    "D_CN_MIXED": ["混合", "平衡", "灵活配置", "稳健", "回报", "策略", "增长", "收益"],
    "D_CN_HK": ["港股", "沪港深", "香港", "恒生"],
    "D_CN_QDII_GLOBAL": ["QDII", "全球", "亚洲", "太平洋", "国际", "美元", "人民币对冲", "互认"],
    "D_CN_INDEX": ["指数", "ETF", "联接"],
}

# QDII 细分三小类 (Bug C 修复: 原来整桶用 ^GSPC 标普代理, 把债性 FOF 按美股统计).
# 商品最具体先判, 稳健次之, 其余默认 QDII 股票.
QDII_SUB_KEYWORDS = {
    "D_CN_QDII_COMMODITY": ["原油", "石油", "天然气", "大宗商品", "商品", "资源"],
    "D_CN_QDII_STABLE": ["稳健", "债", "固收", "多元配置", "平衡", "绝对收益", "收益"],
}


def _subclass_qdii(name):
    """Split the generic QDII bucket into stock / stable / commodity sub-categories."""
    n = name.replace("[仅电子渠道]", "").strip()
    for key, words in QDII_SUB_KEYWORDS.items():
        if any(w in n for w in words):
            return key
    return "D_CN_QDII_STOCK"

# 分类优先级：先匹配具体指数/主题，再匹配宽泛类型。
DOMESTIC_FUND_ORDER = [
    "D_HSBC_SP500",
    "D_CN_DIVLOWVOL100",
    "D_HSBC_DIVLOWVOL",
    "D_HSBC_CSI300",
    "D_HSBC_CDB",
    "D_HSBC_GOLD",
    "D_HSBC_HSTECH",
    "D_HSBC_MMF",
    "D_CN_CREDIT_BOND",
    "D_CN_HK",
    "D_CN_QDII_GLOBAL",
    "D_CN_INDEX",
    "D_CN_STOCK",
    "D_CN_MIXED",
]


def classify_domestic_fund(name):
    """Map an HSBC open-end fund name to a broad domestic asset key."""
    n = name.replace("[仅电子渠道]", "").strip()
    for key in DOMESTIC_FUND_ORDER:
        if any(w in n for w in DOMESTIC_FUND_KEYWORDS[key]):
            if key == "D_CN_QDII_GLOBAL":
                return _subclass_qdii(n)
            return key
    return "D_CN_MIXED"


def _fund_base_name(name):
    """Strip share-class suffix and channel tags to group A/C of the same fund."""
    n = name.replace("[仅电子渠道]", "").strip()
    for suffix in ("A类", "C类", "A 类", "C 类", "A", "C"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break
    return n


def _dedup_a_class(candidates, anchor=None):
    """Prefer A-class over C-class for the same underlying fund; keep anchor if present."""
    groups = {}
    for c in candidates:
        base = _fund_base_name(c["name"])
        groups.setdefault(base, []).append(c)
    out = []
    for base, items in groups.items():
        # Bug B 修复: 锚定基金(用户实际持有, 可能是 C 类)的份额类别必须原样保留,
        # 不能被 A 类合并掉 — 否则后续按 code 查锚定永远找不到, 会选出同基金的另一份额。
        if anchor:
            anchor_item = next((c for c in items if c["code"] == anchor), None)
            if anchor_item is not None:
                out.append(anchor_item)
                continue
        a_items = [c for c in items if "A类" in c["name"] or c["name"].rstrip().endswith("A")]
        chosen = a_items[0] if a_items else items[0]
        out.append(chosen)
    return out


import os

# External data file paths: env override → Finance/tmp (dev machine) → bundled Resources/data.
# Packaged app sets DSH_FINANCE_DIR via PythonSidecar env, or falls back to Resources/data.
_FINANCE_TMP = os.environ.get("DSH_FINANCE_DIR", os.path.expanduser("~/Finance/tmp"))
_BUNDLE_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _resolve_data_file(filename):
    """Resolve a data file: env DSH_FINANCE_DIR → Finance/tmp → bundle Resources/data."""
    candidates = [os.path.join(_FINANCE_TMP, filename), os.path.join(_BUNDLE_DATA, filename)]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]  # return first candidate path even if missing (caller handles)


HSBC_FEES_PATH = _resolve_data_file("hsbc_fund_fees.json")
_MIN_AUM_YI = 1.0      # 1 亿元以下视为低效
_MIN_AGE_DAYS = 365    # 成立不足 1 年视为低效


def _load_fees():
    try:
        with open(HSBC_FEES_PATH, encoding="utf-8") as f:
            return json.load(f).get("fees", {})
    except Exception:
        return {}


def _inefficient(c, fees):
    info = fees.get(c["code"]) or {}
    aum = info.get("aum_yi")
    if aum is not None and aum < _MIN_AUM_YI:
        return True
    inc = info.get("inception")
    if inc:
        try:
            d0 = datetime.date.fromisoformat(inc)
            if (datetime.date.today() - d0).days < _MIN_AGE_DAYS:
                return True
        except ValueError:
            pass
    return False


def _select_lowest_fee(candidates, fees, anchor):
    """Drop inefficient funds, then keep the lowest annual-cost fund in a style bucket."""
    valid = [c for c in candidates if not _inefficient(c, fees)]
    if not valid:
        valid = candidates  # keep at least the anchor if everything is filtered
    if anchor:
        # Bug A 修复: 锚定基金(静态代表或用户实际持仓)只要在校验后的候选里就优先返回。
        # 原条件 len(valid) == 1 使得候选>=2 时锚定永远被最低费率分支覆盖(死代码)。
        anchor_fund = next((c for c in valid if c["code"] == anchor), None)
        if anchor_fund is not None:
            return [anchor_fund]

    def cost(c):
        info = fees.get(c["code"]) or {}
        return info.get("annual_cost")

    with_cost = [c for c in valid if cost(c) is not None]
    if with_cost:
        best = min(with_cost, key=lambda c: (cost(c), -((fees.get(c["code"]) or {}).get("aum_yi") or 0)))
        return [best]
    if anchor:
        a = next((c for c in valid if c["code"] == anchor), None)
        return [a] if a else [valid[0]]
    return [valid[0]]


def load_hsbc_fund_pool(path, live_anchors=None):
    """Load 基金搜索易 open funds JSON and group them by broad asset key.

    A-share dedup rules:
    - A/C 份额只保留 A 类；
    - 同一基金/风格仅保留一个代表；
    - 按费率最低去重（使用 tmp/hsbc_fund_fees.json），并剔除规模<1亿或成立<1年的低效基金。

    live_anchors: dict of {category_key: fund_code} from the App's actual domestic
    holdings (extract_live.json domestic_holdings). When a category has a live
    anchor, it overrides params.py's static fund_code — so select_domestic_funds
    prefers the user's actual fund over a hardcoded stale snapshot.
    """
    fees = _load_fees()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds", data if isinstance(data, list) else [])
    anchors = {a["key"]: a.get("fund_code") for a in BROAD_ASSETS if a["pool"] == "domestic"}
    if live_anchors:
        anchors = {**anchors, **live_anchors}  # live 优先, 静态作 fallback
    pool = {}
    for item in funds:
        if not item.get("open", True):
            continue
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue
        key = classify_domestic_fund(name)
        pool.setdefault(key, []).append({"code": code, "name": name})
    for key, candidates in pool.items():
        anchor = anchors.get(key)
        deduped = _dedup_a_class(candidates, anchor)
        pool[key] = _select_lowest_fee(deduped, fees, anchor)
    return pool


def load_hsbc_raw_funds(path):
    """Load the raw 基金搜索易 funds list (code/name/status), for cross-source checks."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds", data if isinstance(data, list) else [])
    return funds


def _dedup_with_fallback(candidates, anchor=None):
    """A/C 去重, 但每组保留 [锚定/首选, A类备选] 两个候选。

    与 _dedup_a_class 的区别: 天天基金状态过滤前不能只留一个 —— 锚定份额
    (可能是 C 类) 若被暂停申购, 还需要同基金的 A 类兄弟份额兜底, 所以每组
    最多保留两个候选, 供状态过滤后回退。
    """
    groups = {}
    for c in candidates:
        groups.setdefault(_fund_base_name(c["name"]), []).append(c)
    out = []
    for base, items in groups.items():
        picks = []
        if anchor:
            anchor_item = next((c for c in items if c["code"] == anchor), None)
            if anchor_item is not None:
                picks.append(anchor_item)
        a_items = [c for c in items if "A类" in c["name"] or c["name"].rstrip().endswith("A")]
        preferred = a_items[0] if a_items else items[0]
        if all(p["code"] != preferred["code"] for p in picks):
            picks.append(preferred)
        out.extend(picks)
    return out


def build_open_pool_from_status(hsbc_funds, availability, live_anchors=None):
    """按天天基金实时校验结果构建境内开放基金池 (每次优化运行调用)。

    流程 (REQ: 所有基金大类的申赎状态都走天天基金实时联网):
      fund_pipeline.build_check_plan 每类排序 (锚定优先→费率升序→备选垫后)
      → check_fund_availability.py --plan-file 分组早停校验 (每类查到第一只
        开放申购即停; 天天基金无数据的互认基金回退汇丰实时口径并标注)
      → 本函数直接取 selection 中每类选中的开放代表基金。

    hsbc_funds: sync_hsbc_funds.py 实时抓取的开放基金列表
    availability: check_fund_availability.py 输出 (funds + selection + plan)
    live_anchors: {category_key: fund_code} 用户实际持仓锚定 (覆盖静态 fund_code)

    返回 (pool, meta):
      pool: {broad_key: [代表基金 {code,name}]}, 仅含选中开放基金的类别
      meta: {"checked": n, "kept": n, "categories": {key: {tried, open_found, anchor, rep}}}
    """
    anchors = {a["key"]: a.get("fund_code") for a in BROAD_ASSETS if a["pool"] == "domestic"}
    if live_anchors:
        anchors = {**anchors, **live_anchors}  # live 优先, 静态作 fallback

    avail_funds = availability.get("funds", {})
    selection = availability.get("selection", {})
    code_name = {
        str(f.get("code", "")).strip(): str(f.get("name", "")).strip()
        for f in hsbc_funds
    }

    pool = {}
    meta = {"checked": len(avail_funds), "kept": 0, "categories": {}}
    for key, sel in selection.items():
        if key.startswith("_"):
            continue  # _anchors / _all 等兜底组不进基金池
        chosen = sel.get("chosen")
        cat_meta = {
            "tried": sel.get("tried", []),
            "open_found": bool(sel.get("open_found")),
            "anchor": anchors.get(key),
        }
        if chosen:
            pool[key] = [{"code": chosen, "name": code_name.get(chosen, "")}]
            cat_meta["rep"] = chosen
            meta["kept"] += 1
        meta["categories"][key] = cat_meta
    return pool, meta


# ---------------------------------------------------------------------------
# 参数来源：校准文件（历史数据）+ 类别兜底（asset_classes.py 的人工先验/写死值）
# ---------------------------------------------------------------------------
# 规则本身（混合比例、截断线、历史窗口、兜底矩阵）见 asset_classes.py，
# 本模块不再保存任何"静态 μ/σ"——它们已全部退役：
#   有校准值 → 用校准值；没有 → 只对"有写死值"的标的兜底（货币基金）；
#   其余（无行情且无写死值）→ 该标的缺失并报警（σ 没有人工下限，不编造）。
# 行业口径变化记录：
#   - 2026-10-13 之前：所有标的都是 0.5×历史 + 0.5×先验、截断 [0,15%]，σ 上限 0.6。
#   - 2026-10-13 起：按 broad/strategy/active 三类分别处理（见 asset_classes.py）。
CALIBRATION_PATH = _resolve_data_file("calibrated_params.json")
CALIBRATION_LOADED = False
CALIBRATION_SUMMARY = None
CALIBRATION_WARNINGS = []      # 校准阶段的提示（缺数据、σ 毛刺、人工兜底…）
CALIBRATION_PROVENANCE = {}    # key → {class, mu_hist, note, vol_note, ...} 每个数的来历
PARAM_SOURCE = {}              # key → "calibrated" | "written_fallback" | "missing"


def _class_fallback(key):
    """没有校准值时的兜底：只对"有写死值"的标的成立（SPECIAL_FIXED，如货币基金）。

    σ 已无人工下限（2026-10-14），因此没有行情的普通标的构造不出 σ → 返回 (None, None)。
    """
    mu, _ = _AC.build_mu(key, None)
    vol, _ = _AC.build_vol(key, None)
    return mu, vol


def _assign(entry, key, cal_assets):
    """给一条标的写 μ/σ：优先校准值，其次类别兜底；都拿不到就记 missing。"""
    src = cal_assets.get(key)
    if src is not None:
        entry["mu"] = src["mu"]
        entry["vol"] = src["vol"]
        PARAM_SOURCE[key] = "calibrated"
        return True
    mu, vol = _class_fallback(key)
    if mu is None or vol is None:
        PARAM_SOURCE[key] = "missing"
        CALIBRATION_WARNINGS.append(
            f"{key}（{_AC.asset_class(key) or '未登记'}）：校准文件里没有它、也没有写死值兜底 "
            f"→ 本次优化不参与（若该标的应参与，请重新校准）")
        return False
    entry["mu"] = mu
    entry["vol"] = vol
    PARAM_SOURCE[key] = "fixed" if key in _AC.SPECIAL_FIXED else "written_fallback"
    return True


def _apply_calibration():
    """把校准值/类别兜底写进各标的表（表里已不再存静态值）。

    有校准值就用校准值；没有的只对"有写死值"的标的兜底（货币基金）；
    其余记为 missing（缺 σ 就没有可用的风险度量，宁可不参与也不编造）。
    """
    global CALIBRATION_LOADED, CALIBRATION_SUMMARY, CALIBRATION_WARNINGS, CALIBRATION_PROVENANCE
    cal = {}
    try:
        with open(CALIBRATION_PATH, encoding="utf-8") as f:
            cal = json.load(f)
        CALIBRATION_LOADED = True
    except Exception as exc:
        CALIBRATION_SUMMARY = {"loaded": False, "reason": str(exc)}
        CALIBRATION_WARNINGS.append(
            f"校准文件不可用（{type(exc).__name__}）：只有写死值的标的（货币基金）还能兜底，"
            f"其余标的都会因缺 μ/σ 而缺席本次优化")

    cal_assets = cal.get("assets", {})
    # 动态先验（债券公式）：校准文件里带着解析结果，离线也能复现；缺了就用静态兜底表
    _cal_priors = cal.get("priors") or {}
    if cal.get("prior_rf"):
        _AC.set_rf(cal["prior_rf"])          # 当次校准用的本币无风险利率（数据源，非写死）
    if _cal_priors:
        _AC.set_prior_overrides(_cal_priors)  # 解析后的先验（RF+溢价 / 债券公式）

    cal_corr = cal.get("corr", {})
    CALIBRATION_PROVENANCE = cal.get("provenance", {}) or {}
    CALIBRATION_WARNINGS.extend(cal.get("warnings", []) or [])

    # 1) 大类资产（O_US_CORE 例外：运行时由美股内部权重动态聚合）
    for a in BROAD_ASSETS:
        if a["key"] == "O_US_CORE":
            a["mu"] = None
            a["vol"] = None
            PARAM_SOURCE["O_US_CORE"] = "aggregated"
            continue
        _assign(a, a["key"], cal_assets)

    # 2) 内部标的（美国/日本/香港/大中华，同一套逻辑）
    for internal_map in (US_INTERNAL, JP_INTERNAL, GC_INTERNAL):
        for ticker, info in internal_map.items():
            _assign(info, ticker, cal_assets)

    broad_keys = {a["key"] for a in BROAD_ASSETS}
    internal_keys = set(US_INTERNAL.keys())
    jp_keys = set(JP_INTERNAL.keys())
    gc_keys = set(GC_INTERNAL.keys())

    # 3) 用校准相关性重建大类相关性矩阵（校准缺失时保留文件里写死的兜底表）
    if cal_corr:
        BROAD_CORR.clear()
        for pair, v in cal_corr.items():
            a, b = pair.split("|")
            if a in broad_keys and b in broad_keys:
                BROAD_CORR[(a, b)] = v

    # 4) 用校准相关性重建内部相关性矩阵 (美国/日本/香港/大中华 — 同一套逻辑)
    def _rebuild_corr(corr_dict, valid_keys):
        if not cal_corr:
            return
        corr_dict.clear()
        for pair, v in cal_corr.items():
            a, b = pair.split("|")
            if a in valid_keys and b in valid_keys:
                corr_dict[(a, b)] = v

    _rebuild_corr(US_INTERNAL_CORR, internal_keys)
    _rebuild_corr(JP_INTERNAL_CORR, jp_keys)
    _rebuild_corr(GC_INTERNAL_CORR, gc_keys)

    # 5) 报告写死值兜底了哪些标的（这些标的的口径弱于有行情的标的）
    # 说明：前瞻微调（forward_adjusted.json / aistockresearcher）已于 2026-10-14 整体退役，
    # 大类 μ 只来自校准文件 + 类别规则，不再叠加任何事后的前瞻调整。
    fallback = sorted(k for k, v in PARAM_SOURCE.items() if v == "written_fallback")
    if fallback:
        CALIBRATION_WARNINGS.append(
            "以下标的没有行情数据，已用人工兜底值: " + "、".join(fallback))

    CALIBRATION_SUMMARY = {
        "loaded": CALIBRATION_LOADED,
        "path": str(CALIBRATION_PATH),
        "assets": len(cal_assets),
        "corr_pairs": len(cal_corr),
        "generated_at": cal.get("generated_at"),
        "written_fallback": fallback,
    }


def param_origin(key):
    """该标的的参数来自哪里：calibrated / written_fallback / fixed / aggregated / missing。"""
    return PARAM_SOURCE.get(key, "unknown")


def describe_param_source(key):
    """中文一句话，说明该标的 μ/σ 的来历（结果页与 CLI 展示用）。"""
    cls = _AC.asset_class(key)
    origin = param_origin(key)
    if origin == "aggregated":
        return "由美股内部持仓动态聚合（不用校准值）"
    if origin == "fixed":
        return "写死值（货币基金：净值不反映货币收益，固定 μ=2%、σ=1%）"
    if origin == "calibrated":
        p = CALIBRATION_PROVENANCE.get(key, {})
        note = p.get("note")
        if note == "history_only":
            return "纯历史年化（该类无人工先验，口径与其它标的不同）"
        if note == "prior_fallback":
            return "无行情历史，用人工先验 μ"
        if note == "fixed":
            return "写死值（货币基金）"
        base = _AC.describe_class(cls) if cls else "未登记类别"
        return base
    if origin == "written_fallback":
        return "写死值兜底，该标的没有可用行情"
    if origin == "missing":
        return "既无校准值也无人工兜底，本次不参与优化"
    return "来源未知"


_apply_calibration()
