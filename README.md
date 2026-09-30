# PortfolioManager

原生 macOS（SwiftUI）个人投资组合管理应用，把 Finance 工作软件化。

## 模块与能力
- 模块1 资产总览：总市值 / 总成本（境内·境外各一套）、大类资产分布、历史财务、图表
- 模块2 资产明细：每个标的状态（含市场 / 池 / 成本 / 股息明细）
- 能力1 自动抓数 / 能力2 优化集成 / 能力3 数据安全 / 能力4 财务分析

> 领域术语表见 [CONTEXT.md](CONTEXT.md)，架构决策见 [docs/adr/](docs/adr/)。

## 结构
- Sources/PortfolioCore —— 纯逻辑库（模型 / SQLite / 数据源 / 备份 / 优化器 sidecar）
- Sources/PortfolioManager —— SwiftUI UI 层
- Sources/pm-cli —— 无 UI 的 CLI（可 headless 验证核心逻辑）
- Optimizer/scripts —— vendored 的 portfolio-optimizer Python 脚本链
- scripts/ —— Info.plist、图标生成、打包脚本

## 构建
```bash
swift build                    # 编译核心库 + CLI + SwiftUI 可执行
swift run pm-cli --self-test   # 自测
swift build -c release         # release 构建
bash scripts/build_dev.sh      # 预览用：debug + 真实 SDK 版本戳（见下）
```

> **预览一定要用 `scripts/build_dev.sh`，不要直接 `swift build`。** SwiftPM 会把
> deployment target 当成 SDK 版本写进 `LC_BUILD_VERSION`（实测 `minos 14.0 / sdk 14.0`），
> macOS 据此判定「用旧 SDK 构建」并启用兼容外观（TabView 标签栏落到标题下方、窗口标题常显），
> 于是本地看到的界面和用户装到的不是一回事，截图核对也就失去意义。该脚本与
> `scripts/build_app.sh` 用**一字不差**的 `-platform_version macos 14.0 <当前 SDK>`，
> 并在构建后打印实际写进二进制的 `minos / sdk` 供核对。

## 打包 .app（Phase 8）
```bash
bash scripts/build_app.sh              # 组装 dist/PortfolioManager.app + ad-hoc 签名
bash scripts/build_app.sh --with-venv  # 额外打包 Python venv（优化器 sidecar）
```

产物 `dist/PortfolioManager.app`（arm64，最低 macOS 14）。首次运行数据落在
`~/Library/Application Support/PortfolioManager/`。正式分发需 Developer ID 签名 + 公证
（`notarytool`），见 docs/specs/。

## CLI 命令（pm-cli）
```
pm-cli extract [portfolio.numbers]                 # 从 .numbers 提取
pm-cli import [db] [extract.json]                     # 导入持仓到 SQLite
pm-cli refresh [db] [keys...]                         # 抓行情落库
pm-cli optimize [extract.json] [--total-assets N]     # 运行优化器
pm-cli optimize [extract.json] --test-tickers NFLX,0700.HK  # 新标的测试: 临时加入标的重跑优化 (μ=100%历史年化)
pm-cli overview [db]                                  # 总市值/总成本 + 资产分布/历史表现
pm-cli financials [db]                                # 财务分析
pm-cli backup|list|restore|export|import-json|export-csv|daily-backup  # 数据安全
```

## License（GPL-3.0）

Copyright (C) 2025 xiang9916

本程序为自由软件：你可以按照 Free Software Foundation 发布的
**GNU General Public License 第 3 版**的条款将其再分发和/或修改。
本程序不附带任何担保，适用条款见许可证第 15、16 条。

许可证全文见仓库根目录 [LICENSE](LICENSE)（官方版本：<https://www.gnu.org/licenses/gpl-3.0.txt>）。

打包产物中随附的第三方组件（例如 `Optimizer/.venv` 内的 Python 库）
归其原作者所有，仍适用各自的原始开源许可证。
