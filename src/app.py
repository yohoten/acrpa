"""
ACRPA 薄入口: 构建应用并启动主循环。

设计要点 (路线图 §3.1 / 阶段二第 1 项):
  - `import ACRPA` 必须零副作用 (不建窗 / 不 after / 不起线程 / 不 makedirs / 不 load_config)。
    ACRPA.py 已退化为纯定义模块, 真正的构建收敛在 `ACRPA.build_app()` 内。
  - 本模块是唯一的「构建 + 启动」入口:
        run.py (打包入口)  ->  app.main()
        测试 / 工具         ->  import app; rt = app.build(); rt.root...
  - 因此测试可以在不创建任何 Tk 窗口的前提下 `import ACRPA` 覆盖 engine/commands/scriptdata。

用法:
    # 启动完整应用 (等价于旧 `from ACRPA import root; root.mainloop()`)
    import app
    app.main()

    # 只为测试/内省构建一次 GUI, 拿到 root 后自行销毁
    import app
    rt = app.build()
    rt.root.after(500, rt.root.destroy)
    rt.root.mainloop()
"""
import os
import sys

# ── 保证 `src/` 与项目根都在 sys.path 中 (与 run.py 的策略保持一致) ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_HERE, "src")
if os.path.isdir(_SRC_DIR):
    # app.py 位于项目根
    _PROJECT_ROOT = _HERE
else:
    # app.py 位于 src/ 内 (当前布局)
    _SRC_DIR = _HERE
    _PROJECT_ROOT = os.path.dirname(_HERE)
for _p in (_SRC_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 纯定义模块: 此 import 不产生任何副作用
import ACRPA


def build():
    """构建应用: 创建 root、装配全部 GUI 控件与依赖。

    所有原先挂在 ACRPA 模块级的副作用 (建窗 / 路径 / 主题 / 绑定 / 定时器 /
    NetLink 启动等) 都收敛在 `ACRPA.build_app()` 中, 这里显式触发一次。

    返回 ACRPA 模块本身 (也可经模块级名 `app.ACRPA` 访问), 便于:
        rt = app.build(); rt.root.destroy()
    """
    ACRPA.build_app()
    return ACRPA


def main():
    """构建应用并进入 Tk 主循环 (run.py 打包入口调用)。"""
    build()
    ACRPA.root.mainloop()


if __name__ == "__main__":
    main()
