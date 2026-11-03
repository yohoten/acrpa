# -*- coding: utf-8 -*-
"""探针: 主窗口空闲唤醒频率 (v5 §2.3 验收指标 38.17 → ≤18 次/秒)。

做法
----
1. `import app; app.build()` 构建**真实**主窗口 (与 run.py 同一条路径);
2. 窗口 withdraw (不计重绘), 把 `root.after` 包一层计数 (只数**新排期**的唤醒);
3. 先空转 WARMUP 秒让各类退避/自泊收敛, 再统计 SECONDS 秒;
4. 输出「次/秒」与 Top-N 贡献者, 供与文档基线 38.17 对比。

用法
----
    set PYTHONUTF8=1
    python -u tools/_out/_probe_wakeup.py > tools/_out/_probe_wakeup.txt 2>&1

    set PROBE_SECONDS=6 & set PROBE_WARMUP=3    # 可选覆盖
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SECONDS = float(os.environ.get("PROBE_SECONDS") or 6.0)
WARMUP = float(os.environ.get("PROBE_WARMUP") or 3.0)
BASELINE = 38.17          # v5 §2.3 实测基线 (改造前)

import app  # noqa: E402

rt = app.build()
root = rt.root
try:
    root.withdraw()
except Exception:
    pass

_stats = {"counting": False, "count": 0, "by": {}}
_orig_after = root.after


def _wrapped(ms=0, fn=None):
    if fn is None:
        fn, ms = ms, 0
    if _stats["counting"]:
        _stats["count"] += 1
        _key = getattr(fn, "__name__", "?")
        _stats["by"][_key] = _stats["by"].get(_key, 0) + 1
    return _orig_after(ms, fn)


try:
    root.after = _wrapped          # 实例属性遮蔽: 只影响本进程内的排期调用
except Exception:
    pass


def _begin():
    _stats["counting"] = True


def _end():
    _stats["counting"] = False
    try:
        root.quit()
    except Exception:
        pass


root.after(int(WARMUP * 1000), _begin)
root.after(int((WARMUP + SECONDS) * 1000), _end)
print("构建完成, 空转 {:.0f}s 预热 + {:.0f}s 计时 ...".format(WARMUP, SECONDS))
root.mainloop()

_rate = _stats["count"] / SECONDS if SECONDS > 0 else 0.0
print("")
print("=" * 62)
print("空闲唤醒: {} 次 / {:.1f}s = {:.2f} 次/秒".format(
    _stats["count"], SECONDS, _rate))
print("改造前基线: {:.2f} 次/秒".format(BASELINE))
print("目标: <= 18 次/秒  →  {}".format("达标" if _rate <= 18.0 else "未达标"))
print("-" * 62)
print("Top 贡献者 (回调名 → 次数):")
for _k, _v in sorted(_stats["by"].items(), key=lambda kv: -kv[1])[:10]:
    print("  {:32s} {:5d}  ({:.2f} 次/秒)".format(_k, _v, _v / SECONDS))
print("=" * 62)

try:
    root.destroy()
except Exception:
    pass
sys.exit(0 if _rate <= 18.0 else 2)
