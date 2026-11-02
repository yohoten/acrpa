# -*- coding: utf-8 -*-
"""core — 领域层可复用纯 Python 基础件 (无 tkinter 依赖)。

按 docs/项目结构优化建议.md §3.1 从 ``src/utils.py`` 拆出, 目标:
让 ``engine`` / ``workflow`` 等非 GUI 模块在无 tkinter 的解释器里也能导入,
解锁 CLI / 无头模式 (§6.5)。

当前成员:
  * core.log     —— 日志总线 (log1 / ThreadSafeLog / LOG_* 级别常量)
  * core.util    —— 通用纯函数 (_safe_get)
  * core.fs      —— 原子写盘与安全文件名 (atomic_save / safe_filename)
  * core.version —— 版本号解析 / 比较 (parse_version / compare_versions / version_tuple / version_gt)

约定: 本包内任何模块不得 import tkinter / tkinter.font。
"""
