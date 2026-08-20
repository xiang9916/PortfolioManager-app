# PortfolioManager 设计文档

> 2026-08-20 | 把 Finance 工作软件化为原生 macOS App

## 1. 目标与范围

一个个人 macOS 应用，替代当前「.numbers 手动维护 + 终端跑 Python 优化器」的工作流。

- **模块1 资产管理**：大类资产分布、组合历史财务情况、可视化图表。
- **模块2 资产透视**：每个标的的状态（数据更新的基础来源）。
- **能力1 自动抓数**：公开数据自动抓取更新。
- **能力2 优化集成**：完整集成投资组合优化；右下角「运行/设置」胶囊按钮 → 运行中变进度条；中间过程写结构化日志供 AI 读取。
- **能力3 数据安全**：PDF 导出、数据导入导出、每日自动备份可回滚、版本兼容。
- **能力4 财务报表**：季度/半年/年度财务数据对比展示。

## 2. 架构

分两层：
1. **PortfolioCore**（纯逻辑库，SPM 可独立编译/测试，无 UI 依赖）：
   - Models：Asset / Holding / Snapshot / Price / Financial / OptimizationRun
   - Database：GRDB + schema 版本化迁移
   - DataSources：Yahoo / 天天基金 / 汇富 / Morningstar / 富途 adapter
   - Backup：每日快照 + 回滚 + 导入导出
   - PDF：PDF 导出
   - Optimizer：Python sidecar 运行器 + 日志解析器
2. **PortfolioManager**（SwiftUI UI 层）：
   - AssetManagementView（模块1）、AssetPerspectiveView（模块2）、OptimizePillButton（能力2）

## 3. 数据库 schema（SQLite）

- assets(id, key, name, ticker, market, asset_class, pool[domestic/overseas/cross], currency, source, fee_rate)
- holdings(id, asset_id, quantity, cost_basis, value_cny, as_of_date)
- snapshots(id, date, total_value, domestic_value, overseas_value)  ← 历史财务/图表
- prices(id, asset_id, date, close, currency)  ← 净值/K线时间序列
- financials(id, asset_id, period[Q/H/Y], period_end, revenue, eps, ...)  ← 能力4
- optimization_runs(id, started_at, finished_at, status, params_hash, result_json, log_path)
- optimization_logs(run_id, seq, step, message, level, ts)  ← 能力2 日志
- backups(id, created_at, path, schema_version)
- schema_meta(version)  ← 能力3 版本兼容

## 4. 优化器集成（能力2）

- 把 ~/.dsh/skills/portfolio-optimizer/scripts/ 下 8 个 .py vendored 到 Optimizer/scripts/。
- App 内嵌一个 Python runtime（或依赖系统 futu_venv），作为子进程跑 optimize_portfolio.py。
- 子进程按 step 输出结构化 JSON（step_id / message / progress），App 解析后驱动进度条。
- 每次运行写 optimization_runs + optimization_logs，并落 JSONL 到固定路径供 AI 读取。

## 5. 数据安全（能力3）

- 每日定时：SQLite 备份 + 数据导出 JSON 归档（带 schema_version）。
- 回滚：从 backups 恢复到任意日期。
- 导入导出：CSV/JSON。
- 迁移：schema_meta.version 驱动，旧数据自动迁移不破坏。

## 6. 阶段（见 task_plan.md）
