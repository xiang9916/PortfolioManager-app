# PortfolioManager 项目结构

> 2026-09-08 更新（v0.2-beta2 之后）。架构健康度评估见文末。

## 总览（3 层）

```
┌─────────────────────────────────────────────────────────────────────┐
│  PortfolioManager (SwiftUI App, executableTarget)                    │
│   UI 层: 3 个 Tab + 悬浮运行胶囊                                      │
│   资产总览 / 资产明细 / 财务分析 + 优化运行(右下角)                    │
└───────────────┬─────────────────────────────────────────────────────┘
                │ 依赖 PortfolioCore
┌───────────────▼─────────────────────────────────────────────────────┐
│  PortfolioCore (纯逻辑库, 无 UI 依赖, 可独立编译/测试)                 │
│                                                                       │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────────────────┐   │
│  │ Models     │  │ Database   │  │ Query                         │   │
│  │ Asset      │  │ SQLite 封装 │  │ Repository (读取聚合)          │   │
│  │ AssetMarket│  │ Schema v8  │  │ QuarterlyMetrics (财务公式链)   │   │
│  │ Holding    │  │ 版本迁移    │  │                               │   │
│  │ Snapshot…  │  │ 数据归一    │  │                               │   │
│  └────────────┘  └─────┬──────┘  └───────────────▲───────────────┘   │
│        ┌───────────────┴───────────┐            │                    │
│        ▼                           ▼            │                    │
│  ┌───────────────┐        ┌───────────────┐     │                    │
│  │ DataSources   │        │ Backup        │     │                    │
│  │ Yahoo / 天天   │        │ 每日快照 .db   │     │                    │
│  │ 基金(Eastmoney)│        │ JSON 导入/导出  │     │                    │
│  └───────┬───────┘        │ 恢复(可清空)    │     │                    │
│          │                └───────────────┘     │                    │
│          │                                      │                    │
│  ┌───────▼──────────────────────────────────────▼───────────────┐    │
│  │ Optimizer: OptimizationService ──▶ PythonSidecar (子进程)      │    │
│  └───────────────────────────┬──────────────────────────────────┘    │
└──────────────────────────────┼───────────────────────────────────────┘
                               │ 调用 vendored Python
┌──────────────────────────────▼───────────────────────────────────────┐
│  Optimizer/ (Python 脚本链, venv 解释器)                               │
│  scripts/: extract_portfolio / optimize_portfolio / market_data /     │
│            fund_pipeline / monte_carlo_sim / sensitivity_analysis …    │
│  data/: calibrated_params.json                                        │
└───────────────────────────────────────────────────────────────────────┘

pm-cli (CLI executableTarget) ──▶ 复用 PortfolioCore (headless 验证/调试)
```

## 数据流

### 主路径：.numbers → 优化 → 结果
```
Finance/portfolio.numbers
        │ extract_portfolio.py (子进程)
        ▼
tmp/extract_app.json ──▶ optimize_portfolio.py ──▶ JSON 结果
        │                                              │
        ▼                                              ▼
  PortfolioImporter ──▶ SQLite            OptimizationResult ──▶ UI 展示
  (assets/holdings)                        (app 内图表/表格)
```

### 行情自动抓取（能力1）
```
YahooFinanceSource / EastmoneySource ──▶ quotes(最新价) + prices(历史K线)
        │                                    │
        │                                    └─▶ dividends(逐笔每股/每份股息)
        ▼                                         + dividend_fetch_meta(抓取状态)
  市值 = 份额 × 最新价 (派生, 不落库)

全资产股息 = Σ max(0, 2×去年每股股息 − 前年每股股息) × 份额 × 汇率 × (1 − 税率)
全资产股息率 = 全资产股息 ÷ 总市值
```
- 抓取: Yahoo chart `events=div`(逐年窗口求和, 不用 range=max), 境内基金用
  天天基金 `fhsp_{code}.html`(每10份 ÷10, 只取现金分红)。
- 年份: 按**除息日**归入自然年; 窗口 = 2024-01-01 至今, 全量入库。
- 状态三态: `ok`(含“确认无分红”=0) / `unavailable`(数据源无覆盖) / `failed`(抓取失败);
  后两者详情显示「—」, 汇总按 0 计入, 覆盖率提示只统计它们。
- 市场: 闭集 `{US,HK,CN,JP,SG}`（`AssetMarket`），按 ticker 推断或手选；池由市场派生
  （CN → 境内，其余 → 境外；解析不出市场时按币种回退），**不再读 `assets.pool` 列**。
- 税率: 与池共用同一个市场结论; 美股 10% / 港股 28% / A股 0%, 其余 0%(标注未配置)。
- 刷新: 与「更新行情」同一按钮, 网络并发(限流 5) + 串行批量落库, 股息按标的 7 天缓存(跨年重抓)。

### 总市值 / 总成本（资产总览卡片）
```
总市值 = Σ 各持仓「份额 × 最后价 × 汇率」        (卡片主数字, 用于股息率分母与权重)
总成本 = Σ 各持仓「成本 × 汇率」                 (卡片第二个数字, 次要色)
境内/境外 = 按标的的「市场」派生池后分别求和     (市值与成本各一套)
```
- 两者同口径（都含全部持仓），所以卡片可以直接相减看浮盈浮亏；缺汇率的币种按 1:1 估算。
- 财务分析新增季度时，「总市值」「总成本」两格右侧的 ↓ 按钮一键填入**当前**值
  （期初基准列两格同填总成本）—— 填的是实时值，不是季末历史值，见 `docs/adr/0001`。
- 「成本」只指单持仓手填的累计投入；财务分析里的「本金」是派生行（= 总成本 − 累计已实现回报），
  两个词不同义，见 `CONTEXT.md`。

### 池归属（资产总览 / 资产明细 / 股息税率共用）
```
market（闭集 US/HK/CN/JP/SG）──▶ pool（境内/境外）──┬─▶ 资产总览 境内/境外 卡
   ▲                                              ├─▶ 优化器池权重
   │ 手选（新增标的）或按 ticker 推断               └─▶ 股息税率（US 10% / HK 28% / CN 0%）
   └─ 解析不出 → 按币种回退（CNY → 境内）
```
- `assets.pool` 列只是写入时的缓存：**读路径一律由市场派生**，结构上保证池与税率不会分叉。
- 旧的第三态 `Pool.cross`（跨池）已废除，理由与迁移规则见 `docs/adr/0002`。

### 备份/恢复（能力3）
```
exportJSON ──▶ 可移植 JSON (assets/holdings/snapshots/quarterly_reports/
                              income_periods/fx_rates/quotes/dividends/
                              dividend_fetch_meta + schema_version)
importJSON ──▶ 可选 clearAssets / clearFinancials (先清空再导入 = 真正恢复)
每日快照 ──▶ backups/portfolio-<ts>.db (WAL checkpoint 后整库复制)
```

## 依赖方向规则（防屎山红线）
- PortfolioCore **不得** import PortfolioManager（已用 grep 验证 ✅）
- Views 只通过 `AppStore`（@MainActor @Observable 状态中枢）访问数据，不直接碰 Database
- AppStore 持有 `db / repository / optimizer` 三个 Core 对象，UI 订阅其 observable 属性
- Python 只通过 `PythonSidecar`（Process 子进程）单向调用，无反向 IPC

## 关键表（Schema v8）
| 域 | 表 |
|---|---|
| 资产 | assets / holdings / quotes(最新价) / prices(历史) |
| 股息 | dividends(逐笔每股/每份) / dividend_fetch_meta(抓取状态+缓存时间) |
| 历史 | snapshots(市值快照) |
| 财务 | quarterly_reports(逐季度底稿) / income_periods(旧版, 兼容) |
| 汇率 | fx_rates / macro_rates |
| 系统 | schema_meta / optimization_runs / optimization_logs / backups |

## 代码规模
```
总 6438 行 Swift
AppStore 824    (状态中枢, 最大文件)
Database  637   (SQLite 封装)
FinancialAnalysisView 418 (网格 UI, 公式渲染)
Repository 391  (读取聚合)
Tests: 2 文件 / 20 用例
```

## 架构健康度评估（2026-09-08）
| 指标 | 结论 |
|---|---|
| 分层 | ✅ Core 无 UI 依赖；UI/CLI 单向依赖 Core |
| 模块内聚 | ✅ 按目录: Database/Query/DataSources/Backup/Optimizer/Models 职责单一 |
| 依赖面 | ✅ 系统库 SQLite3 + Charts，无第三方 Swift 包 |
| 测试 | ✅ 20 项覆盖备份 roundtrip/财务公式链/DB 迁移 |
| 改进项 | ⚠️ AppStore(824行) 可考虑拆出季度/备份子状态；FinancialAnalysisView 的静态行定义可抽模板 |
