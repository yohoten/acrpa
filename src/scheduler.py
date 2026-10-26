"""Scheduled execution — 统一 Task 模型 + 可插拔触发器 + 字段化执行语义。

L0 (架构基础)
-------------
* 统一 Task: `target_type` ∈ {script, workflow, command} + `target` (+ `target_params`)；
  旧字段 `task["script"]` **始终保留并同步**，因此旧版本可无损降级读取。
* 触发器插件化: 主循环只遍历 `triggers` 注册表，不再有 `period_type` if-else 分支。
* 执行语义字段化: on_overlap / timeout_sec / missed_run / stop_hotkey / max_runs /
  start_date / end_date（全部可选，缺字段即取默认）。
* `next_run_at` 落库（ISO 字符串），重启不重算（只在补偿判定/触发后重算）。
* 任务库备份由 `state.save_config()` 负责（写盘前复制 + 保留 N 份 + 启动清理）。

L1 (P0 触发器七项)
------------------
错过补偿 / 倒计时 / 整点半点 / 生效起止与次数上限 / 任务热键 / 开机执行
—— 全部实现为 `src/triggers.py` 的注册项。新增触发器 = 新增 1 个类 + 1 行 register()。

向后兼容
--------
* 旧任务（只有 script/period_type/period_value）读取时经 `normalize_task()` 归一化。
* `_task_next_runs` 内存表继续维护（分钟精度），供旧 UI 代码直接读取。
"""
import time, threading, datetime, os, json, random
import state
import triggers
from utils import log1, themed


# ── 任务结构版本（与 .acrpas 的 schema 解耦）──
TASK_VERSION = 1

_root = None
_sched_enabled_var = None
_sched_next_label = None
_sched_task_tree = None  # 多任务 Treeview 引用
_stop_event = threading.Event()      # 旧「单任务定时执行」的停止信号
_multi_stop = threading.Event()      # 多任务调度线程的停止信号（与上面解耦，互不误杀）
_last_start_time = 0
_MIN_RESTART_INTERVAL = 2.0

# 宿主注入的执行钩子 (ACRPA 侧接线): 停止当前执行 / 投递主线程执行脚本
_stop_fn = None
_main_run_fn = None

# 多任务运行时状态
_task_threads = {}      # {task_id: thread}
_task_stop_flags = {}   # {task_id: threading.Event}
_task_next_runs = {}    # {task_id: "YYYY-MM-DD HH:MM"}  (旧 UI 兼容读取)
_task_last_runs = {}    # {task_id: "YYYY-MM-DD HH:MM:SS"}
_task_logs = []         # 执行日志
_task_lock = threading.Lock()

# 多任务调度自身状态（与 state._sched_thread_active 解耦：后者是旧「定时执行当前脚本」的存活标记）
_multi_active = False
_missed_checked = False          # 本进程是否已做过错过补偿判定
_run_token = 0                   # 当前执行令牌（超时看门狗据此判断"是否还是同一次运行"）
_compensated = set()             # {(task_id, due_iso)} 已处理过的错过窗口，防止重复补跑
_pending_queue = []              # on_overlap=queue 的待执行队列（上限 1，超出丢弃最旧）

# 执行语义默认值（缺字段即取默认，写入时不主动写默认值）
_EXEC_DEFAULTS = {
    "on_overlap": "skip",        # skip | queue | terminate
    "timeout_sec": 0,            # 0 = 不限（沿用全局 MAX_EXECUTION_MINUTES）
    "missed_run": "ignore",      # ignore | run_now | run_delay
    "missed_delay_sec": 60,
    "stop_hotkey": "",
    "sync": False,
}
_OVERLAP_MODES = ("skip", "queue", "terminate")
_MISSED_POLICIES = ("ignore", "run_now", "run_delay")
_DEFAULT_POLL = 30
_PENDING_MAX_AGE = 600           # 排队项超过 10 分钟视为过期丢弃


# ======================================================================
# 依赖注入
# ======================================================================
def set_gui_refs(root, enabled_var, next_label, task_tree=None):
    global _root, _sched_enabled_var, _sched_next_label, _sched_task_tree
    _root = root
    _sched_enabled_var = enabled_var
    _sched_next_label = next_label
    _sched_task_tree = task_tree


def set_exec_hooks(stop_fn=None, main_run_fn=None):
    """注入执行钩子：stop_fn=停止当前执行；main_run_fn=主机执行脚本的入口。"""
    global _stop_fn, _main_run_fn
    if stop_fn is not None:
        _stop_fn = stop_fn
    if main_run_fn is not None:
        _main_run_fn = main_run_fn


# ======================================================================
# 旧「单任务定时执行」（保持原样，与本模块的多任务调度互不干扰）
# ======================================================================
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
# 任务模型：归一化 / 默认值读取
# ======================================================================
def _new_task_id():
    """稳定且同一毫秒内不冲突的任务 ID（旧实现同毫秒会撞）。"""
    return "{}{:03d}".format(int(time.time() * 1000), random.randint(0, 999))


def exec_opt(task, key, default=None):
    """读取执行语义字段（兼容旧数据的 exec 子字典写法）。"""
    if not isinstance(task, dict):
        return default
    if task.get(key) not in (None, ""):
        return task.get(key)
    ex = task.get("exec")
    if isinstance(ex, dict) and ex.get(key) not in (None, ""):
        return ex.get(key)
    return _EXEC_DEFAULTS.get(key) if default is None else default


def normalize_task(task):
    """旧 8 字段模型 → 新模型（就地归一化，返回同一 dict，绝不抛）。"""
    if not isinstance(task, dict):
        return task
    try:
        if not task.get("id"):
            task["id"] = _new_task_id()
        try:
            task["ver"] = int(task.get("ver") or TASK_VERSION)
        except (TypeError, ValueError):
            task["ver"] = TASK_VERSION

        # ── 目标归一化：旧 task["script"] → target_type="script" + target ──
        ttype = str(task.get("target_type") or "").strip().lower()
        if ttype not in ("script", "workflow", "command"):
            if task.get("script"):
                ttype = "script"
            elif task.get("workflow") or task.get("workflow_path"):
                ttype = "workflow"
            elif task.get("command"):
                ttype = "command"
            else:
                ttype = "script"
            task["target_type"] = ttype
        if not task.get("target"):
            if ttype == "script":
                task["target"] = task.get("script") or ""
            elif ttype == "workflow":
                task["target"] = task.get("workflow") or task.get("workflow_path") or ""
            else:
                task["target"] = task.get("command") or ""
        # 写回时保留 script 字段，保证降级到旧版本仍可读
        if ttype == "script":
            task["script"] = task.get("target") or task.get("script") or ""

        # ── 触发器归一化：旧 period_type/period_value → triggers[0] ──
        specs = task.get("triggers")
        if not isinstance(specs, list) or not specs:
            task["triggers"] = [triggers.legacy_trigger(
                task.get("period_type"), task.get("period_value"))]
        return task
    except Exception:
        return task


def _find_task(task_id):
    for t in state.SCHED_TASKS:
        if isinstance(t, dict) and t.get("id") == task_id:
            return t
    return None


def _poll_interval():
    """轮询间隔（秒），复用既有 sched_poll_interval 配置；下限 5 s。"""
    try:
        v = int(getattr(state, "SCHED_POLL_INTERVAL", _DEFAULT_POLL) or _DEFAULT_POLL)
    except (TypeError, ValueError):
        v = _DEFAULT_POLL
    return max(5, v)


def _int_or_none(value):
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


# ======================================================================
# next_run_at 计算与落库
# ======================================================================
def _apply_next_run(task, dt):
    """把下次运行时间写回任务（ISO）+ 旧内存表（分钟精度）。"""
    tid = task.get("id")
    if not tid:
        return
    if dt is None:
        task["next_run_at"] = ""
        _task_next_runs.pop(tid, None)
    else:
        task["next_run_at"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
        _task_next_runs[tid] = dt.strftime("%Y-%m-%d %H:%M")


def _task_due(task):
    """读取任务的下次运行时间（next_run_at → next_run → 内存表，逐级回退）。"""
    dt = triggers.parse_dt(task.get("next_run_at"))
    if dt is None:
        dt = triggers.parse_dt(task.get("next_run"))
    if dt is None:
        dt = triggers.parse_dt(_task_next_runs.get(task.get("id")))
    return dt


def _calc_task_next_run(task):
    """重算任务的下次运行时间并落库（内部走触发器注册表，无 if-else 分派）。"""
    now = datetime.datetime.now()
    normalize_task(task)
    nxt = triggers.next_run_at_for(task, now)
    _apply_next_run(task, nxt)
    return nxt


def _recalc_if_needed(task, now):
    """next_run_at 缺失时补算；返回是否有变化（用于决定要不要写盘）。"""
    if _task_due(task) is not None:
        return False
    before = task.get("next_run_at")
    _calc_task_next_run(task)
    return task.get("next_run_at") != before


# ======================================================================
# 生效起止 / 次数上限
# ======================================================================
def _task_window_state(task, now=None):
    """→ (状态, 说明)：active / before / expired / limit。"""
    now = now or datetime.datetime.now()
    try:
        start_date = triggers.parse_date(task.get("start_date"))
        end_date = triggers.parse_date(task.get("end_date"))
        today = now.date()
        if start_date and today < start_date:
            return "before", "未到生效开始日期 {}".format(start_date.isoformat())
        if end_date and today > end_date:
            return "expired", "已过生效结束日期 {}".format(end_date.isoformat())
        max_runs = _int_or_none(task.get("max_runs"))
        if max_runs is not None and max_runs > 0:
            if int(task.get("run_count") or 0) >= max_runs:
                return "limit", "已达执行次数上限 {}".format(max_runs)
    except Exception:
        pass
    return "active", ""


def task_status(task, now=None):
    """任务状态文本（列表展示用）：启用 / 已禁用 / 未生效 / 已过期 / 已达上限。"""
    now = now or datetime.datetime.now()
    if not task.get("enabled", True):
        return "已禁用"
    st, _msg = _task_window_state(task, now)
    if st == "expired":
        return "已过期"
    if st == "before":
        return "未生效"
    if st == "limit":
        return "已达上限"
    return "启用"


def target_label(task):
    """目标的人类可读文本（列表展示用）。"""
    ttype = str(task.get("target_type") or "script").lower()
    target = task.get("target") or task.get("script") or ""
    if ttype == "command":
        return "命令:{}".format(target or "未设置")
    base = os.path.basename(target) if target else "未设置"
    return "{}:{}".format("工作流" if ttype == "workflow" else "脚本", base)


def task_summary(task, now=None):
    """任务列表一行所需的全部文本（UI 只读这一个接口）。"""
    normalize_task(task)
    now = now or datetime.datetime.now()
    due = _task_due(task)
    next_text = due.strftime("%Y-%m-%d %H:%M") if due else "--"
    last = task.get("last_run_at") or _task_last_runs.get(task.get("id"), "--")
    return {
        "id": task.get("id"),
        "name": task.get("name") or task.get("id"),
        "target": target_label(task),
        "plan": triggers.describe_task(task),
        "next_run": next_text,
        "last_run": last or "--",
        "status": task_status(task, now),
        "enabled": bool(task.get("enabled", True)),
        "run_count": int(task.get("run_count") or 0),
        "max_runs": _int_or_none(task.get("max_runs")),
    }


# ======================================================================
# 任务增删改
# ======================================================================
def add_task(name, script_path="", period_type="daily", period_value="09:00",
             enabled=True, time_slots=None, target_type="script", target=None,
             trigger_specs=None, **opts):
    """添加计划任务。

    Args:
        name: 任务名称
        script_path: 脚本路径（target_type="script" 且未传 target 时使用）
        period_type: 旧式周期类型 (once/daily/weekly/interval)，仅在未传
                     trigger_specs 时用于生成等价触发器（向后兼容）
        period_value: 旧式周期值
        enabled: 是否启用
        time_slots: 时间段过滤列表 [("09:00","12:00"), ...]，空=None 表示不限
        target_type: script / workflow / command
        target: 目标（脚本路径 / 工作流文件 / 命令名）
        trigger_specs: 触发器数组 [{"kind": ...}, ...]
        opts: on_overlap / timeout_sec / missed_run / missed_delay_sec /
              stop_hotkey / max_runs / start_date / end_date / hotkey_spec /
              target_params
    """
    ttype = str(target_type or "script").strip().lower()
    tgt = target if target is not None else script_path
    task = normalize_task({
        "id": _new_task_id(),
        "ver": TASK_VERSION,
        "name": name,
        "target_type": ttype,
        "target": tgt or "",
        "script": tgt if ttype == "script" else "",
        "enabled": bool(enabled),
        "time_slots": list(time_slots or []),  # 时间段过滤
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_count": 0,
    })
    # 兼容字段：旧版本降级可读（新代码只读 triggers）
    task["period_type"] = period_type or "daily"
    task["period_value"] = period_value or ""
    if trigger_specs:
        task["triggers"] = [dict(s) for s in trigger_specs if isinstance(s, dict)]
    if opts.get("hotkey_spec"):
        task["hotkey_spec"] = str(opts.get("hotkey_spec"))
        if not any(str(s.get("kind")) == "hotkey" for s in task["triggers"]):
            task["triggers"].append({"kind": "hotkey", "spec": task["hotkey_spec"]})
    if isinstance(opts.get("target_params"), dict):
        task["target_params"] = dict(opts["target_params"])
    for k in ("on_overlap", "timeout_sec", "missed_run", "missed_delay_sec",
              "stop_hotkey", "max_runs", "start_date", "end_date"):
        if opts.get(k) not in (None, ""):
            task[k] = opts[k]
    state.SCHED_TASKS.append(task)
    _calc_task_next_run(task)
    _save_tasks()
    return task


def update_task(task_id, patch):
    """按字段更新任务（None = 删除该字段）；成功返回 task，未找到返回 None。"""
    task = _find_task(task_id)
    if task is None:
        return None
    for k, v in (patch or {}).items():
        if v is None:
            task.pop(k, None)
        else:
            task[k] = v
    if str(task.get("target_type") or "script").lower() == "script":
        task["script"] = task.get("target") or task.get("script") or ""
    normalize_task(task)
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
        _pending_queue[:] = [x for x in _pending_queue if x[0] != task_id]
    state.SCHED_TASKS = [t for t in state.SCHED_TASKS if t.get("id") != task_id]
    _task_next_runs.pop(task_id, None)
    _save_tasks()


def toggle_task(task_id, enabled):
    """启用/禁用计划任务"""
    for t in state.SCHED_TASKS:
        if t.get("id") == task_id:
            normalize_task(t)
            t["enabled"] = enabled
            if enabled:
                _calc_task_next_run(t)
            else:
                _apply_next_run(t, None)
                with _task_lock:
                    if task_id in _task_stop_flags:
                        _task_stop_flags[task_id].set()
                        _task_stop_flags.pop(task_id, None)
                        _task_threads.pop(task_id, None)
            break
    _save_tasks()


def _save_tasks():
    """持久化计划任务到 config.json（任务库备份由 state.save_config 负责）"""
    try:
        state.save_config()
    except Exception:
        pass


# ======================================================================
# 执行日志
# ======================================================================
def get_task_logs(limit=200):
    """获取执行日志"""
    with _task_lock:
        return list(_task_logs[-limit:])


def clear_task_logs():
    """清空执行日志"""
    with _task_lock:
        _task_logs.clear()


def _add_task_log(task_name, script_name, status, trigger="定时", log_type="info",
                  folder="", reason=""):
    """添加一条执行日志（reason 为「带原因的」补充说明，L0-④ 验收要求）。"""
    entry = {
        "task_name": task_name,
        "script_name": script_name,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "trigger": trigger,
        "log_type": log_type,
        "folder": folder,
        "reason": reason,
    }
    with _task_lock:
        _task_logs.append(entry)
        if len(_task_logs) > 500:
            _task_logs[:] = _task_logs[-500:]


# ======================================================================
# 执行投递：目标三型 + 执行语义
# ======================================================================
def _request_stop():
    """请求停止当前执行（复用宿主 stop_execution；非主线程时回流主线程）。"""
    fn = _stop_fn
    if fn is None:
        state.quit3 = True
        state.quit2 = True
        return
    try:
        if _root is not None:
            _root.after(0, fn)
        else:
            fn()
    except Exception:
        try:
            fn()
        except Exception:
            pass


def _wait_stop(timeout=2.0):
    """等待上一次执行真正退出（最多 timeout 秒）。"""
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        if not state.running:
            return True
        time.sleep(0.05)
    return not state.running


def _run_script(task, target, name, label):
    """脚本目标：沿用宿主的 main_run 流程（state.filename + root.after 回流主线程）。"""
    if not target or not os.path.exists(target):
        log1("计划任务脚本不存在: {}".format(target), "warning")
        _add_task_log(name, os.path.basename(target) if target else "N/A",
                      "失败-脚本不存在", label, "error")
        return False
    _add_task_log(name, os.path.basename(target), "运行中", label)
    state.filename = target
    state.has_script = True
    state.script_dir = os.path.dirname(target)
    if _main_run_fn is not None and _root is not None:
        _root.after(0, _main_run_fn)
        return True
    log1("计划任务无法投递执行 (宿主未接线): {}".format(name), "error")
    _add_task_log(name, os.path.basename(target), "失败-未接线", label, "error")
    return False


def _run_workflow(task, target, name, label):
    """工作流目标：复用既有 workflow 执行入口（不重写执行器）。"""
    if not target or not os.path.exists(target):
        log1("计划任务工作流不存在: {}".format(target), "warning")
        _add_task_log(name, os.path.basename(target) if target else "N/A",
                      "失败-工作流不存在", label, "error")
        return False
    _add_task_log(name, os.path.basename(target), "运行中", label)

    def _work():
        try:
            import workflow as wf_mod
            wf_def = wf_mod.parse_workflow(target)
            state.quit2 = False
            state.quit3 = True
            wf_mod.workflow_engine.run_workflow(wf_def, os.path.dirname(target))
            _add_task_log(name, os.path.basename(target), "完成", label)
        except Exception as e:
            log1("计划任务工作流执行失败: {} — {}".format(name, e), "error")
            _add_task_log(name, os.path.basename(target), "失败", label, "error", reason=str(e))

    threading.Thread(target=_work, daemon=True).start()
    return True


def _command_row(task):
    """命令目标 → (命令名, 9 个参数) ；参数来自 target_params.args 或 target_params.params。"""
    cmd = str(task.get("target") or "").strip()
    params = task.get("target_params") if isinstance(task.get("target_params"), dict) else {}
    if not cmd:
        cmd = str(params.get("cmd") or "").strip()
    args = params.get("args")
    if args is None:
        args = params.get("params")
    if isinstance(args, str):
        args = args.split(",") if args else []
    if not isinstance(args, (list, tuple)):
        args = []
    return cmd, list(args)[:9]


def _run_command(task, name, label):
    """单命令目标：走 commands 注册表 + engine.execute（一次只执行一条）。"""
    cmd, args = _command_row(task)
    if not cmd:
        log1("计划任务命令为空: {}".format(name), "error")
        _add_task_log(name, "N/A", "失败-命令为空", label, "error")
        return False
    _add_task_log(name, cmd, "运行中", label)

    def _work():
        try:
            import engine as engine_mod
            import scriptdata
            row = scriptdata.ScriptData(cmd, args)
            state.quit2 = False
            state.quit3 = True
            state.running = True
            try:
                engine_mod.engine.execute(row, state.script_dir or os.path.dirname(state.CONFIG_PATH))
            finally:
                state.running = False
            _add_task_log(name, cmd, "完成", label)
        except Exception as e:
            try:
                state.running = False
            except Exception:
                pass
            log1("计划任务命令执行失败: {} — {}".format(name, e), "error")
            _add_task_log(name, cmd, "失败", label, "error", reason=str(e))

    threading.Thread(target=_work, daemon=True).start()
    return True


def _arm_timeout(task, token, label):
    """任务级超时看门狗：到点仍处于同一次运行则请求停止并标记失败。"""
    try:
        secs = _int_or_none(exec_opt(task, "timeout_sec", 0)) or 0
    except Exception:
        secs = 0
    if secs <= 0:
        return
    name = task.get("name") or task.get("id")

    def _watch():
        deadline = float(secs)
        while deadline > 0:
            step = min(1.0, deadline)
            time.sleep(step)
            deadline -= step
            if _run_token != token or state._closing:
                return
        if _run_token != token or state._closing:
            return
        if not state.running:
            return
        log1("计划任务执行超时 ({}s)，请求停止: {}".format(secs, name), "error")
        task["last_result"] = "超时失败"
        _add_task_log(name, target_label(task), "失败-超时", label, "error",
                      reason="超过任务级超时 {} 秒".format(secs))
        _request_stop()

    threading.Thread(target=_watch, daemon=True).start()


def _execute_task(task, now, label="定时", reason=""):
    """真正投递执行（不再做重入判定）；返回是否投递成功。"""
    global _run_token
    name = task.get("name") or task.get("id")
    ttype = str(task.get("target_type") or "script").lower()
    target = task.get("target") or task.get("script") or ""

    _run_token += 1
    token = _run_token
    task["last_run_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        task["run_count"] = int(task.get("run_count") or 0) + 1
    except (TypeError, ValueError):
        task["run_count"] = 1
    _task_last_runs[task.get("id")] = task["last_run_at"]

    if ttype == "workflow":
        ok = _run_workflow(task, target, name, label)
    elif ttype == "command":
        ok = _run_command(task, name, label)
    else:
        ok = _run_script(task, target, name, label)

    task["last_result"] = "运行中" if ok else "失败"
    _arm_timeout(task, token, label)
    if reason:
        _add_task_log(name, target_label(task), "触发({})".format(reason), label)
    return ok


def _enqueue_pending(task, due):
    """on_overlap=queue：排队等上一次结束（队列上限 1，同一任务只保留最新一次）。"""
    tid = task.get("id")
    with _task_lock:
        _pending_queue[:] = [x for x in _pending_queue if x[0] != tid]
        _pending_queue.append((tid, due))
        dropped = None
        while len(_pending_queue) > 1:
            dropped = _pending_queue.pop(0)
            log1("计划任务排队超出上限(1)，丢弃较早的一次: {}".format(dropped[0]), "warning")


def _drain_pending(now):
    """上一次执行结束后，消费待执行队列。"""
    if state.running:
        return
    with _task_lock:
        items = list(_pending_queue)
        _pending_queue.clear()
    for tid, due in items:
        task = _find_task(tid)
        if task is None or not task.get("enabled", True):
            continue
        if due is not None and (now - due).total_seconds() > _PENDING_MAX_AGE:
            log1("计划任务排队等待超过 {}s，丢弃: {}".format(_PENDING_MAX_AGE, tid), "warning")
            _add_task_log(task.get("name") or tid, target_label(task),
                          "丢弃-排队过期", "排队", "warning")
            continue
        _execute_task(task, now, "排队", "排队补执行")


def _after_fire(task, now, trg=None):
    """触发后的簿记：一次性停用 / 次数上限 / 重算 next_run_at。"""
    tid = task.get("id")
    name = task.get("name") or tid
    if trg is not None:
        try:
            trg.on_fired(task, now)
        except Exception:
            pass
    # 一次性触发器 → 自动停用
    if trg is not None and getattr(trg, "one_shot", False):
        task["enabled"] = False
        _apply_next_run(task, None)
        log1("计划任务已完成一次性触发，自动停用: {}".format(name))
        _add_task_log(name, target_label(task), "已完成-自动停用", "定时")
        return
    # 次数上限
    max_runs = _int_or_none(task.get("max_runs"))
    if max_runs is not None and max_runs > 0 and int(task.get("run_count") or 0) >= max_runs:
        task["enabled"] = False
        _apply_next_run(task, None)
        log1("计划任务已达执行次数上限 {}，自动停用: {}".format(max_runs, name), "warning")
        _add_task_log(name, target_label(task), "已达上限-自动停用", "定时", "warning")
        return
    _calc_task_next_run(task)


def _fire_task(task, now, label="定时", reason="", trg=None, advance=True):
    """统一的触发入口（调度线程 / 热键 / 手动 / 补偿 共用）。

    返回 True = 已投递执行；False = 因重入策略等原因未执行（仍会推进 next_run）。
    """
    name = task.get("name") or task.get("id")
    overlap = str(exec_opt(task, "on_overlap", "skip") or "skip").lower()
    if overlap not in _OVERLAP_MODES:
        overlap = "skip"

    if state.running:
        if overlap == "queue":
            _enqueue_pending(task, now)
            log1("计划任务排队等待 (上次执行未结束): {}".format(name), "warning")
            _add_task_log(name, target_label(task), "排队-上次未结束", label, "warning",
                          reason="on_overlap=queue")
            if advance:
                _after_fire(task, now, trg)
            return False
        if overlap == "terminate":
            log1("计划任务重入策略 terminate，先停止当前执行: {}".format(name), "warning")
            _add_task_log(name, target_label(task), "终止上次执行", label, "warning",
                          reason="on_overlap=terminate")
            _request_stop()
            _wait_stop(timeout=2.0)
        else:
            log1("定时任务触发时脚本正在运行，跳过: {}".format(name), "warning")
            _add_task_log(name, target_label(task), "跳过-运行中", label, "warning",
                          reason="on_overlap=skip")
            if advance:
                _after_fire(task, now, trg)
            return False

    ok = _execute_task(task, now, label, reason)
    if advance:
        _after_fire(task, now, trg)
    return ok


# ======================================================================
# 单个任务判定（主循环为每个任务调用一次）
# ======================================================================
def _evaluate_task(task, now):
    """判定单个任务是否到期/被事件命中；命中则触发。"""
    normalize_task(task)
    if not task.get("enabled", True):
        return
    win_state, win_msg = _task_window_state(task, now)
    if win_state != "active":
        if win_state == "limit":
            task["enabled"] = False
            _apply_next_run(task, None)
            log1("计划任务{}，自动停用: {}".format(win_msg, task.get("name")), "warning")
            _add_task_log(task.get("name") or task.get("id"), target_label(task),
                          "已达上限-自动停用", "定时", "warning", reason=win_msg)
            _save_tasks()
        elif win_state == "expired":
            if _task_due(task) is not None:
                _apply_next_run(task, None)
                _save_tasks()
        return

    # ── 时间段过滤: 不在允许时段内则跳过（语义与旧实现一致）──
    if not _is_in_time_slots(now, task.get("time_slots")):
        return

    specs = task.get("triggers") or []
    if not specs:
        return

    # next_run_at 缺失时补算一次（热键型任务没有 next_run_at，属正常）
    if _task_due(task) is None and _recalc_if_needed(task, now):
        _save_tasks()

    hit, hit_trg = False, None
    for spec in specs:
        trg = triggers.make(spec)
        if trg is None:
            continue
        try:
            if trg.should_fire(now, task):
                hit, hit_trg = True, trg
                break
        except Exception as e:
            log1("触发器判定异常 ({}): {}".format(spec.get("kind"), e), "warning")
    if not hit:
        return

    label = hit_trg.describe() if hit_trg is not None else "定时"
    _fire_task(task, now, label, "", hit_trg)
    _save_tasks()


# ======================================================================
# 错过执行补偿（启动时一次性判定，只补一次）
# ======================================================================
def _run_missed_compensation(now):
    """启动时比对 next_run_at 与 now，按 missed_run 策略处理。

    * 只判定一次（`_missed_checked`），且同一 (task_id, due) 窗口只处理一次。
    * 含 on_boot 触发器的任务不参与补偿 —— 由开机触发器自己负责本次启动的执行
      （两者互斥去重，避免同一次启动重复执行）。
    """
    poll = _poll_interval()
    for task in list(state.SCHED_TASKS):
        normalize_task(task)
        if not task.get("enabled", True):
            continue
        win_state, _msg = _task_window_state(task, now)
        if win_state != "active":
            continue
        due = _task_due(task)
        if due is None:
            continue
        late = (now - due).total_seconds()
        if late <= poll:
            continue  # 未超出一个轮询周期 → 交给主循环正常触发，不算"错过"
        if triggers.has_boot_trigger(task):
            _calc_task_next_run(task)
            continue
        key = (task.get("id"), due.strftime("%Y-%m-%dT%H:%M:%S"))
        if key in _compensated:
            continue
        _compensated.add(key)

        name = task.get("name") or task.get("id")
        policy = str(exec_opt(task, "missed_run", "ignore") or "ignore").lower()
        if policy not in _MISSED_POLICIES:
            policy = "ignore"
        trg = triggers.trigger_for_due(task, due, now)

        if policy == "run_now":
            log1("计划任务错过补偿(立即执行): {} (错过 {}s)".format(name, int(late)), "warning")
            _add_task_log(name, target_label(task), "补偿-立即执行", "补偿", "warning",
                          reason="错过 {} 秒".format(int(late)))
            _fire_task(task, now, "补偿", "错过补偿", trg)
        elif policy == "run_delay":
            delay = _int_or_none(exec_opt(task, "missed_delay_sec", 60))
            delay = 60 if delay is None else max(0, delay)
            _apply_next_run(task, now + datetime.timedelta(seconds=delay))
            log1("计划任务错过补偿(延迟 {}s 执行): {}".format(delay, name), "warning")
            _add_task_log(name, target_label(task), "补偿-延迟{}s".format(delay), "补偿", "warning",
                          reason="错过 {} 秒".format(int(late)))
        else:
            # ignore：一次性触发器标记已消费，避免后续再补跑；周期型直接推进窗口
            if trg is not None and getattr(trg, "one_shot", False):
                try:
                    trg.on_fired(task, now)
                except Exception:
                    pass
            _calc_task_next_run(task)
            log1("计划任务错过补偿已忽略: {} (错过 {}s)".format(name, int(late)))
    _save_tasks()


# ======================================================================
# 多任务主循环（触发器注册表驱动，无 period_type 分支）
# ======================================================================
def multi_task_loop(main_run_fn=None):
    """后台线程：轮询所有任务的触发器（时间型 + 事件型）。"""
    global _multi_active, _missed_checked, _main_run_fn
    if main_run_fn is not None:
        _main_run_fn = main_run_fn
    log1("多任务调度线程已启动 (轮询间隔: {}s | 触发器: {})".format(
        _poll_interval(), ", ".join(triggers.list_kinds())))
    _multi_active = True
    try:
        while not state._closing:
            try:
                now = datetime.datetime.now()
                if not _missed_checked:
                    _missed_checked = True
                    try:
                        _run_missed_compensation(now)
                    except Exception as e:
                        log1("错过补偿判定异常: {}".format(e), "error")
                try:
                    _drain_pending(now)
                except Exception as e:
                    log1("排队队列处理异常: {}".format(e), "error")
                for task in list(state.SCHED_TASKS):
                    try:
                        _evaluate_task(task, now)
                    except Exception as e:
                        log1("计划任务判定异常: {}".format(e), "error")

                if _root and _sched_task_tree:
                    try:
                        _root.after(0, _refresh_task_tree)
                    except Exception:
                        pass

                stop_now = False
                for _ in range(_poll_interval()):
                    if _multi_stop.is_set() or state._closing:
                        stop_now = True
                        break
                    time.sleep(1)
                if stop_now:
                    break
            except Exception as e:
                log1("多任务调度异常: {}".format(e), "error")
                time.sleep(_poll_interval())
    finally:
        _multi_active = False
        _multi_stop.clear()
        log1("多任务调度线程已停止")


def stop_multi_scheduler():
    """请求停止多任务调度线程（仅置标志，不阻塞调用方）。"""
    _multi_stop.set()


def is_multi_active():
    return _multi_active


def run_task_now(task_id, main_run_fn=None):
    """立即执行指定任务（手动触发；不推进 next_run_at / 不消费一次性触发器）。"""
    global _main_run_fn
    if main_run_fn is not None:
        _main_run_fn = main_run_fn
    task = _find_task(task_id)
    if task is None:
        return False
    normalize_task(task)
    now = datetime.datetime.now()
    return _fire_task(task, now, "手动", "手动触发", None, advance=False)


# ======================================================================
# 时间段过滤（语义原样保留）
# ======================================================================
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


def _slots_text(task):
    slots = task.get("time_slots", [])
    try:
        return ", ".join("{}-{}".format(s, e) for s, e in slots) if slots else "全天"
    except Exception:
        return "全天"


# ======================================================================
# Treeview 刷新（调度器内部注入的 Treeview 用；任务管理器有独立实现）
# ======================================================================
def _refresh_task_tree():
    """刷新计划任务 Treeview"""
    if not _sched_task_tree:
        return
    try:
        for item in _sched_task_tree.get_children():
            _sched_task_tree.delete(item)
        for task in state.SCHED_TASKS:
            row = task_summary(task)
            values = (
                row["name"],
                row["target"],
                row["plan"],
                _slots_text(task),
                row["next_run"],
                row["last_run"],
                row["status"],
            )
            tag = "enabled" if row["enabled"] else "disabled"
            _sched_task_tree.insert("", "end", values=values, iid=row["id"], tags=(tag,))
            _sched_task_tree.tag_configure("enabled", foreground=themed("sc"))
            _sched_task_tree.tag_configure("disabled", foreground=themed("fgm"))
    except Exception:
        pass


# ======================================================================
# 启动多任务调度
# ======================================================================
def start_multi_scheduler(main_run_fn=None):
    """启动多任务调度线程。

    注意：**不在启动时重算 next_run_at** —— 落库的值要留给错过补偿判定使用
    （只有补偿场景才重算），这样重启后任务列表的"下次执行"保持不变。
    """
    global _last_start_time, _main_run_fn, _missed_checked
    if main_run_fn is not None:
        _main_run_fn = main_run_fn
    if _multi_active:
        return
    current_time = time.time()
    if current_time - _last_start_time < _MIN_RESTART_INTERVAL:
        return

    _multi_stop.clear()
    _missed_checked = False
    for task in state.SCHED_TASKS:
        normalize_task(task)      # 只归一化，不重算 → 保留 next_run_at

    _last_start_time = current_time
    t = threading.Thread(target=multi_task_loop, args=(_main_run_fn,), daemon=True)
    t.start()
    log1("多任务调度已启动 ({} 个任务)".format(
        len([t for t in state.SCHED_TASKS if t.get("enabled", True)])))
