"""Shared assumptions for portfolio optimization."""

import datetime
import json

import numpy as np

RF = 0.025
TARGET_RETURN = 0.10

US_INTERNAL = {
    "SPLG": {"name": "SPDR Portfolio S&P 500 ETF", "mu": 0.090, "vol": 0.17},
    "VTV": {"name": "Vanguard Value ETF", "mu": 0.085, "vol": 0.16},
    "SPMO": {"name": "Invesco S&P 500 Momentum ETF", "mu": 0.095, "vol": 0.19},
    "UNH": {"name": "UnitedHealth Group", "mu": 0.090, "vol": 0.22},
    "GOOG": {"name": "Alphabet Inc.", "mu": 0.090, "vol": 0.22},
    "AAPL": {"name": "Apple Inc.", "mu": 0.090, "vol": 0.24},
    "MSFT": {"name": "Microsoft Corp.", "mu": 0.090, "vol": 0.22},
    "NVDA": {"name": "NVIDIA Corp.", "mu": 0.110, "vol": 0.38},
    "AMZN": {"name": "Amazon.com Inc.", "mu": 0.095, "vol": 0.28},
    "META": {"name": "Meta Platforms Inc.", "mu": 0.100, "vol": 0.30},
    "TSLA": {"name": "Tesla Inc.", "mu": 0.100, "vol": 0.45},
    "BUFFER": {"name": "MAXJ/DMAX 缓冲期权", "mu": 0.070, "vol": 0.10},
}

US_INTERNAL_CORR = {
    ("SPLG", "VTV"): 0.85,
    ("SPLG", "SPMO"): 0.75,
    ("SPLG", "UNH"): 0.75,
    ("SPLG", "GOOG"): 0.75,
    ("SPLG", "AAPL"): 0.70,
    ("SPLG", "MSFT"): 0.70,
    ("SPLG", "NVDA"): 0.65,
    ("SPLG", "AMZN"): 0.70,
    ("SPLG", "META"): 0.70,
    ("SPLG", "TSLA"): 0.55,
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
    ("BUFFER", "SPLG"): 0.60,
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
    "1364": {"name": "iShares JPX-Nikkei 400", "mu": 0.070, "vol": 0.17},
    "1698": {"name": "Listed Index Fund Japan High Dividend", "mu": 0.070, "vol": 0.15},
    "2516": {"name": "TSE Growth 250", "mu": 0.080, "vol": 0.20},
    "1477": {"name": "iShares MSCI Japan Min Vol ex-REITs", "mu": 0.065, "vol": 0.12},
    "1478": {"name": "iShares MSCI Japan High Dividend", "mu": 0.070, "vol": 0.14},
    "1490": {"name": "MSCI Japan High Div Low Vol", "mu": 0.065, "vol": 0.12},
    "2529": {"name": "Nomura Shareholder Yield 70", "mu": 0.075, "vol": 0.16},
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

# 香港权益大类内部：宽基/中企/高股息/科技
HK_INTERNAL = {
    "2800": {"name": "Tracker Fund of Hong Kong", "mu": 0.070, "vol": 0.22},
    "2828": {"name": "Hang Seng China Enterprises Index ETF", "mu": 0.075, "vol": 0.25},
    "3031": {"name": "Fullgoal HSI High Dividend ETF", "mu": 0.070, "vol": 0.16},
    "3067": {"name": "iShares Hang Seng TECH ETF", "mu": 0.080, "vol": 0.30},
    "3110": {"name": "Global X HSI High Dividend Yield ETF", "mu": 0.070, "vol": 0.16},
    "3070": {"name": "Ping An CSI HK Dividend ETF", "mu": 0.070, "vol": 0.16},
}

HK_INTERNAL_CORR = {
    ("2800", "2828"): 0.90,
    ("2800", "3031"): 0.80,
    ("2800", "3067"): 0.70,
    ("2800", "3110"): 0.80,
    ("2800", "3070"): 0.80,
    ("2828", "3031"): 0.75,
    ("2828", "3067"): 0.75,
    ("2828", "3110"): 0.75,
    ("2828", "3070"): 0.75,
    ("3031", "3067"): 0.55,
    ("3031", "3110"): 0.90,
    ("3031", "3070"): 0.90,
    ("3067", "3110"): 0.55,
    ("3067", "3070"): 0.55,
    ("3110", "3070"): 0.90,
}

# 大中华权益一级大类内部：9 个二级风格/因子
GC_INTERNAL = {
    "CSI300": {"name": "沪深300", "mu": 0.075, "vol": 0.20},
    "DIVLOWVOL": {"name": "红利低波", "mu": 0.070, "vol": 0.16},
    "DIVLOWVOL100": {"name": "中证红利低波100", "mu": 0.075, "vol": 0.16},
    "STOCK": {"name": "主动股票/行业", "mu": 0.080, "vol": 0.22},
    "MIXED": {"name": "混合", "mu": 0.070, "vol": 0.15},
    "INDEX": {"name": "其他指数", "mu": 0.075, "vol": 0.18},
    "HK_BROAD": {"name": "港股宽基", "mu": 0.070, "vol": 0.22},
    "HK_DIV": {"name": "港股高股息", "mu": 0.070, "vol": 0.16},
    "HK_TECH": {"name": "港股科技", "mu": 0.080, "vol": 0.30},
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
    {"key": "D_HSBC_SP500", "name": "博时标普500联接A", "fund_code": "050025", "pool": "domestic", "mu": 0.090, "vol": 0.17},
    {"key": "D_HSBC_CDB", "name": "博时中债3-5年国开行A", "fund_code": "007485", "pool": "domestic", "mu": 0.035, "vol": 0.04},
    {"key": "D_HSBC_MMF", "name": "博时现金宝货币B", "fund_code": "000891", "pool": "domestic", "mu": 0.020, "vol": 0.01},
    # 境内：当前实际持仓补充（债券/全球保持独立）
    {"key": "D_CN_CREDIT_BOND", "name": "易方达双债增强债券A", "fund_code": "110035", "pool": "domestic", "mu": 0.045, "vol": 0.05},
    {"key": "D_CN_QDII_GLOBAL", "name": "全球/亚洲QDII及互认基金", "fund_code": None, "pool": "domestic", "mu": 0.075, "vol": 0.18},
    # 大中华权益一级大类（跨池：内地权益 + 香港权益，内部再拆 9 类二级）
    {"key": "D_GREATER_CN", "name": "大中华权益（沪深300/红利低波/主动/港股等）", "fund_code": None, "pool": "cross", "mu": 0.075, "vol": 0.20},
    # 黄金一级大类（跨池：境内黄金基金 + 境外黄金ETF 合并）
    {"key": "O_GOLD", "name": "黄金（境内/境外合并）", "fund_code": None, "pool": "cross", "mu": 0.060, "vol": 0.15},
    # 境外：核心与已有
    {"key": "O_US_CORE", "name": "美国核心权益（文件内部权重）", "pool": "overseas", "mu": None, "vol": None},
    {"key": "O_BTC", "name": "比特币", "pool": "overseas", "mu": 0.150, "vol": 0.60},
    {"key": "O_HYLB", "name": "HYLB 高收益信用", "pool": "overseas", "mu": 0.065, "vol": 0.10},
    {"key": "O_JP_EQ", "name": "日本权益ETF 1698.T", "pool": "overseas", "mu": 0.070, "vol": 0.18},
    {"key": "O_SG_EQ", "name": "新加坡权益ETF G3B.SI", "pool": "overseas", "mu": 0.065, "vol": 0.15},
    {"key": "O_US_TLT", "name": "美国长久期国债TLH", "pool": "overseas", "mu": 0.040, "vol": 0.14},
    {"key": "O_US_REIT", "name": "美国住宅REIT ETF REZ", "pool": "overseas", "mu": 0.065, "vol": 0.18},
    {"key": "O_US_ENERGY", "name": "能源生产商XOM", "pool": "overseas", "mu": 0.075, "vol": 0.25},
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
    ("D_CN_QDII_GLOBAL", "D_HSBC_SP500"): 0.70,
    ("D_CN_QDII_GLOBAL", "D_HSBC_CSI300"): 0.40,
    ("D_CN_QDII_GLOBAL", "D_HSBC_DIVLOWVOL"): 0.35,
    ("D_CN_QDII_GLOBAL", "D_HSBC_CDB"): 0.05,
    ("D_CN_QDII_GLOBAL", "D_HSBC_GOLD"): 0.10,
    ("D_CN_QDII_GLOBAL", "D_HSBC_HSTECH"): 0.40,
    ("D_CN_QDII_GLOBAL", "D_HSBC_MMF"): 0.00,
    ("D_CN_QDII_GLOBAL", "D_CN_CREDIT_BOND"): 0.20,
    ("D_CN_QDII_GLOBAL", "D_CN_DIVLOWVOL100"): 0.35,
    ("D_CN_QDII_GLOBAL", "O_US_CORE"): 0.70,
    ("D_CN_QDII_GLOBAL", "O_BTC"): 0.20,
    ("D_CN_QDII_GLOBAL", "O_HYLB"): 0.40,
    ("D_CN_QDII_GLOBAL", "O_HK_HSTECH"): 0.45,
    ("D_CN_QDII_GLOBAL", "O_HK_HIGHDIV"): 0.40,
    ("D_CN_QDII_GLOBAL", "O_JP_EQ"): 0.50,
    ("D_CN_QDII_GLOBAL", "O_SG_EQ"): 0.45,
    ("D_CN_QDII_GLOBAL", "O_US_TLT"): -0.10,
    ("D_CN_QDII_GLOBAL", "O_US_REIT"): 0.40,
    ("D_CN_QDII_GLOBAL", "O_US_ENERGY"): 0.35,
    ("D_CN_QDII_GLOBAL", "O_HK_GOLD"): 0.10,
    ("D_CN_STOCK", "D_CN_MIXED"): 0.70,
    ("D_CN_STOCK", "D_CN_INDEX"): 0.80,
    ("D_CN_STOCK", "D_CN_HK"): 0.55,
    ("D_CN_STOCK", "D_CN_QDII_GLOBAL"): 0.40,
    ("D_CN_MIXED", "D_CN_INDEX"): 0.70,
    ("D_CN_MIXED", "D_CN_HK"): 0.50,
    ("D_CN_MIXED", "D_CN_QDII_GLOBAL"): 0.40,
    ("D_CN_INDEX", "D_CN_HK"): 0.50,
    ("D_CN_INDEX", "D_CN_QDII_GLOBAL"): 0.35,
    ("D_CN_HK", "D_CN_QDII_GLOBAL"): 0.40,
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


def _dedup_a_class(candidates):
    """Prefer A-class over C-class for the same underlying fund; keep anchor if present."""
    groups = {}
    for c in candidates:
        base = _fund_base_name(c["name"])
        groups.setdefault(base, []).append(c)
    out = []
    for base, items in groups.items():
        a_items = [c for c in items if "A类" in c["name"] or c["name"].rstrip().endswith("A")]
        chosen = a_items[0] if a_items else items[0]
        out.append(chosen)
    return out


HSBC_FEES_PATH = "/Users/sectator/MEGA/Finance/tmp/hsbc_fund_fees.json"
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
        anchor_fund = next((c for c in valid if c["code"] == anchor), None)
        if anchor_fund is not None and len(valid) == 1:
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


def load_hsbc_fund_pool(path):
    """Load 基金搜索易 open funds JSON and group them by broad asset key.

    A-share dedup rules:
    - A/C 份额只保留 A 类；
    - 同一基金/风格仅保留一个代表；
    - 按费率最低去重（使用 tmp/hsbc_fund_fees.json），并剔除规模<1亿或成立<1年的低效基金。
    """
    fees = _load_fees()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds", data if isinstance(data, list) else [])
    anchors = {a["key"]: a.get("fund_code") for a in BROAD_ASSETS if a["pool"] == "domestic"}
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
        deduped = _dedup_a_class(candidates)
        anchor = anchors.get(key)
        pool[key] = _select_lowest_fee(deduped, fees, anchor)
    return pool


# ---------------------------------------------------------------------------
# 市场数据校准（前瞻性混合）
# ---------------------------------------------------------------------------
# 校准文件由 scripts/calibrate_params.py 生成，包含：
#   - 历史年化收益/波动（约 3 年日频数据）
#   - 预期收益 = 60% 历史收益 + 40% 原前瞻假设，并做合理截断
#   - 相关性矩阵 = 历史日收益相关性
# 如果文件存在，模块加载时会自动覆盖上面写死的手工参数。
CALIBRATION_PATH = "/Users/sectator/MEGA/Finance/tmp/calibrated_params.json"
CALIBRATION_LOADED = False
CALIBRATION_SUMMARY = None


def _apply_calibration():
    global CALIBRATION_LOADED, CALIBRATION_SUMMARY
    try:
        with open(CALIBRATION_PATH, encoding="utf-8") as f:
            cal = json.load(f)
    except Exception as exc:
        CALIBRATION_SUMMARY = {"loaded": False, "reason": str(exc)}
        return

    cal_assets = cal.get("assets", {})
    cal_corr = cal.get("corr", {})
    cal_us = cal.get("us_internal", {})

    # 1) 更新大类资产的预期收益和波动率
    for a in BROAD_ASSETS:
        key = a["key"]
        if key == "O_US_CORE":
            # O_US_CORE 的 mu/vol 仍由 Numbers 内部权重动态聚合，不覆盖
            continue
        if key in cal_assets:
            a["mu"] = cal_assets[key]["mu"]
            a["vol"] = cal_assets[key]["vol"]

    # 2) 更新美国核心内部标的
    for ticker, info in US_INTERNAL.items():
        if ticker in cal_us:
            info["mu"] = cal_us[ticker]["mu"]
            info["vol"] = cal_us[ticker]["vol"]
        elif ticker in cal_assets:
            info["mu"] = cal_assets[ticker]["mu"]
            info["vol"] = cal_assets[ticker]["vol"]

    # 2b) 更新日本/香港/新加坡权益内部标的
    cal_jp = cal.get("jp_internal", {})
    for ticker, info in JP_INTERNAL.items():
        if ticker in cal_jp:
            info["mu"] = cal_jp[ticker]["mu"]
            info["vol"] = cal_jp[ticker]["vol"]
        elif ticker in cal_assets:
            info["mu"] = cal_assets[ticker]["mu"]
            info["vol"] = cal_assets[ticker]["vol"]

    cal_hk = cal.get("hk_internal", {})
    for ticker, info in HK_INTERNAL.items():
        if ticker in cal_hk:
            info["mu"] = cal_hk[ticker]["mu"]
            info["vol"] = cal_hk[ticker]["vol"]
        elif ticker in cal_assets:
            info["mu"] = cal_assets[ticker]["mu"]
            info["vol"] = cal_assets[ticker]["vol"]

    cal_gc = cal.get("gc_internal", {})
    for ticker, info in GC_INTERNAL.items():
        if ticker in cal_gc:
            info["mu"] = cal_gc[ticker]["mu"]
            info["vol"] = cal_gc[ticker]["vol"]
        elif ticker in cal_assets:
            info["mu"] = cal_assets[ticker]["mu"]
            info["vol"] = cal_assets[ticker]["vol"]

    broad_keys = {a["key"] for a in BROAD_ASSETS}
    internal_keys = set(US_INTERNAL.keys())
    jp_keys = set(JP_INTERNAL.keys())
    hk_keys = set(HK_INTERNAL.keys())
    gc_keys = set(GC_INTERNAL.keys())

    # 3) 用校准相关性重建大类相关性矩阵
    BROAD_CORR.clear()
    for pair, v in cal_corr.items():
        a, b = pair.split("|")
        if a in broad_keys and b in broad_keys:
            BROAD_CORR[(a, b)] = v

    # 4) 用校准相关性重建美国核心内部相关性
    US_INTERNAL_CORR.clear()
    for pair, v in cal_corr.items():
        a, b = pair.split("|")
        if a in internal_keys and b in internal_keys:
            US_INTERNAL_CORR[(a, b)] = v

    # 4b) 用校准相关性重建日本权益内部相关性
    JP_INTERNAL_CORR.clear()
    for pair, v in cal_corr.items():
        a, b = pair.split("|")
        if a in jp_keys and b in jp_keys:
            JP_INTERNAL_CORR[(a, b)] = v

    # 4c) 用校准相关性重建香港权益内部相关性
    HK_INTERNAL_CORR.clear()
    for pair, v in cal_corr.items():
        a, b = pair.split("|")
        if a in hk_keys and b in hk_keys:
            HK_INTERNAL_CORR[(a, b)] = v

    # 4d) 用校准相关性重建大中华权益内部相关性
    GC_INTERNAL_CORR.clear()
    for pair, v in cal_corr.items():
        a, b = pair.split("|")
        if a in gc_keys and b in gc_keys:
            GC_INTERNAL_CORR[(a, b)] = v

    # 5) 前瞻微调：aistockresearcher 生成的 forward_adjusted.json 若存在则覆盖大类 mu
    try:
        with open("/Users/sectator/MEGA/Finance/tmp/forward_adjusted.json", encoding="utf-8") as f:
            fwd = json.load(f)
        fwd_assets = fwd.get("assets", {})
        for a in BROAD_ASSETS:
            if a["key"] in fwd_assets and a["key"] != "O_US_CORE":
                a["mu"] = fwd_assets[a["key"]]["mu"]
        CALIBRATION_SUMMARY = {"loaded": True, "path": "calibrated + forward_adjusted",
                               "assets": len(cal_assets), "corr_pairs": len(cal_corr)}
    except Exception:
        pass

    CALIBRATION_LOADED = True
    CALIBRATION_SUMMARY = {
        "loaded": True,
        "path": CALIBRATION_PATH,
        "assets": len(cal_assets),
        "corr_pairs": len(cal_corr),
        "generated_at": cal.get("generated_at"),
    }


_apply_calibration()
