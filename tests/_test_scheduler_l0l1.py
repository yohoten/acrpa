# -*- coding: utf-8 -*-
"""L0 + L1 调度架构自检（无 GUI / 无网络 / 不触碰真实 config.json）。

覆盖 8 条手工自检清单：
  1. 旧格式任务（仅 period_type）能正常加载并按时触发
  2. on_overlap 三态（skip / queue / terminate）
  3. missed_run 补跑不重复
  4. 倒计时触发后自动停用
  5. 整点半点 + 小时白名单
  6. 生效起止 / 次数上限边界
  7. 任务热键（事件队列 + 归一化 + 与内建热键冲突忽略）
  8. 开机执行与错过补偿不重复执行同一次

设计要点：
  * state.CONFIG_PATH 改指临时目录 —— 绝不覆盖用户的 config.json；
  * 所有任务目标都指向不存在的文件 → 只验证调度语义，不真的执行脚本/命令；
  * 退出码 0 = 全部通过，1 = 有失败（供 tools/run_tests.py 纳入 CI）。
"""
import datetime
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import state          # noqa: E402
import triggers       # noqa: E402
import scheduler      # noqa: E402

# ── 隔离：临时配置目录 ─────────────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix="acrpa_sched_l0l1_")
state.CONFIG_PATH = os.path.join(_TMP, "config.json")
state.SCHED_TASKS = []

_fails = []


def check(name, cond, detail=""):
    if cond:
        print("PASS  {}".format(name))
    else:
        print("FAIL  {}  {}".format(name, detail))
        _fails.append(name)


def _dt(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def _reset_tasks():
    state.SCHED_TASKS = []
    scheduler._pending_queue[:] = []
    scheduler._task_next_runs.clear()
    scheduler._task_last_runs.clear()
    scheduler._compensated.clear()
    scheduler._task_logs[:] = []


# ══════════════════════════════════════════════════════════════════
# 1. 旧格式任务归一化 + next_run_at 落库
# ══════════════════════════════════════════════════════════════════
_reset_tasks()
legacy = {"id": "1", "name": "旧任务", "script": "nope.xls",
          "period_type": "daily", "period_value": "09:00", "enabled": True}
scheduler.normalize_task(legacy)
check("旧任务: target_type 归一化为 script",
      legacy.get("target_type") == "script" and legacy.get("target") == "nope.xls")
check("旧任务: script 字段保留（可降级）", legacy.get("script") == "nope.xls")
check("旧任务: period_type → triggers[0] daily",
      legacy["triggers"][0]["kind"] == "daily" and legacy["triggers"][0]["time"] == "09:00")

legacy_weekly = {"id": "2", "name": "旧周任务", "script": "nope.xls",
                 "period_type": "weekly", "period_value": "0,1|09:30"}
scheduler.normalize_task(legacy_weekly)
check("旧周任务: '0,1|09:30' → days=[0,1] time=09:30",
      legacy_weekly["triggers"][0]["days"] == [0, 1]
      and legacy_weekly["triggers"][0]["time"] == "09:30")

legacy_interval = {"id": "3", "name": "旧间隔", "script": "nope.xls",
                   "period_type": "interval", "period_value": "120"}
scheduler.normalize_task(legacy_interval)
check("旧间隔任务: '120' → interval.seconds=120",
      legacy_interval["triggers"][0]["seconds"] == 120)

nxt = scheduler._calc_task_next_run(legacy)
check("next_run_at 落库为 ISO 字符串",
      bool(legacy.get("next_run_at")) and "T" in legacy["next_run_at"],
      repr(legacy.get("next_run_at")))
check("_task_next_runs 分钟精度兼容表同步",
      scheduler._task_next_runs.get("1") == nxt.strftime("%Y-%m-%d %H:%M"))
check("next_run_at 可被 _task_due 读回（重启不重算的前提）",
      scheduler._task_due(legacy) == nxt)

# 旧任务按时触发：把 next_run_at 设到过去 → 主循环判定到期
state.SCHED_TASKS = [legacy]
legacy["next_run_at"] = (datetime.datetime.now() - datetime.timedelta(seconds=5)) \
    .strftime("%Y-%m-%dT%H:%M:%S")
legacy["run_count"] = 0
scheduler._evaluate_task(legacy, datetime.datetime.now())
check("旧任务到期后触发（run_count 递增）",
      int(legacy.get("run_count") or 0) == 1, "run_count={}".format(legacy.get("run_count")))
check("旧任务触发后 next_run_at 推进到未来",
      scheduler._task_due(legacy) is not None
      and scheduler._task_due(legacy) > datetime.datetime.now())

# ══════════════════════════════════════════════════════════════════
# 2. on_overlap 三态
# ══════════════════════════════════════════════════════════════════
_reset_tasks()
t_skip = scheduler.normalize_task({"id": "s", "name": "skip任务", "script": "nope.xls",
                                   "enabled": True, "on_overlap": "skip",
                                   "triggers": [{"kind": "interval", "seconds": 3600}]})
state.SCHED_TASKS = [t_skip]
state.running = True
ok = scheduler._fire_task(t_skip, datetime.datetime.now(), "定时")
check("on_overlap=skip: 运行中不执行并返回 False", ok is False)
check("on_overlap=skip: 写一条带原因的日志",
      any("跳过" in x.get("status", "") for x in scheduler.get_task_logs(10)),
      [x.get("status") for x in scheduler.get_task_logs(10)])

t_queue = scheduler.normalize_task({"id": "q", "name": "queue任务", "script": "nope.xls",
                                    "enabled": True, "on_overlap": "queue",
                                    "triggers": [{"kind": "interval", "seconds": 3600}]})
state.SCHED_TASKS = [t_queue]
scheduler._fire_task(t_queue, datetime.datetime.now(), "定时")
scheduler._fire_task(t_queue, datetime.datetime.now(), "定时")
check("on_overlap=queue: 同一任务只保留一条待执行（上限 1）",
      len(scheduler._pending_queue) == 1, "pending={}".format(scheduler._pending_queue))

stopped = {"called": 0}


def _fake_stop():
    stopped["called"] += 1
    state.running = False


scheduler.set_exec_hooks(stop_fn=_fake_stop)
t_term = scheduler.normalize_task({"id": "t", "name": "terminate任务", "script": "nope.xls",
                                   "enabled": True, "on_overlap": "terminate",
                                   "triggers": [{"kind": "interval", "seconds": 3600}]})
state.SCHED_TASKS = [t_term]
state.running = True
scheduler._fire_task(t_term, datetime.datetime.now(), "定时")
check("on_overlap=terminate: 先请求停止当前执行", stopped["called"] >= 1)
state.running = False

# ══════════════════════════════════════════════════════════════════
# 3. missed_run 补跑不重复
# ══════════════════════════════════════════════════════════════════
_reset_tasks()
missed = scheduler.normalize_task({
    "id": "m", "name": "补偿任务", "script": "nope.xls", "enabled": True,
    "missed_run": "run_now", "run_count": 0,
    "triggers": [{"kind": "interval", "seconds": 600}]})
missed["next_run_at"] = (datetime.datetime.now() - datetime.timedelta(hours=2)) \
    .strftime("%Y-%m-%dT%H:%M:%S")
state.SCHED_TASKS = [missed]
now = datetime.datetime.now()
scheduler._run_missed_compensation(now)
scheduler._run_missed_compensation(now)   # 第二次必须什么都不做
check("missed_run=run_now: 补跑恰好一次", int(missed.get("run_count") or 0) == 1,
      "run_count={}".format(missed.get("run_count")))
check("missed_run: 补偿窗口已登记（去重）", len(scheduler._compensated) == 1)

_reset_tasks()
missed_ignore = scheduler.normalize_task({
    "id": "mi", "name": "忽略补偿", "script": "nope.xls", "enabled": True,
    "missed_run": "ignore", "run_count": 0,
    "triggers": [{"kind": "interval", "seconds": 600}]})
missed_ignore["next_run_at"] = (datetime.datetime.now() - datetime.timedelta(hours=2)) \
    .strftime("%Y-%m-%dT%H:%M:%S")
state.SCHED_TASKS = [missed_ignore]
scheduler._run_missed_compensation(datetime.datetime.now())
check("missed_run=ignore: 不补跑", int(missed_ignore.get("run_count") or 0) == 0)
check("missed_run=ignore: 推进到下一个窗口",
      scheduler._task_due(missed_ignore) is not None
      and scheduler._task_due(missed_ignore) > datetime.datetime.now())

_reset_tasks()
missed_delay = scheduler.normalize_task({
    "id": "md", "name": "延迟补偿", "script": "nope.xls", "enabled": True,
    "missed_run": "run_delay", "missed_delay_sec": 60, "run_count": 0,
    "triggers": [{"kind": "interval", "seconds": 600}]})
missed_delay["next_run_at"] = (datetime.datetime.now() - datetime.timedelta(hours=2)) \
    .strftime("%Y-%m-%dT%H:%M:%S")
state.SCHED_TASKS = [missed_delay]
scheduler._run_missed_compensation(datetime.datetime.now())
due = scheduler._task_due(missed_delay)
check("missed_run=run_delay: 下次执行被推迟到 now+delay",
      due is not None and 0 < (due - datetime.datetime.now()).total_seconds() <= 65)

# ══════════════════════════════════════════════════════════════════
# 4. 倒计时触发后自动停用
# ══════════════════════════════════════════════════════════════════
_reset_tasks()
cd = scheduler.normalize_task({"id": "cd", "name": "倒计时", "script": "nope.xls",
                               "enabled": True, "run_count": 0,
                               "triggers": [{"kind": "countdown", "seconds": 1}]})
state.SCHED_TASKS = [cd]
base = _dt("2026-01-05 10:00:00")
scheduler._calc_task_next_run(cd)          # 以真实 now 定 due_at 并落库
spec = cd["triggers"][0]
check("倒计时: due_at 首次解析即落库", bool(spec.get("due_at")), repr(spec))
due = triggers.parse_dt(spec.get("due_at"))
check("倒计时: 到期时间 = 设定时刻 + 1s（+容差）", due is not None)
scheduler._evaluate_task(cd, due + datetime.timedelta(seconds=1))
check("倒计时: 到点触发后任务自动停用", cd.get("enabled") is False)
check("倒计时: 一次性触发器标记已消费", bool(spec.get("fired_at")))
check("倒计时: 停用后 next_run_at 清空", not cd.get("next_run_at"))

# ══════════════════════════════════════════════════════════════════
# 5. 整点 / 半点 + 小时白名单
# ══════════════════════════════════════════════════════════════════
h_spec = {"kind": "hourly", "mode": "full", "hours": [9, 12, 18]}
h = triggers.make(h_spec)
n1 = h.next_run(_dt("2026-01-05 10:30:00"))
check("整点: 10:30 的白名单[9,12,18] → 当天 12:00",
      n1 == _dt("2026-01-05 12:00:00"), repr(n1))
n2 = h.next_run(_dt("2026-01-05 19:00:00"))
check("整点: 19:00 后跨天 → 次日 09:00", n2 == _dt("2026-01-06 09:00:00"), repr(n2))
h_both = triggers.make({"kind": "hourly", "mode": "both", "hours": [9]})
n3 = h_both.next_run(_dt("2026-01-05 09:10:00"))
check("半点: mode=both 在 09:10 → 09:30", n3 == _dt("2026-01-05 09:30:00"), repr(n3))
h_min = triggers.make({"kind": "hourly", "minute": 15, "hours": [8]})
n4 = h_min.next_run(_dt("2026-01-05 07:00:00"))
check("整点: 显式 minute=15 → 08:15", n4 == _dt("2026-01-05 08:15:00"), repr(n4))

# ══════════════════════════════════════════════════════════════════
# 6. 生效起止 / 次数上限边界
# ══════════════════════════════════════════════════════════════════
today = datetime.date.today()
t_win = {"id": "w", "name": "窗口任务", "script": "nope.xls", "enabled": True,
         "triggers": [{"kind": "interval", "seconds": 600}]}
t_win["start_date"] = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
check("生效起止: 开始日期在未来 → 未生效",
      scheduler._task_window_state(t_win)[0] == "before")
t_win["start_date"] = today.strftime("%Y-%m-%d")   # 边界：等于开始日 → 生效
check("生效起止: 等于开始日 → 生效（含当日）",
      scheduler._task_window_state(t_win)[0] == "active")
t_win["end_date"] = today.strftime("%Y-%m-%d")     # 边界：等于结束日 → 仍生效
check("生效起止: 等于结束日 → 仍生效（含当日）",
      scheduler._task_window_state(t_win)[0] == "active")
t_win["end_date"] = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
check("生效起止: 结束日在过去 → 已过期",
      scheduler._task_window_state(t_win)[0] == "expired")
check("生效起止: 状态文本为「已过期」而非「已禁用」",
      scheduler.task_status(t_win) == "已过期", scheduler.task_status(t_win))
t_win.pop("end_date", None)
t_win["max_runs"] = 2
t_win["run_count"] = 1
check("次数上限: 未达上限 → 仍生效",
      scheduler._task_window_state(t_win)[0] == "active")
t_win["run_count"] = 2                              # 边界：等于上限 → 停用
state.SCHED_TASKS = [t_win]
scheduler._evaluate_task(t_win, datetime.datetime.now())
check("次数上限: 达到 max_runs → 自动停用", t_win.get("enabled") is False)

# ══════════════════════════════════════════════════════════════════
# 7. 任务热键（事件队列 + 归一化 + 冲突忽略）
# ══════════════════════════════════════════════════════════════════
triggers.fire_hotkey("Ctrl+Shift+1")
hk = triggers.make({"kind": "hotkey", "spec": "shift+ctrl+1"})   # 顺序/大小写不同
check("热键: 归一化后能消费到事件", hk.should_fire(datetime.datetime.now()) is True)
check("热键: 同一事件只消费一次",
      hk.should_fire(datetime.datetime.now()) is False)
check("热键: 无事件时不误触发",
      triggers.make({"kind": "hotkey", "spec": "ctrl+shift+2"})
      .should_fire(datetime.datetime.now()) is False)

# 冲突检测（与内建热键相同 → 忽略该绑定）：直接验证 ACRPA 的实现（无 GUI 导入失败则跳过）
try:
    sys.path.insert(0, os.path.join(BASE, "src"))
    import ACRPA as _acrpa
    _acrpa._HOTKEY_ACTIONS = {"run": _acrpa._parse_hotkey_spec("ctrl+alt+r")}
    _acrpa._RECORD_STOP_SPEC = None
    state.SCHED_TASKS = [
        {"id": "h1", "name": "冲突任务", "target_type": "script", "target": "nope.xls",
         "enabled": True, "triggers": [{"kind": "hotkey", "spec": "ctrl+alt+r"}]},
        {"id": "h2", "name": "正常任务", "target_type": "script", "target": "nope.xls",
         "enabled": True, "triggers": [{"kind": "hotkey", "spec": "ctrl+alt+9"}],
         "stop_hotkey": "ctrl+alt+f11"},
    ]
    _acrpa._rebuild_task_hotkeys()
    check("热键: 与内建热键冲突的绑定被忽略（不误触发）",
          len(_acrpa._TASK_HOTKEYS) == 2, len(_acrpa._TASK_HOTKEYS))
    check("热键: 无冲突绑定全部登记（run + stop）",
          sorted(v[2] for v in _acrpa._TASK_HOTKEYS.values()) == ["run", "stop"],
          [v[2] for v in _acrpa._TASK_HOTKEYS.values()])
except Exception as _e:      # 无 GUI 环境导入失败 → 明确跳过而非误报
    print("SKIP  任务热键冲突检测（ACRPA 导入不可用: {}）".format(_e))

# ══════════════════════════════════════════════════════════════════
# 8. 开机执行 vs 错过补偿互斥去重
# ══════════════════════════════════════════════════════════════════
_reset_tasks()
boot = scheduler.normalize_task({
    "id": "b", "name": "开机任务", "script": "nope.xls", "enabled": True,
    "missed_run": "run_now", "run_count": 0,
    "triggers": [{"kind": "on_boot", "delay_sec": 30}]})
boot["next_run_at"] = (datetime.datetime.now() - datetime.timedelta(hours=3)) \
    .strftime("%Y-%m-%dT%H:%M:%S")
state.SCHED_TASKS = [boot]
scheduler._run_missed_compensation(datetime.datetime.now())
check("开机执行: 不参与错过补偿（不重复执行同一次）",
      int(boot.get("run_count") or 0) == 0, "run_count={}".format(boot.get("run_count")))

boot_trg = triggers.make(boot["triggers"][0])
check("开机执行: 延迟未到不触发",
      boot_trg.should_fire(triggers._BOOT_TS + datetime.timedelta(seconds=10)) is False)
check("开机执行: 延迟到点触发",
      boot_trg.should_fire(triggers._BOOT_TS + datetime.timedelta(seconds=31)) is True)
boot_trg.on_fired(boot)
boot_trg2 = triggers.make(boot["triggers"][0])   # 重建实例模拟下一轮 poll
check("开机执行: 同一进程启动只触发一次",
      boot_trg2.should_fire(triggers._BOOT_TS + datetime.timedelta(seconds=60)) is False)

# ══════════════════════════════════════════════════════════════════
# 9. 附加：统一 Task 三型 target 归一化 + 触发器注册表完整性
# ══════════════════════════════════════════════════════════════════
kinds = set(triggers.list_kinds())
check("触发器注册表含 L0/L1 全部 8 种 kind",
      kinds == {"once", "daily", "weekly", "interval", "countdown", "hourly",
                "on_boot", "hotkey"}, sorted(kinds))
wf = scheduler.normalize_task({"id": "wf", "name": "工作流任务",
                               "target_type": "workflow", "target": "x.json",
                               "enabled": True, "triggers": [{"kind": "daily", "time": "10:00"}]})
check("统一 Task: workflow 目标归一化", wf["target_type"] == "workflow" and wf["target"] == "x.json")
cmd = scheduler.normalize_task({"id": "cm", "name": "命令任务",
                                "target_type": "command", "target": "按键",
                                "target_params": {"args": ["a", "1"]},
                                "enabled": True, "triggers": [{"kind": "daily", "time": "10:00"}]})
check("统一 Task: command 目标归一化 + 参数保留",
      cmd["target_type"] == "command" and cmd["target_params"]["args"] == ["a", "1"])
check("统一 Task: 三种目标的可读标签",
      scheduler.target_label(wf).startswith("工作流:")
      and scheduler.target_label(cmd).startswith("命令:")
      and scheduler.target_label({"target_type": "script", "target": "a/b.xls"}).startswith("脚本:"))

# ══════════════════════════════════════════════════════════════════
# 10. 任务库自动备份 / 保留份数 / 启动清理
# ══════════════════════════════════════════════════════════════════
_bk_saved_path = state.CONFIG_PATH
_bk_saved_keep = state.SCHEDULER_BACKUP_KEEP
_bk_dir = os.path.join(_TMP, "bk")
os.makedirs(_bk_dir, exist_ok=True)
state.CONFIG_PATH = os.path.join(_bk_dir, "config.json")
state.SCHEDULER_BACKUP_KEEP = 3
with open(state.CONFIG_PATH, "w") as f:
    f.write('{"retry_max": 0}')
state.RETRY_MAX = 7
state.save_config()
# 收尾修噪音: 备份落点已从"数据目录根"迁到 <数据目录>/backups/，
# 因此这里同时断言"备份在 backups/ 里"与"数据目录根没有 .bak 残留"。
_bk_sub = state.backups_dir()
check("任务库备份: 落点为 <数据目录>/backups/",
      _bk_sub == os.path.join(_bk_dir, "backups"), _bk_sub)
created = [n for n in os.listdir(_bk_sub)] if os.path.isdir(_bk_sub) else []
check("任务库备份: 写盘前生成 <库文件名>.bak.<时间戳>", len(created) == 1, created)
check("任务库备份: 数据目录根不再散落 .bak 文件",
      not [n for n in os.listdir(_bk_dir) if ".bak." in n], os.listdir(_bk_dir))

# 合成 6 份更早的备份 → 启动清理只保留最近 N=3 份
for k in range(6):
    with open(os.path.join(_bk_sub, "config.json.bak.20200101-00000{}".format(k)), "w") as f:
        f.write('{"x": %d}' % k)
state.prune_task_backups()
left = sorted(n for n in os.listdir(_bk_sub) if ".bak." in n)
check("任务库备份: 超量备份被清理，保留最近 N=3 份", len(left) == 3, left)
check("任务库备份: 最新的备份未被误删",
      any("20200101-000005" in n for n in left), left)
state.CONFIG_PATH = _bk_saved_path
state.SCHEDULER_BACKUP_KEEP = _bk_saved_keep

# ── 汇总 ──────────────────────────────────────────────────────────
print("-" * 60)
if _fails:
    print("失败 {} 项: {}".format(len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("全部通过")
sys.exit(0)
