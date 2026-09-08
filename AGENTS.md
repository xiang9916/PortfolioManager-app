# AGENTS.md — PortfolioManager 开发须知

> 给在此仓库工作的 AI / 开发者。2026-09-08 汇总。

## 这是什么
原生 macOS (SwiftUI) 个人投资组合管理 App，替代「.numbers 手动维护 + 终端跑 Python 优化器」的工作流。
当前版本线：v0.2-beta2+（版本号仅存于 `scripts/Info.plist`）。开源协议 GPL-3.0，作者 xiang9916。

## 仓库边界（重要）
- 本仓库根 = `PortfolioManager-app/`（`../Finance/`、`../*.xlsx` 等在工作区里但**不是本仓库内容**，别提交）。
- git remote: `origin https://github.com/xiang9916/PortfolioManager-app.git`，分支 `main`。
- 无 CI workflow：DMG 本地构建后手动 `gh release create` 上传。
- `.gitignore` 已忽略：`.build/` `Optimizer/.venv/` `tmp/` `dist/` `__pycache__/` `*.pyc` `.DS_Store`。**不要把 venv/构建产物/含个人凭据的文件加进来。**

## 构建与测试
```bash
swift build -c debug        # 编译 Core + pm-cli + App 可执行
swift test                  # 跑 PortfolioCoreTests (BackupImportTests + QuarterlyMetricsTests)
# DSH 沙箱下 manifest 缓存无权限 → 用: swift build --disable-sandbox / swift test --disable-sandbox
```
- 纯系统依赖（SQLite3/Charts），无第三方 Swift 包；Package.swift 4 个 target。

## 打包发布（供参考）
```bash
bash scripts/build_app.sh --with-venv --dmg   # dist/PortfolioManager-<ver>.dmg, 必须带 venv 否则优化器无 Python
```
- 版本 bump：只改 `scripts/Info.plist`（CFBundleShortVersionString + CFBundleVersion 同步），commit `chore: bump version to X.Y`，打 annotated tag `vX.Y`（message `PortfolioManager X.Y`）。
- Release：Pre-release，标题 `PortfolioManager X.Y`，notes 中文 + 结尾 SHA256 行，附 DMG。
- venv 基础解释器 `/opt/miniconda3/bin/python3`（3.13.x）。

## 架构速览（详见图 docs/ARCHITECTURE.md）
```
PortfolioManager (SwiftUI)  ──依赖──▶  PortfolioCore (纯逻辑, 无UI)  ──▶  Optimizer/ (Python venv 子进程)
  ContentView: 3 Tab               ├ Models (Asset/Holding/Snapshot/QuarterlyReport…)
  AssetOverview/AssetPerspective/  ├ Database (SQLite + Schema v7 迁移)
  FinancialAnalysis 视图            ├ Query (Repository 聚合 + QuarterlyMetrics 公式链)
  AppStore = @MainActor 状态中枢    ├ DataSources (Yahoo/Eastmoney)
   持有 db / repository / optimizer ├ Backup (每日 .db 快照 + JSON 导入导出)
                                   ├ Optimizer (OptimizationService → PythonSidecar)
                                   └ Import/JSON (PortfolioImporter / ResultImporter)
pm-cli (headless CLI) 复用 PortfolioCore
```
红线：**PortfolioCore 永不 import PortfolioManager**；Views 只经 AppStore 取数。

## 数据模型要点（Schema v7）
- 市值 = 份额 × 最新价（**派生，不落库**）；`quotes` = 最新价，`prices` = 历史K线。
- 能力4 财务分析 = `quarterly_reports`（一列一季，9 个手动字段，其余行由 `QuarterlyMetrics.compute` 派生；`income_periods` 为旧版表仅兼容）。
- `assets.sort_order` 驱动资产透视手动排序；资产权重按人民币统一（fx_rates 折算）。

## 备份/恢复语义（2026-09-08 起）
- `BackupManager.exportJSON`: 导出 assets/holdings/snapshots/quarterly_reports/income_periods/fx_rates/quotes + schema_version。
- `importJSON(from:clearAssets:clearFinancials:)`: 默认**合并**(upsert)；`clearAssets`=先清 holdings/prices/quotes/snapshots/assets/fx_rates；`clearFinancials`=先清 quarterly_reports/income_periods。UI 导入前弹窗让用户勾选。pm-cli 对应 `--clear-assets` `--clear-financials`。
- 财务字段 UI 防抖 0.6s 落盘；**导出备份前记得 `flushQuarterlyPersist()`**（AppStore 已处理）。
- 已删除功能：PDF 导出（按钮/AppStore/PDFExporter/pm-cli report 全移除，勿复活）。

## 优化器 (能力2) 注意
- Python 解释器解析链：`PORTFOLIO_OPTIMIZER_PYTHON` 环境变量 → App bundle Resources `Optimizer/.venv` → 仓库 `Optimizer/.venv` → PATH。
- futu-api 仅声明于 requirements（备用），**当前无代码 import futu**；行情走 Yahoo/Eastmoney 公开源。安全：仓库不得出现任何 API key/个人凭据。
- 「新标的测试」= 临时加 ticker 重跑，不改持仓，结果在弹窗（testOptimization 状态）。

## 工作流习惯
- 中文 commit message，风格前缀：`feat(scope):` / `fix(scope):` / `chore:`。
- 改版前先跑全量测试；发布流程见上。
- 真实用户数据在 `~/Library/Application Support/PortfolioManager/portfolio.db`（勿用真实库做破坏性验证，先复制）。
