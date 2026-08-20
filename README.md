# PortfolioManager

原生 macOS（SwiftUI）个人投资组合管理应用，把 Finance 工作软件化。

## 模块与能力
- 模块1 资产管理：大类资产分布、历史财务、图表、PDF 导出
- 模块2 资产透视：每个标的状态
- 能力1 自动抓数 / 能力2 优化集成 / 能力3 数据安全 / 能力4 财务报表

## 结构
- Sources/PortfolioCore —— 纯逻辑库（模型 / SQLite / 数据源 / 备份 / PDF / 优化器 sidecar）
- Sources/PortfolioManager —— SwiftUI UI 层
- Sources/pm-cli —— 无 UI 的 CLI（可 headless 验证核心逻辑）
- Optimizer/scripts —— vendored 的 portfolio-optimizer Python 脚本链

## 构建
\`\`\`bash
swift build          # 编译核心库 + CLI（CommandLineTools 即可）
swift run pm-cli ../Finance/portfolio_result.json   # 冒烟测试
swift test           # 单元测试
\`\`\`

打包 .app 需完整 Xcode（xcodebuild），见 docs/specs/。
