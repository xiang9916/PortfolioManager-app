# PortfolioManager 项目结构

> 2026-09-08 更新（v0.2-beta2 之后）。架构健康度评估见文末。

## 总览（3 层）

```
┌─────────────────────────────────────────────────────────────────────┐
│  PortfolioManager (SwiftUI App, executableTarget)                    │
│   UI 层: 3 个 Tab + 悬浮运行胶囊                                      │
│   资产管理 / 资产透视 / 财务分析 + 优化运行(右下角)                    │
└───────────────┬─────────────────────────────────────────────────────┘
                │ 依赖 PortfolioCore
┌───────────────▼─────────────────────────────────────────────────────┐
│  PortfolioCore (纯逻辑库, 无 UI 依赖, 可独立编译/测试)                 │
│                                                                       │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────────────────┐   │
│  │ Models     │  │ Database   │  │ Query                         │   │
│  │ Asset      │  │ SQLite 封装 │  │ Repository (读取聚合)          │   │
│  │ Holding    │  │ Schema v7  │  │ QuarterlyMetrics (财务公式链)   │   │
│  │ Snapshot…  │  │ 版本迁移    │  │                               │   │
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
Finance/投资组合情况.numbers
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
        │
        ▼
  市值 = 份额 × 最新价 (派生, 不落库)
```

### 备份/恢复（能力3）
```
exportJSON ──▶ 可移植 JSON (assets/holdings/snapshots/quarterly_reports/
                              income_periods/fx_rates/quotes + schema_version)
importJSON ──▶ 可选 clearAssets / clearFinancials (先清空再导入 = 真正恢复)
每日快照 ──▶ backups/portfolio-<ts>.db (WAL checkpoint 后整库复制)
```

## 依赖方向规则（防屎山红线）
- PortfolioCore **不得** import PortfolioManager（已用 grep 验证 ✅）
- Views 只通过 `AppStore`（@MainActor @Observable 状态中枢）访问数据，不直接碰 Database
- AppStore 持有 `db / repository / optimizer` 三个 Core 对象，UI 订阅其 observable 属性
- Python 只通过 `PythonSidecar`（Process 子进程）单向调用，无反向 IPC

## 关键表（Schema v7）
| 域 | 表 |
|---|---|
| 资产 | assets / holdings / quotes(最新价) / prices(历史) |
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
