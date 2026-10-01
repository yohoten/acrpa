"""acrpa_api.py — 提供给自定义 Python 脚本的 AcrpaAPI 门面。

设计要点
--------
* 本模块**不在顶层 import engine**（避免循环依赖与打包顺序问题）。engine 通过
  构造参数 engine_module 注入，或在方法内惰性 `import engine`。
* AcrpaAPI **有意直调 engine 的既有私有/公有方法**（_coord/_input/_key/_wait/
  _hover/_find_cached/_write/_hotkey/execute 等），以保持与内置命令 100% 一致的
  行为——这是**有意引入的耦合**：若 engine 私有方法签名变更，需同步本模块。
* 每个方法整体 try/except：失败返回 None/False，并 log 一条 warning，绝不抛出。

对外接口
--------
    API_VERSION
    ACRPA_API_CAPS  # {"levels": [...], "methods": [...]} 供插件做能力声明
    class AcrpaAPI(engine_module=None, exec_ctx=None)
"""
import time

API_VERSION = 1

# 供插件做能力声明的模块级常量（单一来源）
ACRPA_API_CAPS = {
    "levels": ["sandbox", "trusted", "full"],
    "methods": ["log", "click", "type_text", "key_press", "find_image",
                "sleep", "wait", "move_to", "screenshot", "get_var", "set_var",
                "get_row", "run_command", "read_excel_cell"],
}


def _log(msg, level="info"):
    """尽力写日志；utils 不可用时静默。"""
    try:
        from utils import log1
        log1("[py] " + str(msg), level)
    except Exception:
        pass


class _Cell(object):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _Row(object):
    """轻量行适配器：使 engine 的 row[i].value 私有方法可直接复用。"""

    __slots__ = ("cells",)

    def __init__(self, values):
        self.cells = [_Cell(v) for v in values]

    def __getitem__(self, index):
        if index < len(self.cells):
            return self.cells[index]
        return _Cell(None)


class _FakeScript(object):
    """engine.execute() 需要带 cmd_type/args 属性的对象（会被 RowAdapter 包装）。"""

    def __init__(self, cmd_type, args):
        self.cmd_type = cmd_type
        self.args = list(args or [])


class AcrpaAPI(object):
    """自定义脚本可用的能力门面。所有方法失败返回 None/False，绝不抛出。"""

    def __init__(self, engine_module=None, exec_ctx=None):
        self._injected = engine_module
        self._ctx = exec_ctx if isinstance(exec_ctx, dict) else {}
        self._cached_mod = None

    # ── 内部解析 ──
    def _module(self):
        """惰性 import engine 模块（本模块顶层不 import engine）。"""
        if self._cached_mod is None:
            try:
                import engine as _m
                self._cached_mod = _m
            except Exception:
                self._cached_mod = False
        return self._cached_mod or None

    def _engine_obj(self):
        """返回 engine 单例实例：注入对象优先，其次惰性 import 的 engine.engine。"""
        inj = self._injected
        if inj is None:
            m = self._module()
            return getattr(m, "engine", None) if m is not None else None
        # 注入的是模块 → 取模块里的 engine 单例；注入的是实例 → 直接用
        if not hasattr(inj, "_coord") and hasattr(inj, "engine"):
            return getattr(inj, "engine")
        return inj

    def _pa(self):
        """获取 pyautogui（经 engine.get_pyautogui 惰性加载）。"""
        eng = self._engine_obj()
        f = getattr(eng, "get_pyautogui", None)
        if callable(f):
            try:
                return f()
            except Exception:
                return None
        m = self._module()
        f = getattr(m, "get_pyautogui", None) if m is not None else None
        if callable(f):
            try:
                return f()
            except Exception:
                return None
        return None

    def _call_engine(self, method_name, values, label):
        """直调 engine 私有命令方法（有意耦合，见模块 docstring）。"""
        try:
            eng = self._engine_obj()
            method = getattr(eng, method_name, None)
            if not callable(method):
                _log("能力不可用: %s" % label, "warning")
                return False
            method(_Row(values), self._ctx.get("script_dir", ""))
            return True
        except Exception as e:
            _log("%s 失败: %s" % (label, e), "warning")
            return False

    # ── 基础 ──
    def log(self, msg, level="info"):
        try:
            _log(msg, level)
            return True
        except Exception:
            return False

    def click(self, x, y):
        """点击屏幕坐标（直调 engine._coord，行为同「坐标」命令）。"""
        try:
            return self._call_engine(
                "_coord", ["坐标", int(x), int(y), "左", 1, 0.1], "click")
        except Exception as e:
            _log("click 失败: %s" % e, "warning")
            return False

    def type_text(self, text):
        """逐字输入文本（直调 engine._write，支持 DD 驱动回退）。"""
        try:
            return self._call_engine("_write", ["写入", str(text), 0.05, "auto"],
                                     "type_text")
        except Exception as e:
            _log("type_text 失败: %s" % e, "warning")
            return False

    def key_press(self, key):
        """按键；含 '+' 视为组合键（直调 engine._key / _hotkey）。"""
        try:
            s = str(key)
            if "+" in s:
                parts = s.split("+", 1)
                return self._call_engine("_hotkey", ["热键", parts[0], parts[1]],
                                         "key_press")
            return self._call_engine("_key", ["按键", s, 1, 0.1], "key_press")
        except Exception as e:
            _log("key_press 失败: %s" % e, "warning")
            return False

    def find_image(self, path, timeout=None):
        """在屏幕上找图，返回 (x, y) 或 None（直调 engine._find_cached）。"""
        try:
            eng = self._engine_obj()
            finder = getattr(eng, "_find_cached", None)
            if not callable(finder):
                _log("find_image 不可用（engine._find_cached 缺失）", "warning")
                return None
            deadline = None
            if timeout is not None:
                try:
                    deadline = time.time() + float(timeout)
                except (TypeError, ValueError):
                    deadline = None
            while True:
                try:
                    loc = finder(str(path), 0.96)
                except Exception:
                    loc = None
                if loc is not None:
                    try:
                        return (int(loc[0]), int(loc[1]))
                    except Exception:
                        try:
                            return (int(loc.x), int(loc.y))
                        except Exception:
                            return None
                if deadline is None or time.time() >= deadline:
                    return None
                time.sleep(0.5)
        except Exception as e:
            _log("find_image 失败: %s" % e, "warning")
            return None

    def sleep(self, sec):
        try:
            time.sleep(max(0.0, float(sec)))
            return True
        except Exception:
            return False

    def wait(self, sec):
        """等待（直调 engine._wait，暂停/停止感知）。"""
        try:
            return self._call_engine("_wait", ["等待", sec], "wait")
        except Exception as e:
            _log("wait 失败: %s" % e, "warning")
            return False

    def move_to(self, x, y):
        """鼠标移动到坐标（直调 engine._hover）。"""
        try:
            return self._call_engine("_hover", ["悬停", int(x), int(y), 0.1],
                                     "move_to")
        except Exception as e:
            _log("move_to 失败: %s" % e, "warning")
            return False

    def screenshot(self, path=None):
        """截图。指定 path 时保存并返回该路径；未指定返回 None。"""
        try:
            if not path:
                return None
            pa = self._pa()
            if pa is None:
                _log("screenshot 不可用（pyautogui 缺失）", "warning")
                return None
            pa.screenshot(str(path))
            return str(path)
        except Exception as e:
            _log("screenshot 失败: %s" % e, "warning")
            return None

    # ── 变量 ──
    def get_var(self, name):
        try:
            eng = self._engine_obj()
            variables = getattr(eng, "variables", None)
            if isinstance(variables, dict):
                return variables.get(str(name))
            return None
        except Exception as e:
            _log("get_var 失败: %s" % e, "warning")
            return None

    def set_var(self, name, value):
        try:
            eng = self._engine_obj()
            variables = getattr(eng, "variables", None)
            if isinstance(variables, dict):
                variables[str(name)] = value
                return True
            return False
        except Exception as e:
            _log("set_var 失败: %s" % e, "warning")
            return False

    def get_row(self):
        """返回当前行上下文 dict（来自 exec_ctx['row']）。"""
        try:
            r = self._ctx.get("row")
            if isinstance(r, dict):
                return dict(r)
            return {"row": r if r is not None else 0}
        except Exception:
            return {}

    # ── 命令复用 ──
    def run_command(self, cmd_type, args):
        """复用 engine 的命令执行入口（engine.execute）执行任意内置命令。"""
        try:
            eng = self._engine_obj()
            execute = getattr(eng, "execute", None)
            if not callable(execute):
                _log("run_command 不可用（engine.execute 缺失）", "warning")
                return False
            row = _FakeScript(str(cmd_type), list(args or []))
            res = execute(row, self._ctx.get("script_dir", ""))
            return bool(res)
        except Exception as e:
            _log("run_command 失败: %s" % e, "warning")
            return False

    def read_excel_cell(self, path, sheet, row, col):
        """读取 Excel 单元格（薄封装 xlrd）。失败返回 None。"""
        try:
            import xlrd
            bk = xlrd.open_workbook(str(path))
            try:
                sh = bk.sheet_by_name(sheet)
            except Exception:
                sh = bk.sheet_by_index(int(sheet or 0))
            return sh.cell_value(int(row), int(col))
        except Exception as e:
            _log("read_excel_cell 失败: %s" % e, "warning")
            return None

    @property
    def available(self):
        """当前可用能力名列表。"""
        names = []
        try:
            for name in ACRPA_API_CAPS["methods"]:
                if callable(getattr(self, name, None)):
                    names.append(name)
        except Exception:
            pass
        return names
