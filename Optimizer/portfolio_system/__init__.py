"""portfolio_system：优化器的可分层包（ADR 0003）。

依赖方向：cli -> callers -> core -> models；callers -> services -> models。
core/models 是纯计算层：禁网络 / 禁磁盘 / 禁子进程 / 禁模块级可变全局。
本阶段 P0 只落骨架：core 以**委托**方式调用既有 optimize_common / optimize_portfolio，
既有脚本与 CLI 契约零改动。
"""
