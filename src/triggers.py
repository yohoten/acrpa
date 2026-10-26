"""触发器插件化 — Trigger 基类 + 注册表 + 内建触发器 (L0-③ / L1 P0)。

设计约束
--------
* **纯标准库**，不 import tkinter，import 期零副作用（不读文件 / 不起线程 / 不写日志）。
* 触发器实例只持有 `task["triggers"][i]` 那份 dict 的**同一个对象**，因此需要
  "首次解析即落库"的字段（倒计时 `due_at`、开机 `last_boot`、一次性 `fired_at`）
  直接写 spec，持久化由 `scheduler` 统一负责（它拿着同一份 task）。
* 组合语义：`task["triggers"]` 内多个触发器是 **或** 关系（任一到期即触发）；
  时间段过滤 / 生效起止 / 次数上限是 **且** 关系，由 scheduler 统一判定。

对外契约（L2~L5 子任务请勿改动，只允许"新增"）
--------------------------------------------
    Trigger.kind / label / one_shot / time_based
    Trigger(spec)                              构造（spec 至少含 {"kind": ...}）
    .describe() -> str                         任务列表「计划」列的人类可读文本
    .next_run(now, task=None) -> datetime|None 时间型：下次到期时间（None=无计划）
    .should_fire(now, task=None) -> bool       到期/事件判定（主循环唯一决策点）
    .on_fired(task, now=None) -> None          触发后的状态回写（可选重写）
    .poll(now) -> None                         事件型预留钩子（本层未使用）

    register(cls) / get(kind) / make(spec) / list_kinds() / list_infos()
    parse_dt(value) -> datetime|None
    legacy_trigger(period_type, period_value) -> dict      旧 4 种 period_type → spec
    next_run_at_for(task, now=None) -> datetime|None       多触发器取最早
    describe_task(task) -> str                             多触发器合并文本
    has_boot_trigger(task) -> bool                         开机触发器存在性（去重判定）
    fire_hotkey(spec_text) / consume_hotkey(spec_text)     热键事件队列（线程安全）
    from_text(kind, text, **opts) -> dict                  UI 文本 → spec
"""
import datetime
import os
import threading

# ── 时间格式常量 ────────────────────────────────────────────────────
_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
_DT_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
               "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                 "%Y-%m-%dT%H:%M:%S")

# 进程启动时刻 / 令牌 —— 开机触发器"同一启动只触发一次"的判定依据。
_BOOT_TS = datetime.datetime.now()
_BOOT_TOKEN = "{}-{}".format(_BOOT_TS.strftime("%Y%m%d-%H%M%S"), os.getpid())

_WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")


# ══════════════════════════════════════════════════════════════════════
# 通用解析工具
# ══════════════════════════════════════════════════════════════════════
def parse_dt(value):
    """任意时间表示 → datetime；无效/空返回 None（绝不抛）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:  # 兜底: 带微秒/时区偏移的 ISO 串
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def parse_date(value):
    """任意日期表示 → date；无效/空返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    dt = parse_dt(s)
    return dt.date() if dt else None


def _int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _parse_hm(text, default=(9, 0)):
    """'09:30' → (9, 30)；无效返回 default。"""
    try:
        parts = str(text).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return default


def _parse_int_list(text):
    """'9,12,18' / [9,12,18] → [9,12,18]（去重升序，忽略非法项）。"""
    out = []
    if isinstance(text, (list, tuple)):
        items = list(text)
    else:
        items = str(text or "").replace(";", ",").replace("，", ",").split(",")
    for it in items:
        try:
            out.append(int(float(str(it).strip())))
        except Exception:
            continue
    return sorted(set(out))


def _fmt_seconds(secs):
    """秒数 → 人类可读（90 → '1分30秒'）。"""
    secs = int(secs)
    if secs <= 0:
        return "0秒"
    if secs < 60:
        return "{}秒".format(secs)
    if secs < 3600:
        return "{}分{}秒".format(secs // 60, secs % 60) if secs % 60 else "{}分".format(secs // 60)
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    parts = ["{}小时".format(h)]
    if m:
        parts.append("{}分".format(m))
    if s:
        parts.append("{}秒".format(s))
    return "".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════════
_REGISTRY = {}


def register(cls):
    """注册触发器类（可作装饰器使用）。kind 重复时后者覆盖（便于插件替换）。"""
    kind = str(getattr(cls, "kind", "") or "")
    if kind and kind != "base":
        _REGISTRY[kind] = cls
    return cls


def get(kind):
    """→ 触发器类（调用方自行实例化）；未注册返回 None。"""
    return _REGISTRY.get(str(kind or ""))


def make(spec):
    """spec dict → 触发器实例；未注册/非法返回 None（绝不抛）。"""
    if not isinstance(spec, dict):
        return None
    cls = get(spec.get("kind"))
    if cls is None:
        return None
    try:
        return cls(spec)
    except Exception:
        return None


def list_kinds():
    """→ 已注册的 kind 列表（注册顺序稳定）。"""
    return list(_REGISTRY.keys())


def list_infos():
    """→ [(kind, 显示名)]；供任务管理器下拉使用。"""
    out = []
    for kind, cls in _REGISTRY.items():
        out.append((kind, str(getattr(cls, "label", kind))))
    return out


# ══════════════════════════════════════════════════════════════════════
# Trigger 基类
# ══════════════════════════════════════════════════════════════════════
class Trigger:
    """触发器基类。

    kind        唯一标识（注册键）
    label       显示名
    one_shot    True = 触发一次后任务自动停用（一次性 / 倒计时）
    time_based  True = 参与 next_run_at 合并计算；False = 纯事件型（热键）
    """

    kind = "base"
    label = "触发器"
    one_shot = False
    time_based = True

    def __init__(self, spec=None):
        self.spec = spec if isinstance(spec, dict) else {}

    # ── 元信息 ──
    def describe(self):
        """任务列表「计划」列的人类可读文本。"""
        return self.label

    # ── 判定 ──
    def next_run(self, now, task=None):
        """时间型：下次到期时间；None = 无计划（事件型默认 None）。"""
        return None

    def should_fire(self, now, task=None):
        """默认：比对任务已落库的 next_run_at（覆盖者实现自己的事件判定）。"""
        due = parse_dt(_task_next_run_at(task))
        return bool(due is not None and now >= due)

    def on_fired(self, task, now=None):
        """触发后的状态回写（默认空实现）。"""
        return None

    def poll(self, now):
        """事件型预留钩子（本层未使用，保持接口稳定）。"""
        return None


def _task_next_run_at(task):
    """从任务读 next_run_at（兼容旧的 next_run 字段）。"""
    if not isinstance(task, dict):
        return None
    return task.get("next_run_at") or task.get("next_run")


# ══════════════════════════════════════════════════════════════════════
# 时间型触发器
# ══════════════════════════════════════════════════════════════════════
@register
class OnceTrigger(Trigger):
    """一次性：到达指定时刻执行一次后自动停用。"""

    kind = "once"
    label = "一次性"
    one_shot = True

    def at(self):
        return parse_dt(self.spec.get("at"))

    def describe(self):
        if self.spec.get("fired_at"):
            return "一次(已触发)"
        dt = self.at()
        return "一次 {}".format(dt.strftime("%m-%d %H:%M")) if dt else "一次(未设时刻)"

    def next_run(self, now, task=None):
        if self.spec.get("fired_at"):
            return None
        return self.at()          # 允许落在过去 → 交给 missed_run 策略处理

    def should_fire(self, now, task=None):
        if self.spec.get("fired_at"):
            return False
        dt = self.at()
        return bool(dt is not None and now >= dt)

    def on_fired(self, task, now=None):
        self.spec["fired_at"] = (now or datetime.datetime.now()).strftime(_ISO_FMT)


@register
class DailyTrigger(Trigger):
    """每天 HH:MM。"""

    kind = "daily"
    label = "每天"

    def _hm(self):
        return _parse_hm(self.spec.get("time"), (9, 0))

    def describe(self):
        h, m = self._hm()
        return "每天 {:02d}:{:02d}".format(h, m)

    def next_run(self, now, task=None):
        h, m = self._hm()
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= now:
            cand += datetime.timedelta(days=1)
        return cand


@register
class WeeklyTrigger(Trigger):
    """每周指定星期（0=周一 … 6=周日）的 HH:MM。"""

    kind = "weekly"
    label = "每周"

    def _days(self):
        days = _parse_int_list(self.spec.get("days"))
        days = [d for d in days if 0 <= d <= 6]
        return days or [0]

    def _hm(self):
        return _parse_hm(self.spec.get("time"), (9, 0))

    def describe(self):
        h, m = self._hm()
        names = ",".join(_WEEKDAY_NAMES[d] for d in self._days())
        return "每周{} {:02d}:{:02d}".format(names, h, m)

    def next_run(self, now, task=None):
        h, m = self._hm()
        days = self._days()
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= now:
            cand += datetime.timedelta(days=1)
        for _ in range(7):
            if cand.weekday() in days:
                return cand
            cand += datetime.timedelta(days=1)
        return cand


@register
class IntervalTrigger(Trigger):
    """固定间隔（秒）。next_run 语义 = "从当前时刻起再等 N 秒"，与旧实现一致。"""

    kind = "interval"
    label = "固定间隔"

    def seconds(self):
        return max(1, _int(self.spec.get("seconds"), 3600))

    def describe(self):
        return "每{}".format(_fmt_seconds(self.seconds()))

    def next_run(self, now, task=None):
        return now + datetime.timedelta(seconds=self.seconds())


@register
class CountdownTrigger(Trigger):
    """倒计时：首次解析时定 `due_at`，到点执行一次后任务自动停用。"""

    kind = "countdown"
    label = "倒计时"
    one_shot = True

    def total_seconds(self):
        if self.spec.get("seconds") not in (None, ""):
            return max(1, _int(self.spec.get("seconds"), 60))
        h = _int(self.spec.get("hours"), 0)
        m = _int(self.spec.get("minutes"), 0)
        s = _int(self.spec.get("secs"), 0)
        total = h * 3600 + m * 60 + s
        return max(1, total)

    def _due(self, now):
        """返回到期时刻；首次调用时把 due_at 落到 spec（由 scheduler 持久化）。"""
        due = parse_dt(self.spec.get("due_at"))
        if due is None:
            due = now + datetime.timedelta(seconds=self.total_seconds())
            self.spec["due_at"] = due.strftime(_ISO_FMT)
        return due

    def describe(self):
        if self.spec.get("fired_at"):
            return "倒计时(已触发)"
        due = parse_dt(self.spec.get("due_at"))
        if due is None:
            return "倒计时 {}".format(_fmt_seconds(self.total_seconds()))
        left = int((due - datetime.datetime.now()).total_seconds())
        if left <= 0:
            return "倒计时(到点待触发)"
        return "倒计时 剩{}".format(_fmt_seconds(left))

    def next_run(self, now, task=None):
        if self.spec.get("fired_at"):
            return None
        return self._due(now)

    def should_fire(self, now, task=None):
        if self.spec.get("fired_at"):
            return False
        due = self._due(now)
        return bool(due is not None and now >= due)

    def on_fired(self, task, now=None):
        self.spec["fired_at"] = (now or datetime.datetime.now()).strftime(_ISO_FMT)


@register
class HourlyTrigger(Trigger):
    """整点 / 半点：hours 小时白名单 + mode(full/half/both)，可选显式 minute。"""

    kind = "hourly"
    label = "整点/半点"

    def _minutes(self):
        if self.spec.get("minute") not in (None, ""):
            m = _int(self.spec.get("minute"), 0)
            return [m] if 0 <= m <= 59 else [0]
        mode = str(self.spec.get("mode") or "full").strip().lower()
        if mode == "half":
            return [30]
        if mode == "both":
            return [0, 30]
        return [0]

    def _hours(self):
        hours = [h for h in _parse_int_list(self.spec.get("hours")) if 0 <= h <= 23]
        return hours or list(range(24))

    def describe(self):
        mode = str(self.spec.get("mode") or "full").strip().lower()
        tag = "整点/半点" if mode == "both" else ("半点" if mode == "half" else "整点")
        hours = self._hours()
        if len(hours) >= 24:
            return "每小时{}".format(tag)
        return "每天 {} 点{}".format(",".join(str(h) for h in hours), tag)

    def next_run(self, now, task=None):
        best = None
        for h in self._hours():
            for m in self._minutes():
                cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if cand <= now:
                    cand += datetime.timedelta(days=1)
                if best is None or cand < best:
                    best = cand
        return best


@register
class OnBootTrigger(Trigger):
    """开机 / 程序启动后延迟 N 秒触发一次（同一进程启动只触发一次）。"""

    kind = "on_boot"
    label = "开机/启动执行"

    def delay_sec(self):
        d = _int(self.spec.get("delay_sec"), 30)
        return max(0, d)

    def _due(self):
        return _BOOT_TS + datetime.timedelta(seconds=self.delay_sec())

    def _already_fired(self):
        return self.spec.get("last_boot") == _BOOT_TOKEN

    def describe(self):
        if self._already_fired():
            return "开机执行(已触发)"
        return "开机后延迟{}执行".format(_fmt_seconds(self.delay_sec()))

    def next_run(self, now, task=None):
        if self._already_fired():
            return None
        return self._due()

    def should_fire(self, now, task=None):
        if self._already_fired():
            return False
        return now >= self._due()

    def on_fired(self, task, now=None):
        self.spec["last_boot"] = _BOOT_TOKEN


# ══════════════════════════════════════════════════════════════════════
# 事件型触发器：任务级热键
# ══════════════════════════════════════════════════════════════════════
_HOTKEY_LOCK = threading.Lock()
_HOTKEY_PENDING = {}   # {归一化热键串: 待消费次数}


def _norm_hotkey(spec_text):
    """归一化热键串：小写、去空格、按键名排序（'Ctrl+Shift+1' ≡ 'shift+ctrl+1'）。"""
    parts = [p.strip().lower() for p in str(spec_text or "").split("+") if p.strip()]
    return "+".join(sorted(parts))


def fire_hotkey(spec_text):
    """登记一次热键触发（由热键轮询线程调用，不触碰 Tk）。"""
    key = _norm_hotkey(spec_text)
    if not key:
        return
    with _HOTKEY_LOCK:
        _HOTKEY_PENDING[key] = _HOTKEY_PENDING.get(key, 0) + 1


def consume_hotkey(spec_text):
    """消费一次热键触发 → True 表示确有事件待处理。"""
    key = _norm_hotkey(spec_text)
    if not key:
        return False
    with _HOTKEY_LOCK:
        n = _HOTKEY_PENDING.get(key, 0)
        if n <= 0:
            return False
        if n == 1:
            _HOTKEY_PENDING.pop(key, None)
        else:
            _HOTKEY_PENDING[key] = n - 1
        return True


@register
class HotkeyTrigger(Trigger):
    """任务级热键：由宿主 200ms 轮询登记事件，调度循环消费。"""

    kind = "hotkey"
    label = "热键"
    time_based = False

    def spec_text(self):
        return str(self.spec.get("spec") or self.spec.get("hotkey") or "").strip()

    def describe(self):
        text = self.spec_text()
        return "热键 {}".format(text) if text else "热键(未设置)"

    def next_run(self, now, task=None):
        return None

    def should_fire(self, now, task=None):
        text = self.spec_text()
        if not text:
            return False
        return consume_hotkey(text)


# ══════════════════════════════════════════════════════════════════════
# 任务级辅助函数
# ══════════════════════════════════════════════════════════════════════
def _specs(task):
    if not isinstance(task, dict):
        return []
    specs = task.get("triggers")
    return specs if isinstance(specs, list) else []


def next_run_at_for(task, now=None):
    """→ 所有时间型触发器里最早的下次运行时间；无时间型触发器返回 None。"""
    now = now or datetime.datetime.now()
    best = None
    for spec in _specs(task):
        trg = make(spec)
        if trg is None or not getattr(trg, "time_based", True):
            continue
        try:
            nxt = trg.next_run(now, task)
        except Exception:
            nxt = None
        if nxt is not None and (best is None or nxt < best):
            best = nxt
    return best


def describe_task(task):
    """→ 多触发器合并的人类可读文本（'每天 09:00 + 热键 ctrl+1'）。"""
    parts = []
    for spec in _specs(task):
        trg = make(spec)
        if trg is None:
            continue
        try:
            parts.append(trg.describe())
        except Exception:
            parts.append(str(spec.get("kind") or "?"))
    return " + ".join(parts) if parts else "未设置"


def has_boot_trigger(task):
    """任务是否含 on_boot 触发器（用于"开机执行 vs 错过补偿"互斥去重）。"""
    for spec in _specs(task):
        if str(spec.get("kind") or "") == "on_boot":
            return True
    return False


def trigger_for_due(task, due, now=None):
    """找到"本次到期"的触发器实例（时间型且 next_run 最接近 due）；找不到返回 None。"""
    now = now or datetime.datetime.now()
    best, best_gap = None, None
    for spec in _specs(task):
        trg = make(spec)
        if trg is None or not getattr(trg, "time_based", True):
            continue
        try:
            cand = trg.next_run(now, task)
        except Exception:
            cand = None
        if cand is None:
            continue
        gap = abs((cand - due).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = trg, gap
    return best


# ── 旧 4 种 period_type → trigger spec（向后兼容的唯一转换点）──────────
def legacy_trigger(period_type, period_value):
    """旧任务字段 (period_type/period_value) → 等价 trigger spec。"""
    pt = str(period_type or "daily").strip().lower()
    pv = "" if period_value is None else str(period_value).strip()

    if pt == "once":
        return {"kind": "once", "at": pv}
    if pt == "interval":
        return {"kind": "interval", "seconds": max(1, _int(pv, 3600))}
    if pt == "weekly":
        days, time_text = [], "09:00"
        if "|" in pv:
            left, right = pv.split("|", 1)
            days = [d for d in _parse_int_list(left) if 0 <= d <= 6]
            time_text = right.strip() or "09:00"
        else:
            days = [d for d in _parse_int_list(pv) if 0 <= d <= 6]
        return {"kind": "weekly", "days": days or [0], "time": time_text}
    if pt == "countdown":
        return {"kind": "countdown", "seconds": max(1, _int(pv, 60))}
    if pt == "hourly":
        return {"kind": "hourly", "mode": "full",
                "hours": [h for h in _parse_int_list(pv) if 0 <= h <= 23]}
    if pt == "on_boot":
        return {"kind": "on_boot", "delay_sec": max(0, _int(pv, 30))}
    if pt == "hotkey":
        return {"kind": "hotkey", "spec": pv}
    # daily 及未知值 → 每天 HH:MM（旧实现的兜底语义）
    return {"kind": "daily", "time": pv or "09:00"}


def default_trigger():
    """新建任务的缺省触发器（每天 09:00，与旧模型默认值一致）。"""
    return {"kind": "daily", "time": "09:00"}


# ── UI 文本 → spec（任务管理器表单用；解析失败一律给安全默认，绝不抛）──
def from_text(kind, text, **opts):
    """把任务管理器里的一行文本框解析成 trigger spec。

    kind: once / daily / weekly / interval / countdown / hourly / hotkey / on_boot
    text: 与 kind 对应的文本（见任务管理器表单的动态提示）
    opts: 附加项（目前用到 hourly 的 mode、on_boot 的 delay_sec、hotkey 的 spec）
    """
    kind = str(kind or "daily").strip().lower()
    text = "" if text is None else str(text).strip()

    if kind == "once":
        return {"kind": "once", "at": text}
    if kind == "interval":
        return {"kind": "interval", "seconds": max(1, _int(text, 3600))}
    if kind == "weekly":
        return legacy_trigger("weekly", text)
    if kind == "countdown":
        if ":" in text:      # H:M:S 三元组
            parts = [p for p in text.split(":")]
            vals = [_int(p, 0) for p in parts[:3]]
            while len(vals) < 3:
                vals.insert(0, 0)
            return {"kind": "countdown", "hours": vals[0],
                    "minutes": vals[1], "secs": vals[2]}
        return {"kind": "countdown", "seconds": max(1, _int(text, 60))}
    if kind == "hourly":
        return {"kind": "hourly",
                "mode": str(opts.get("mode") or "full").strip().lower(),
                "hours": [h for h in _parse_int_list(text) if 0 <= h <= 23]}
    if kind == "hotkey":
        return {"kind": "hotkey", "spec": text or str(opts.get("spec") or "")}
    if kind == "on_boot":
        return {"kind": "on_boot", "delay_sec": max(0, _int(text, 30))}
    if kind == "daily":
        return {"kind": "daily", "time": text or "09:00"}
    return legacy_trigger(kind, text)
