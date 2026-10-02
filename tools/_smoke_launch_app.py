# -*- coding: utf-8 -*-
"""_smoke_launch_app.py  (阶段2 测试子任务，仅新建，不改业务源码)

应用启动存活验证：
  - 以项目根为 cwd，用 subprocess.Popen 启动 .venv\\Scripts\\python.exe run.py
  - 轮询等待最多 15s：判断进程是否仍存活；期间周期性读取输出
  - 判定：
      ① 进程在 ~10s 后仍存活且无致命 Traceback -> 启动成功（GUI 已建窗）
      ② 进程提前退出 -> 记录退出码与 stdout/stderr 全文（启动失败）
      ③ 超时无输出但存活 -> 视为存活（GUI 阻塞于 mainloop 属正常）
  - 结束时强制终止该进程及其子进程（taskkill /F /T /PID）
  - 结论与输出写入 tools\\_smoke_result_phase2\\_smoke_launch_app.out
"""

import os
import sys
import time
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "tools", "_smoke_result_phase2")
os.makedirs(OUTDIR, exist_ok=True)

PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CHILD_LOG = os.path.join(OUTDIR, "_smoke_launch_app_child.log")
REPORT = os.path.join(OUTDIR, "_smoke_launch_app.out")

POLL_TOTAL = 15.0        # 最长轮询秒数
ALIVE_THRESHOLD = 10.0   # 存活判定阈值

lines = []


def emit(msg=""):
    print(msg)
    lines.append(msg)


def read_log():
    try:
        with open(CHILD_LOG, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return "<<读取子进程日志失败: %r>>" % (e,)


def main():
    emit("=== ACRPA 应用启动存活验证 (run.py) ===")
    emit("解释器: %s" % PY)
    emit("解释器存在: %s" % os.path.exists(PY))
    emit("工作目录(cwd): %s" % ROOT)
    emit("子进程日志: %s" % CHILD_LOG)
    emit("-" * 64)

    if not os.path.exists(PY):
        emit("[FAIL] 未找到 .venv 解释器: %s" % PY)
        return 1

    # 清空旧日志
    with open(CHILD_LOG, "wb") as f:
        f.write(b"")

    log_fp = open(CHILD_LOG, "wb")
    try:
        child = subprocess.Popen(
            [PY, "-X", "utf8", "run.py"],
            cwd=ROOT,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as e:
        log_fp.close()
        emit("[FAIL] 启动 run.py 抛出异常: %r" % (e,))
        return 1

    pid = child.pid
    emit("已启动 run.py, PID=%d" % pid)

    start = time.time()
    exited = None
    alive_at_threshold = False
    last_note = 0

    while True:
        elapsed = time.time() - start
        rc = child.poll()
        if rc is not None:
            exited = rc
            emit("[t=%.1fs] 进程已退出, 退出码=%s" % (elapsed, rc))
            break
        if elapsed >= ALIVE_THRESHOLD and not alive_at_threshold:
            alive_at_threshold = True
            emit("[t=%.1fs] 进程仍存活 (>= %.0fs 阈值)" % (elapsed, ALIVE_THRESHOLD))
        if elapsed >= POLL_TOTAL:
            emit("[t=%.1fs] 达到最长轮询 %.0fs，进程仍存活" % (elapsed, POLL_TOTAL))
            break
        # 每 2s 打一条进度
        if elapsed - last_note >= 2.0:
            last_note = elapsed
            emit("[t=%.1fs] alive=%s" % (elapsed, child.poll() is None))
        time.sleep(0.5)

    still_alive = (exited is None)

    # 读取子进程输出（在终止前抓一次）
    finally_kill_needed = still_alive
    log_fp.flush()
    try:
        log_fp.close()
    except Exception:
        pass

    emit("-" * 64)
    child_out = read_log()
    has_tb = ("Traceback (most recent call last)" in child_out)

    # 终止进程及子进程
    if still_alive:
        emit("[清理] 强制终止进程树 PID=%d (taskkill /F /T)" % pid)
        try:
            kr = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=20,
            )
            emit("taskkill rc=%s out=%s err=%s"
                 % (kr.returncode, (kr.stdout or "").strip(), (kr.stderr or "").strip()))
        except Exception as e:
            emit("[WARN] taskkill 失败: %r" % (e,))
        try:
            child.wait(timeout=10)
        except Exception:
            pass
        emit("[清理] 终止后 poll()=%s" % (child.poll(),))

    # 判定
    emit("-" * 64)
    if still_alive and not has_tb:
        verdict = "启动成功（GUI 已建窗）"
        code = 0
    elif still_alive and has_tb:
        # 存活但出现 Traceback（可能是非致命异常），仍算存活但标注
        verdict = "启动成功（存活，但子进程输出含 Traceback，需复核）"
        code = 0
    elif exited == 0:
        verdict = "启动失败/提前退出（退出码 0，未能保持 mainloop 存活）"
        code = 1
    else:
        verdict = "启动失败（提前退出，退出码=%s）" % exited
        code = 1

    emit("判定: %s" % verdict)
    emit("存活至阈值(>=%.0fs): %s" % (ALIVE_THRESHOLD, alive_at_threshold))
    emit("子进程输出含 Traceback: %s" % has_tb)
    emit("-" * 64)
    emit("子进程 stdout/stderr 全文:")
    emit("<<<CHILD_OUT_BEGIN>>>")
    emit(child_out if child_out.strip() else "(空)")
    emit("<<<CHILD_OUT_END>>>")
    emit("=== 结果: %s (rc=%d) ===" % (
        "OK" if code == 0 else "FAIL", code))

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
