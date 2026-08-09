"""Scheduled execution — time calculation and background scheduler loop.
   v2:  计划任务模块，支持多任务管理与轮询间隔。"""
import time, threading, datetime, os, json
import state
from utils import log1


# GUI references injected by ACRPA.py
_root = None
_sched_enabled_var = None
_sched_next_label = None
_sched_task_tree = None  # 多任务 Treeview 引用
_stop_event = threading.Event()
_last_start_time = 0
_MIN_RESTART_INTERVAL = 2.0

# 多任务运行时状态
_task_threads = {}      # {task_id: thread}
_task_stop_flags = {}   # {task_id: threading.Event}
_task_next_runs = {}    # {task_id: "YYYY-MM-DD HH:MM"}
_task_last_runs = {}    # {task_id: "YYYY-MM-DD HH:MM:SS"}
_task_logs = []         # 执行日志
_task_lock = threading.Lock()


def set_gui_refs(root, enabled_var, next_label, task_tree=None):
    global _root, _sched_enabled_var, _sched_next_label, _sched_task_tree
    _root = root
    _sched_enabled_var = enabled_var
    _sched_next_label = next_label
    _sched_task_tree = task_tree


def calc_next_run():
    """Compute the next scheduled run time and update state.SCHED_NEXT_RUN."""
    now = datetime.datetime.now()
    today = now.replace(hour=state.SCHED_HOUR, minute=state.SCHED_MINUTE, second=0, microsecond=0)

    if state.SCHED_REPEAT_MODE == "daily":
        candidate = today if today > now else today + datetime.timedelta(days=1)

    elif state.SCHED_REPEAT_MODE == "weekly":
        if today > now and state.SCHED_WEEKDAYS[now.weekday()]:
            candidate = today
        else:
            offset = 1
            while offset <= 7:
                idx = (now.weekday() + offset) % 7
                if state.SCHED_WEEKDAYS[idx]:
                    break
                offset += 1
            else:
                offset = 1
            candidate = today + datetime.timedelta(days=offset)
    else:  # "once"
        candidate = today if today > now else today + datetime.timedelta(days=1)

    state.SCHED_NEXT_RUN = candidate.strftime("%Y-%m-%d %H:%M")
    return candidate


def scheduler_loop(main_run_fn, save_config_fn):
    """Background thread: check time every 30 s and trigger execution."""
    log1("定时调度线程已启动")

    while state.SCHED_ENABLED and not state._closing and not state.quit2:
        try:
            now = datetime.datetime.now()
            next_dt = None
            if state.SCHED_NEXT_RUN:
                try:
                    next_dt = datetime.datetime.strptime(state.SCHED_NEXT_RUN, "%Y-%m-%d %H:%M")
                except ValueError:
                    log1("定时调度: 下次运行时间格式异常，重新计算", "warning")
                    calc_next_run()
                    continue

            if next_dt and now >= next_dt:
                if state.running:
                    log1("定时执行触发时脚本正在运行，跳过本次执行", "warning")
                else:
                    log1("定时执行触发！准备运行脚本", "info")
                    if _root: _root.after(0, main_run_fn)

                if state.SCHED_REPEAT_MODE == "once":
                    state.SCHED_ENABLED = False
                    if _sched_enabled_var and _root:
                        _root.after(0, lambda: _sched_enabled_var.set(False))
                    save_config_fn()
                    calc_next_run()
                    break
                else:
                    calc_next_run()

            for _ in range(30):
                if _stop_event.is_set() or not state.SCHED_ENABLED or state._closing or state.quit2:
                    break
                time.sleep(1)
        except Exception as e:
            log1("定时调度异常: {}".format(e), "error")
            time.sleep(30)

    state._sched_thread_active = False
    _stop_event.clear()
    log1("定时调度线程已停止")


def start_scheduler(main_run_fn, save_config_fn):
    """Start scheduler thread if enabled and not already running."""
    global _last_start_time
    current_time = time.time()
    if state._sched_thread_active or not state.SCHED_ENABLED:
        return
    if current_time - _last_start_time < _MIN_RESTART_INTERVAL:
        return

    calc_next_run()
    state._sched_thread_active = True
    _last_start_time = current_time
    t = threading.Thread(target=scheduler_loop, args=(main_run_fn, save_config_fn), daemon=True)
    t.start()


def stop_scheduler():
    """Signal scheduler to stop immediately (all tasks)."""
    state.SCHED_ENABLED = False
    _stop_event.set()
    with _task_lock:
        for tid, evt in list(_task_stop_flags.items()):
            evt.set()
        _task_threads.clear()
        _task_stop_flags.clear()


# ======================================================================
# 多任务管理 ( 计划任务模块)
# ======================================================================

def add_task(name, script_path, period_type, period_value, enabled=True, time_slots=None):
    """添加计划任务。
    
    Args:
        name: 任务名称
        script_path: 脚本路径
        period_type: 周期类型 (once/daily/weekly/interval)
        period_value: 周期值
        enabled: 是否启用
        time_slots: 时间段过滤列表，如 [("09:00","12:00"),("14:00","18:00")]
                   空/None 表示不限制时间段
    """
    task = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "script": script_path,
        "period_type": period_type,
        "period_value": period_value,
        "enabled": enabled,
        "time_slots": time_slots or [],  # 时间段过滤
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.SCHED_TASKS.append(task)
    _calc_task_next_run(task)
    _save_tasks()
    return task


def remove_task(task_id):
    """删除计划任务"""
    with _task_lock:
        if task_id in _task_stop_flags:
            _task_stop_flags[task_id].set()
            _task_stop_flags.pop(task_id, None)
            _task_threads.pop(task_id, None)
    state.SCHED_TASKS = [t for t in state.SCHED_TASKS if t["id"] != task_id]
    _task_next_runs.pop(task_id, None)
    _save_tasks()


def toggle_task(task_id, enabled):
    """启用/禁用计划任务"""
    for t in state.SCHED_TASKS:
        if t["id"] == task_id:
            t["enabled"] = enabled
            if enabled:
                _calc_task_next_run(t)
            else:
                _task_next_runs.pop(task_id, None)
                with _task_lock:
                    if task_id in _task_stop_flags:
                        _task_stop_flags[task_id].set()
                        _task_stop_flags.pop(task_id, None)
                        _task_threads.pop(task_id, None)
            break
    _save_tasks()


def _calc_task_next_run(task):
    """计算任务的 next_run"""
    now = datetime.datetime.now()
    tid = task["id"]
    ptype = task.get("period_type", "daily")
    pval = task.get("period_value", "09:00")

    if ptype == "once":
        try:
            dt = datetime.datetime.strptime(pval, "%Y-%m-%d %H:%M")
        except ValueError:
            dt = now + datetime.timedelta(minutes=1)
        _task_next_runs[tid] = dt.strftime("%Y-%m-%d %H:%M")

    elif ptype == "interval":
        try:
            secs = int(pval)
        except (ValueError, TypeError):
            secs = 3600
        dt = now + datetime.timedelta(seconds=secs)
        _task_next_runs[tid] = dt.strftime("%Y-%m-%d %H:%M")

    elif ptype == "daily":
        try:
            h, m = map(int, pval.split(":"))
        except Exception:
            h, m = 9, 0
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        _task_next_runs[tid] = candidate.strftime("%Y-%m-%d %H:%M")

    elif ptype == "weekly":
        try:
            parts = pval.split("|")
            days = [int(d) for d in parts[0].split(",")]
            h, m = map(int, parts[1].split(":"))
        except Exception:
            days = [0]
            h, m = 9, 0
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        for _ in range(7):
            if candidate.weekday() in days and candidate > now:
                break
            candidate += datetime.timedelta(days=1)
        else:
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0) + datetime.timedelta(days=1)
            while candidate.weekday() not in days:
                candidate += datetime.timedelta(days=1)
        _task_next_runs[tid] = candidate.strftime("%Y-%m-%d %H:%M")

    else:
        _task_next_runs[tid] = (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")


def _save_tasks():
    """持久化计划任务到 config.json"""
    try:
        state.save_config()
    except Exception:
        pass


def get_task_logs(limit=200):
    """获取执行日志"""
    with _task_lock:
        return list(_task_logs[-limit:])


def clear_task_logs():
    """清空执行日志"""
    with _task_lock:
        _task_logs.clear()


def _add_task_log(task_name, script_name, status, trigger="定时", log_type="info", folder=""):
    """添加一条执行日志"""
    entry = {
        "task_name": task_name,
        "script_name": script_name,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "trigger": trigger,
        "log_type": log_type,
        "folder": folder,
    }
    with _task_lock:
        _task_logs.append(entry)
        if len(_task_logs) > 500:
            _task_logs[:] = _task_logs[-500:]


def run_task_now(task_id, main_run_fn):
    """立即执行指定任务"""
    for t in state.SCHED_TASKS:
        if t["id"] == task_id and t.get("enabled", True):
            script = t.get("script", "")
            name = t.get("name", "")
            if script and os.path.exists(script):
                _add_task_log(name, os.path.basename(script), "运行中", "手动")
                state.filename = script
                state.has_script = True
                state.script_dir = os.path.dirname(script)
                if _root:
                    _root.after(0, main_run_fn)
            else:
                log1("计划任务脚本不存在: {}".format(script), "warning")
            break


def _is_in_time_slots(now, time_slots):
    """检查当前时间是否在任一时间段内。
    
    Args:
        now: datetime.datetime 当前时间
        time_slots: [(start_str, end_str), ...] 如 [("09:00","12:00"),("14:00","18:00")]
    
    Returns:
        True 如果在任一时间段内（或无时间段限制）
    """
    if not time_slots:
        return True  # 无时间段限制，始终允许
    
    current_minutes = now.hour * 60 + now.minute
    for start_str, end_str in time_slots:
        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            start_m = sh * 60 + sm
            end_m = eh * 60 + em
            if start_m <= current_minutes < end_m:
                return True
        except Exception:
            continue
    return False


def multi_task_loop(main_run_fn):
    """后台线程：轮询多任务调度（支持时间段过滤）"""
    log1("多任务调度线程已启动 (轮询间隔: {}s)".format(state.SCHED_POLL_INTERVAL))

    while not state._closing and not state.quit2:
        try:
            now = datetime.datetime.now()
            for task in list(state.SCHED_TASKS):
                if not task.get("enabled", True):
                    continue
                
                # ── 时间段过滤: 不在允许时段内则跳过 ──
                time_slots = task.get("time_slots", [])
                if not _is_in_time_slots(now, time_slots):
                    continue
                
                tid = task["id"]
                nr_str = _task_next_runs.get(tid)
                if not nr_str:
                    _calc_task_next_run(task)
                    nr_str = _task_next_runs.get(tid)
                if nr_str:
                    try:
                        nr_dt = datetime.datetime.strptime(nr_str, "%Y-%m-%d %H:%M")
                        if now >= nr_dt:
                            if not state.running:
                                script = task.get("script", "")
                                name = task.get("name", tid)
                                if script and os.path.exists(script):
                                    log1("定时任务触发: {}".format(name))
                                    _add_task_log(name, os.path.basename(script), "运行中", "定时")
                                    _task_last_runs[tid] = now.strftime("%Y-%m-%d %H:%M:%S")
                                    state.filename = script
                                    state.has_script = True
                                    state.script_dir = os.path.dirname(script)
                                    if _root:
                                        _root.after(0, main_run_fn)
                                else:
                                    log1("计划任务脚本不存在: {}".format(script), "warning")
                                    _add_task_log(name, os.path.basename(script) if script else "N/A",
                                                  "失败-脚本不存在", "定时", "error")
                            else:
                                log1("定时任务触发时脚本正在运行，跳过: {}".format(
                                    task.get("name", tid)), "warning")

                            if task.get("period_type") == "once":
                                task["enabled"] = False
                                _task_next_runs.pop(tid, None)
                            else:
                                _calc_task_next_run(task)
                            _save_tasks()
                    except ValueError:
                        _calc_task_next_run(task)

            if _root and _sched_task_tree:
                try:
                    _root.after(0, _refresh_task_tree)
                except Exception:
                    pass

            for _ in range(state.SCHED_POLL_INTERVAL):
                if _stop_event.is_set() or state._closing or state.quit2:
                    break
                time.sleep(1)

        except Exception as e:
            log1("多任务调度异常: {}".format(e), "error")
            time.sleep(state.SCHED_POLL_INTERVAL)

    state._sched_thread_active = False
    _stop_event.clear()
    log1("多任务调度线程已停止")


def _refresh_task_tree():
    """刷新计划任务 Treeview"""
    if not _sched_task_tree:
        return
    try:
        for item in _sched_task_tree.get_children():
            _sched_task_tree.delete(item)
        for task in state.SCHED_TASKS:
            tid = task["id"]
            nr = _task_next_runs.get(tid, "--")
            lr = _task_last_runs.get(tid, "--")
            enabled = "✓" if task.get("enabled", True) else "✗"
            # 时间段显示
            slots = task.get("time_slots", [])
            slots_str = ", ".join("{}-{}".format(s, e) for s, e in slots) if slots else "全天"
            values = (
                task.get("name", tid),
                os.path.basename(task.get("script", "")),
                task.get("period_type", "daily"),
                slots_str,
                nr,
                lr,
                enabled,
            )
            tag = "enabled" if task.get("enabled", True) else "disabled"
            _sched_task_tree.insert("", "end", values=values, iid=tid, tags=(tag,))
            _sched_task_tree.tag_configure("enabled", foreground="#10B981")
            _sched_task_tree.tag_configure("disabled", foreground="#9CA3AF")
    except Exception:
        pass


def start_multi_scheduler(main_run_fn):
    """启动多任务调度线程"""
    global _last_start_time
    current_time = time.time()
    if state._sched_thread_active:
        return
    if current_time - _last_start_time < _MIN_RESTART_INTERVAL:
        return

    _stop_event.clear()
    for task in state.SCHED_TASKS:
        if task.get("enabled", True):
            _calc_task_next_run(task)

    state._sched_thread_active = True
    _last_start_time = current_time
    t = threading.Thread(target=multi_task_loop, args=(main_run_fn,), daemon=True)
    t.start()
    log1("多任务调度已启动 ({} 个任务)".format(
        len([t for t in state.SCHED_TASKS if t.get("enabled", True)])))
