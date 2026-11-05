"""
Workflow Engine — 多脚本串联工作流编排

功能:
- 多脚本顺序/并行执行
- 条件分支 (if/then/else)
- 变量跨脚本共享
- YAML/JSON 工作流定义解析

依赖: engine.py, scriptdata.py, safe_eval.py, state.py, utils.py
"""
import os, sys, time, json, threading, datetime, itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
import state
from core.log import log1


# ── 工作流步骤类型 ──
STEP_SCRIPT = "script"
STEP_PARALLEL = "parallel"
STEP_CONDITION = "condition"
STEP_WAIT = "wait"
STEP_RETRY = "retry"
STEP_WORKFLOW = "workflow"          # 子工作流引用

# 子工作流引用最大嵌套深度 (防 A→B→A 循环引用之外的深度失控)
_WF_MAX_DEPTH = 16


class WorkflowError(Exception):
    """工作流执行错误"""
    pass


class WorkflowParseError(WorkflowError):
    """工作流定义解析错误"""
    pass


# ======================================================================
# 工作流定义解析
# ======================================================================

def _load_xls_script(filepath):
    """加载 .xls 脚本为 ScriptData 列表"""
    import xlrd
    from scriptdata import ScriptData

    if not os.path.exists(filepath):
        raise WorkflowError("脚本文件不存在: {}".format(filepath))

    wb = xlrd.open_workbook(filepath)
    s1 = wb.sheet_by_index(0)
    rows = []
    for row_idx in range(2, s1.nrows):
        row = s1.row_values(row_idx)
        if not row or not row[0]:
            continue
        cmd_type = str(row[0]) if row[0] else ""
        args = [str(cell) if cell else "" for cell in row[1:10]]
        while len(args) < 9:
            args.append("")
        rows.append(ScriptData(cmd_type, args[:9]))
    wb.release_resources()
    return rows


def parse_workflow(filepath):
    """
    解析工作流定义文件 (JSON 或 YAML)。

    支持格式:
    {
      "name": "工作流名称",
      "steps": [
        {"type": "script", "path": "script1.xls"},
        {"type": "parallel", "steps": [...]},
        {"type": "condition", "if": "${var}", "then": "script.xls", "else": "script.xls"},
        {"type": "wait", "seconds": 5}
      ]
    }

    简化条件格式也支持:
        {"type": "condition", "if": "${ok}",
         "then": {"type": "script", "path": "a.xls"},
         "else": {"type": "script", "path": "b.xls"}}

    Args:
        filepath: .json 或 .yaml 工作流文件路径

    Returns:
        dict: 解析后的工作流定义
    """
    if not os.path.exists(filepath):
        raise WorkflowParseError("工作流文件不存在: {}".format(filepath))

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".yaml", ".yml"):
        # 尝试解析 YAML（使用简易解析或依赖 pyyaml）
        try:
            import yaml
            data = yaml.safe_load(content)
        except ImportError:
            # 简易 YAML 解析：仅支持最简格式
            data = _simple_yaml_parse(content)
    else:
        data = json.loads(content)

    # 规范化步骤
    if "steps" in data:
        data["steps"] = [_normalize_step(s) for s in data["steps"]]

    return data


def _normalize_step(step):
    """规范化单个步骤：确保 type 字段存在，展开简写格式"""
    if isinstance(step, str):
        # 简写："script.xls" → {"type": "script", "path": "script.xls"}
        return {"type": "script", "path": step}

    if "path" in step and "type" not in step:
        step["type"] = "script"

    # 默认字段 (新节点类型向后兼容)
    step.setdefault("enabled", True)
    step.setdefault("comment", "")

    # 失败补偿 (rollback): 任一步骤可声明 on_error_rollback 子步骤列表
    if isinstance(step.get("on_error_rollback"), list):
        step["on_error_rollback"] = [_normalize_step(s) for s in step["on_error_rollback"]]

    if step.get("type") == "parallel" and "steps" in step:
        step["steps"] = [_normalize_step(s) for s in step["steps"]]
        # 显式输出: 仅声明 outputs 的变量按分支定义顺序合并回主作用域;
        # 缺省 None → 运行时向后兼容为「合并全部」。
        step.setdefault("outputs", None)

    if step.get("type") == "loop" and "steps" in step:
        # 循环节点: 规范化子步骤
        step["steps"] = [_normalize_step(s) for s in step["steps"]]
        step.setdefault("times", "1")

    if step.get("type") == "condition":
        # 规范化 then/else 分支
        for key in ("then", "else"):
            if key in step and isinstance(step[key], str):
                step[key] = {"type": "script", "path": step[key]}
            elif key in step and isinstance(step[key], dict):
                step[key] = _normalize_step(step[key])

    if step.get("type") == "command":
        step.setdefault("cmd", "")
        step.setdefault("params", [""] * 9)

    if step.get("type") == "variable":
        step.setdefault("var_name", "")
        step.setdefault("var_value", "")

    if step.get("type") == "log":
        step.setdefault("text", "")

    if step.get("type") == "retry" and "steps" in step:
        # 重试节点: 规范化子步骤 + 默认参数
        step["steps"] = [_normalize_step(s) for s in step["steps"]]
        step.setdefault("max_attempts", 3)
        step.setdefault("delay", 1.0)

    if step.get("type") == "workflow":
        # 子工作流引用: 默认字段
        step.setdefault("path", "")
        step.setdefault("inputs", {})
        step.setdefault("outputs", [])

    return step


def _simple_yaml_parse(content):
    """极简 YAML 解析器（仅支持工作流定义的基本格式，无 pyyaml 依赖）"""
    import re
    result = {"steps": []}
    lines = content.split("\n")
    current_step = None
    in_parallel = False
    parallel_steps = []
    current_key = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 顶层键
        m = re.match(r"^(\w+):\s*(.*)", stripped)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            if key == "name":
                result["name"] = val
            elif key == "steps":
                # 顶层 steps 标记，跳过
                pass
            elif key == "script":
                current_step = {"type": "script", "path": val}
                result["steps"].append(current_step)
            elif key == "parallel":
                in_parallel = True
                parallel_steps = []
                current_step = {"type": "parallel", "steps": parallel_steps}
                result["steps"].append(current_step)
            elif key == "condition":
                # 读取条件表达式
                condition_val = val
                current_step = {"type": "condition", "if": condition_val}
                result["steps"].append(current_step)
            elif key == "then" and current_step and current_step["type"] == "condition":
                current_step["then"] = {"type": "script", "path": val}
            elif key == "else" and current_step and current_step["type"] == "condition":
                current_step["else"] = {"type": "script", "path": val}
            elif key == "wait":
                try:
                    current_step = {"type": "wait", "seconds": float(val)}
                except ValueError:
                    current_step = {"type": "wait", "seconds": 1}
                result["steps"].append(current_step)
            continue

        # 并行块内的 script 条目（带缩进 -）
        if in_parallel:
            m = re.match(r"-\s*script:\s*(.+)", stripped)
            if m:
                parallel_steps.append({"type": "script", "path": m.group(1).strip().strip('"').strip("'")})
            elif stripped.startswith("- parallel:") or not stripped.startswith("-"):
                in_parallel = False

    return result


# ======================================================================
# 工作流执行引擎
# ======================================================================

class WorkflowEngine:
    """工作流执行引擎"""

    def __init__(self):
        # 工作流共享变量 —— 线程本地容器, 支持并行分支隔离
        self._variables_tls = threading.local()
        self._variables_main = {}
        self._stopped = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        # 执行可视化: 当前执行的步骤索引 (-1=未执行) 与结果标记
        self._current_step = -1
        self._step_results = {}   # {step_index: "running"/"ok"/"error"}
        self._step_lock = threading.Lock()  # 保护并行期间 _current_step/_step_results 的写
        self._wf_stack = []       # 子工作流引用栈 (循环引用检测 + 深度上限)

    @property
    def _variables(self):
        """当前线程的工作流变量; 未设置线程本地 dict 时回退到主线程 dict。"""
        d = getattr(self._variables_tls, "d", None)
        return d if d is not None else self._variables_main

    @_variables.setter
    def _variables(self, value):
        self._variables_tls.d = value
        if threading.current_thread() is threading.main_thread():
            self._variables_main = value

    @property
    def variables(self):
        return self._variables

    @property
    def current_step(self):
        """当前正在执行的步骤索引 (-1=空闲)。"""
        return self._current_step

    def stop(self):
        """停止工作流执行"""
        self._stopped = True
        state.quit2 = True
        state.pause_event.set()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def _chk(self):
        """检查是否需要停止或暂停"""
        if self._stopped or state.quit2:
            return False
        while not self._pause_event.is_set():
            if self._stopped or state.quit2:
                return False
            time.sleep(0.05)
        return True

    def run_workflow(self, workflow_def, base_dir="."):
        """
        执行完整工作流。

        Args:
            workflow_def: 解析后的工作流定义 dict
            base_dir: 脚本文件的基准目录
        """
        self._stopped = False
        name = workflow_def.get("name", "未命名工作流")
        steps = workflow_def.get("steps", [])
        # 外层循环次数 (运行栏设置): loop_count<=0 约定为无限循环 (靠「最长分钟」/停止收敛)
        _raw_loops = workflow_def.get("loop_count", 1)
        if _raw_loops is None or _raw_loops == "":
            _raw_loops = 1
        try:
            outer_loops = int(_raw_loops)
        except (TypeError, ValueError):
            outer_loops = 1
        infinite = outer_loops <= 0
        try:
            max_minutes = float(workflow_def.get("max_minutes", 0) or 0)
        except (TypeError, ValueError):
            max_minutes = 0.0

        log1("=" * 50)
        log1("工作流启动: {}".format(name))
        log1("步骤数: {} | 循环: {} | 基准目录: {}".format(
            len(steps), "无限" if infinite else outer_loops, base_dir))
        log1("=" * 50)

        state.running = True
        start_time = time.time()
        self._step_results = {}

        try:
            loop_seq = itertools.count() if infinite else range(max(1, outer_loops))
            for loop_no in loop_seq:
                if not self._chk():
                    log1("工作流已停止", "warning")
                    break
                if loop_no > 0:
                    log1("--- 外层循环 第 {}/{} 次 ---".format(
                        loop_no + 1, "∞" if infinite else outer_loops))
                for i, step in enumerate(steps):
                    if not self._chk():
                        log1("工作流已停止", "warning")
                        break

                    # 执行可视化: 标记当前步骤
                    self._current_step = i
                    self._step_results[i] = "running"
                    log1("--- 步骤 {}/{} ---".format(i + 1, len(steps)))
                    try:
                        self._execute_step(step, base_dir)
                        self._step_results[i] = "ok"
                    except Exception as e:
                        self._step_results[i] = "error"
                        log1("步骤 {} 执行失败: {}".format(i + 1, e), "error")
                        if getattr(state, 'STOP_ON_ERROR', True):
                            self._stopped = True
                            break
                # 超时检测 (无限循环时)
                if max_minutes > 0 and (time.time() - start_time) >= max_minutes * 60:
                    log1("工作流超时: 已达最大运行 {} 分钟，自动停止".format(max_minutes), "warning")
                    break
                if not self._chk():
                    break

        except WorkflowError as e:
            log1("工作流错误: {}".format(e), "error")
        except Exception as e:
            log1("工作流异常: {}".format(e), "error")
        finally:
            elapsed = time.time() - start_time
            state.running = False
            self._current_step = -1
            log1("=" * 50)
            log1("工作流结束: {} (耗时 {:.1f}s)".format(name, elapsed))
            log1("=" * 50)

    def _execute_step(self, step, base_dir):
        """执行单个步骤 (支持 enabled 禁用跳过; 失败时先执行 on_error_rollback 补偿)。"""
        # 禁用节点跳过
        if step.get("enabled", True) is False:
            log1("  跳过已禁用步骤: {}".format(step.get("comment", "") or step.get("type", "")))
            return

        step_type = step.get("type", "script")
        try:
            self._dispatch(step, step_type, base_dir)
        except Exception:
            self._run_rollback(step, base_dir)
            raise

    def _dispatch(self, step, step_type, base_dir):
        """按步骤类型分派 (不含 rollback 包装, 由 _execute_step 统一补偿)。"""
        if step_type == "script":
            self._run_script(step, base_dir)

        elif step_type == "parallel":
            self._run_parallel(step, base_dir)

        elif step_type == "condition":
            self._run_condition(step, base_dir)

        elif step_type == "loop":
            self._run_loop(step, base_dir)

        elif step_type == "command":
            self._run_command(step, base_dir)

        elif step_type == "variable":
            self._run_variable(step, base_dir)

        elif step_type == "log":
            self._run_log(step)

        elif step_type == "wait":
            seconds = step.get("seconds", 1)
            log1("等待 {} 秒...".format(seconds))
            for _ in range(int(seconds * 10)):
                if not self._chk():
                    break
                time.sleep(0.1)

        elif step_type == "retry":
            self._run_retry(step, base_dir)

        elif step_type == "workflow":
            self._run_workflow_ref(step, base_dir)

        else:
            log1("未知步骤类型: {}".format(step_type), "warning")

    def _run_rollback(self, step, base_dir):
        """执行失败补偿子步骤 (best-effort; 补偿自身失败不掩盖原始异常)。"""
        rollback = step.get("on_error_rollback") or []
        if not rollback:
            return
        log1("  执行失败补偿 ({} 个步骤)...".format(len(rollback)))
        for sub in rollback:
            try:
                self._execute_step(sub, base_dir)
            except Exception as e:
                log1("  补偿步骤执行失败 (忽略): {}".format(e), "warning")

    def _run_retry(self, step, base_dir):
        """重试节点: 子步骤异常时按 delay 退避重试, 超过 max_attempts 抛最后一次异常。"""
        sub_steps = step.get("steps") or []
        if not sub_steps:
            log1("重试节点无子步骤", "warning")
            return
        try:
            max_attempts = int(step.get("max_attempts", 3) or 3)
        except (TypeError, ValueError):
            max_attempts = 3
        if max_attempts < 1:
            max_attempts = 1
        try:
            delay = float(step.get("delay", 1.0) or 0)
        except (TypeError, ValueError):
            delay = 0.0

        last_err = None
        for attempt in range(1, max_attempts + 1):
            if not self._chk():
                break
            log1("重试节点: 第 {}/{} 次尝试".format(attempt, max_attempts))
            try:
                for sub in sub_steps:
                    if not self._chk():
                        return
                    self._execute_step(sub, base_dir)
                return  # 成功
            except Exception as e:
                last_err = e
                log1("重试节点第 {} 次尝试失败: {}".format(attempt, e), "warning")
                if attempt < max_attempts and delay > 0:
                    slept = 0.0
                    while slept < delay:
                        if not self._chk():
                            break
                        time.sleep(0.1)
                        slept += 0.1
        raise WorkflowError("重试节点 {} 次后仍失败: {}".format(max_attempts, last_err))

    def _resolve_workflow_target(self, step, base_dir):
        """解析子工作流目标绝对路径; 缺失/非文件抛 WorkflowError。"""
        path = step.get("path", "")
        if not path:
            raise WorkflowError("子工作流路径为空")
        if os.path.isabs(path):
            target = path
        else:
            target = os.path.join(base_dir, path)
        target = os.path.normpath(target)
        if not os.path.exists(target):
            raise WorkflowError("子工作流不存在: {}".format(target))
        if not os.path.isfile(target):
            raise WorkflowError("子工作流路径不是文件: {}".format(target))
        return os.path.abspath(target)

    def _run_workflow_ref(self, step, base_dir):
        """子工作流引用节点: 加载目标 .json, 隔离作用域执行, 输出变量回写父作用域。

        循环引用检测: 执行中工作流绝对路径栈; 深度上限 _WF_MAX_DEPTH。
        变量语义: 以父作用域为基线副本 + inputs 覆盖 → 子工作流在副本内读写;
                  结束后仅声明的 outputs 写回父作用域 (与 parallel 分支隔离同理念)。
        """
        target = self._resolve_workflow_target(step, base_dir)
        stack = self._wf_stack
        if target in stack:
            chain = " → ".join(stack + [target])
            raise WorkflowError("检测到工作流循环引用: {}".format(chain))
        if len(stack) >= _WF_MAX_DEPTH:
            raise WorkflowError("子工作流嵌套过深 (> {} 层)".format(_WF_MAX_DEPTH))

        wf_def = parse_workflow(target)
        sub_steps = wf_def.get("steps", [])
        sub_base = os.path.dirname(target)
        inputs = step.get("inputs") or {}
        outputs = step.get("outputs") or []

        scope = dict(self._variables)
        for k, v in inputs.items():
            if isinstance(v, str) and "${" in v:
                import re
                v = re.sub(r"\$\{([^}]+)\}",
                           lambda m: str(self._variables.get(m.group(1), "")), v)
            scope[k] = v

        with self._step_lock:
            saved_step = self._current_step
            saved_results = self._step_results
            self._step_results = {}
        stack.append(target)
        log1("进入子工作流: {} ({} 个步骤)".format(os.path.basename(target), len(sub_steps)))
        try:
            fatal = self._with_isolated_scope(scope, lambda: self._run_steps(sub_steps, sub_base))
            if fatal:
                raise WorkflowError("子工作流执行失败: {}".format(os.path.basename(target)))
            for k in outputs:
                if k in scope:
                    self._variables[k] = scope[k]
        finally:
            stack.pop()
            with self._step_lock:
                self._current_step = saved_step
                self._step_results = saved_results
        log1("离开子工作流: {}".format(os.path.basename(target)))

    def _with_isolated_scope(self, scope_vars, fn):
        """在隔离变量作用域内执行 fn。

        同时切换 self._variables (工作流侧) 与 engine.variables (引擎侧), 但二者
        使用**独立** dict: 引擎侧副本防止 execute_script() 的 .clear() 清空工作流
        作用域。执行结束后只把引擎侧**相对初始快照的变更**合并回 scope_vars,
        既让 command/script 步骤写回变量可见, 又不覆盖 workflow 侧 (variable 步骤)
        对同名键的写入。
        """
        import engine as eng_mod
        eng = eng_mod.engine
        old_wf = self._variables
        old_eng = getattr(eng, 'variables', None)
        eng_scope = dict(scope_vars)     # engine 侧 (command/script 写这里)
        eng_initial = dict(scope_vars)   # engine 侧初始快照, 用于 diff
        self._variables = scope_vars
        try:
            eng.variables = eng_scope
        except Exception:
            pass
        try:
            result = fn()
            # 仅合并 engine 侧相对初始快照的变更 (新增/更新), 不覆盖 workflow 侧写入
            for k, v in eng_scope.items():
                if k not in eng_initial or eng_initial[k] != v:
                    scope_vars[k] = v
            return result
        finally:
            self._variables = old_wf
            try:
                eng.variables = old_eng
            except Exception:
                pass

    def _run_steps(self, steps, base_dir):
        """顺序执行一组步骤 (单遍), 返回是否发生致命失败 (STOP_ON_ERROR 命中)。

        顶层 run_workflow 与子工作流引用复用同一实现, 保证 STOP_ON_ERROR /
        暂停停止 / 可视化语义完全一致。
        """
        fatal = False
        for i, step in enumerate(steps):
            if not self._chk():
                break  # 用户停止/暂停: 不视为失败, 交由上层 _chk 收敛
            with self._step_lock:
                self._current_step = i
                self._step_results[i] = "running"
            log1("--- 步骤 {}/{} ---".format(i + 1, len(steps)))
            try:
                self._execute_step(step, base_dir)
                with self._step_lock:
                    self._step_results[i] = "ok"
            except Exception as e:
                with self._step_lock:
                    self._step_results[i] = "error"
                log1("步骤 {} 执行失败: {}".format(i + 1, e), "error")
                if getattr(state, 'STOP_ON_ERROR', True):
                    self._stopped = True
                    fatal = True
                    break
        return fatal

    def _run_command(self, step, base_dir):
        """执行 command 节点: 调用 commands 注册表的命令。

        节点格式: {"type": "command", "cmd": "坐标", "params": [9 个参数]}
        """
        cmd_name = step.get("cmd", "")
        if not cmd_name:
            log1("命令节点缺少 cmd 字段", "warning")
            return
        try:
            import commands
            import engine as eng_mod
            eng = eng_mod.engine
            handler = commands.get_handler(cmd_name)
            if handler is None:
                raise WorkflowError("未知命令: {}".format(cmd_name))
            # 构造 RowAdapter 兼容对象 (ScriptData)
            from scriptdata import ScriptData
            params = step.get("params", []) or []
            sd = ScriptData(cmd_name, [str(p) if p is not None else "" for p in params][:9])
            result = eng.execute(sd, base_dir)
            if not result.ok:
                raise WorkflowError(result.message or "命令执行失败: {}".format(cmd_name))
            log1("  命令: {} {}".format(cmd_name, params[:3]))
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError("命令执行失败 {}: {}".format(cmd_name, e))

    def _run_variable(self, step, base_dir):
        """执行 variable 节点: 设置工作流变量。

        节点格式: {"type": "variable", "var_name": "x", "var_value": "10"}
        """
        var_name = step.get("var_name", "").strip()
        if not var_name:
            log1("变量节点缺少变量名", "warning")
            return
        raw = step.get("var_value", "")
        # 支持引用其他变量 ${var}
        if isinstance(raw, str) and "${" in raw:
            import re
            raw = re.sub(r"\$\{([^}]+)\}", lambda m: str(self._variables.get(m.group(1), "")), raw)
        # 尝试数值转换
        value = raw
        if isinstance(raw, str):
            try:
                if "." in raw:
                    value = float(raw)
                else:
                    value = int(raw)
            except ValueError:
                pass
        self._variables[var_name] = value
        log1("  变量: {} = {}".format(var_name, value))

    def _run_log(self, step):
        """执行 log 节点: 输出日志/变量值。

        节点格式: {"type": "log", "text": "..."}
        """
        text = step.get("text", "")
        if "${" in text:
            import re
            text = re.sub(r"\$\{([^}]+)\}", lambda m: str(self._variables.get(m.group(1), "")), text)
        log1("  [LOG] {}".format(text))

    def _run_loop(self, step, base_dir):
        """执行 loop 节点: 固定次数或条件循环。

        节点格式: {"type": "loop", "times": "3" | "${x} < 5", "steps": [...]}
        """
        sub_steps = step.get("steps", []) or []
        if not sub_steps:
            log1("循环节点无子步骤", "warning")
            return

        times_expr = str(step.get("times", "1")).strip()
        is_conditional = "${" in times_expr or any(c in times_expr for c in "<>=!") and not times_expr.isdigit()

        max_iter = 0
        if not is_conditional:
            try:
                max_iter = int(times_expr)
            except ValueError:
                max_iter = 1
        else:
            max_iter = 1000  # 条件循环安全上限

        iteration = 0
        while iteration < max_iter:
            if not self._chk():
                break
            if is_conditional:
                # 求值条件
                import re
                variables = {}
                for vn in re.findall(r"\$\{([^}]+)\}", times_expr):
                    variables[vn] = self._variables.get(vn, 0)
                evaluated = re.sub(r"\$\{([^}]+)\}", r"\1", times_expr)
                try:
                    from safe_eval import safe_eval_condition
                    if not safe_eval_condition(evaluated, variables):
                        break
                except Exception:
                    break
            for sub in sub_steps:
                if not self._chk():
                    break
                self._execute_step(sub, base_dir)
            iteration += 1
        log1("  循环结束，共执行 {} 次".format(iteration))

    def _run_script(self, step, base_dir):
        """执行单个脚本"""
        path = step.get("path", "")
        if not path:
            log1("脚本路径为空，跳过", "warning")
            return

        # 解析路径（相对于工作流文件目录）
        if not os.path.isabs(path):
            script_path = os.path.join(base_dir, path)
        else:
            script_path = path

        log1("执行脚本: {}".format(os.path.basename(script_path)))

        try:
            rows = _load_xls_script(script_path)
            log1("  加载 {} 行命令".format(len(rows)))
        except Exception as e:
            raise WorkflowError("脚本加载失败: {}".format(e))

        # 更新全局状态（兼容 autorun 的进度显示）
        state.exec_state["total_rows"] = len(rows)
        state.exec_state["row"] = 0
        script_dir = os.path.dirname(script_path)

        # 注入工作流变量到 engine
        try:
            import engine as eng_mod
            eng = eng_mod.engine
            # 合并工作流变量（工作流变量优先）
            for k, v in self._variables.items():
                eng.variables[k] = v
            # 执行脚本
            eng.execute_script(rows, script_dir)
            if getattr(eng, '_script_failed', False):
                raise WorkflowError("脚本执行失败: {}".format(os.path.basename(script_path)))
            # 回写变量
            self._variables.update(eng.variables)
        except ImportError as e:
            raise WorkflowError("无法导入 engine 模块: {}".format(e))
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError("脚本执行异常: {}".format(e))

    def _run_parallel(self, step, base_dir):
        """真并行执行多个子步骤。

        每个分支拥有独立变量作用域 (branch_scopes[i]), 分支间互不可见;
        执行结束后仅将声明为 outputs 的变量按分支定义顺序确定性合并回主作用域。
        未声明 outputs 时向后兼容旧行为: 合并全部分支变量 (按定义顺序)。
        """
        sub_steps = step.get("steps", [])
        if not sub_steps:
            return

        log1("并行执行 {} 个子步骤...".format(len(sub_steps)))

        n = len(sub_steps)
        outputs = [str(x) for x in (step.get("outputs") or [])]
        # 向后兼容: 未声明 outputs 时, 合并所有变量 (旧行为)
        merge_all = not outputs

        # 主线程快照父作用域, 每分支浅拷贝一份独立作用域
        parent_vars = dict(self._variables)
        branch_scopes = [dict(parent_vars) for _ in range(n)]
        branch_errors = [None for _ in range(n)]

        def _run_sub_step(sub_step, idx):
            scope = branch_scopes[idx]
            try:
                # 隔离作用域: self._variables / engine.variables 均切换为线程本地 dict
                self._with_isolated_scope(
                    scope, lambda: self._execute_step(sub_step, base_dir))
                log1("  并行子步骤 {} 完成".format(idx + 1))
            except Exception as e:
                branch_errors[idx] = str(e)
                log1("  并行子步骤 {} 失败: {}".format(idx + 1, e), "error")

        max_workers = min(n, 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_run_sub_step, s, i)
                for i, s in enumerate(sub_steps)
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        # 按分支定义顺序确定性合并 (后定义覆盖先定义)
        for scope in branch_scopes:
            if merge_all:
                self._variables.update(scope)
            else:
                for k in outputs:
                    if k in scope:
                        self._variables[k] = scope[k]

        # 报告失败
        failed_indices = [i + 1 for i, e in enumerate(branch_errors) if e]
        if failed_indices:
            message = "并行步骤 {} 执行失败".format(failed_indices)
            log1(message, "error")
            raise WorkflowError(message)

    def _run_condition(self, step, base_dir):
        """条件分支"""
        condition = step.get("if", "")
        if not condition:
            log1("条件为空，跳过", "warning")
            return

        result = self._eval_condition(condition)
        log1("条件判断: {} → {}".format(condition, result))

        if result:
            branch = step.get("then")
        else:
            branch = step.get("else")

        if branch:
            if isinstance(branch, str):
                branch = {"type": "script", "path": branch}
            self._execute_step(branch, base_dir)
        else:
            log1("  无对应分支，跳过")

    def _eval_condition(self, condition_str):
        """评估条件表达式"""
        import re
        from safe_eval import safe_eval_condition, EvalError

        try:
            variables = {}
            # 解析 ${var} 引用
            var_refs = re.findall(r'\$\{([^}]+)\}', condition_str)
            for vn in var_refs:
                vn = vn.strip()
                value = self._variables.get(vn, None)
                if value is None:
                    value = 0
                variables[vn] = value

            evaluated = re.sub(r'\$\{([^}]+)\}', r'\1', condition_str)
            return safe_eval_condition(evaluated, variables)
        except EvalError:
            return False
        except Exception:
            return False


# 全局工作流引擎实例
workflow_engine = WorkflowEngine()


# ======================================================================
# 工作流命令 (在脚本中使用)
# ======================================================================

def _get_engine():
    try:
        import engine as eng_mod
        return eng_mod.engine
    except Exception:
        return None


def _handler_workflow_run(row, script_dir):
    """
    命令: 运行工作流 — 在脚本中调用工作流文件

    参数:
        params[0]: 工作流文件路径 (.json/.yaml)
    """
    eng = _get_engine()
    if not eng:
        return
    eng._chk()

    if hasattr(row, 'args'):
        wf_path = row.args[0] if len(row.args) > 0 else ""
    else:
        wf_path = str(row[1].value) if row[1].value else ""

    if not wf_path:
        log1("运行工作流: 未指定工作流文件", "error")
        return

    # 相对路径转绝对
    if not os.path.isabs(wf_path):
        wf_path = os.path.join(script_dir, wf_path)

    if not os.path.exists(wf_path):
        log1("运行工作流: 文件不存在 '{}'".format(wf_path), "error")
        return

    try:
        wf_def = parse_workflow(wf_path)
        base_dir = os.path.dirname(wf_path)

        # 传递当前变量
        workflow_engine._variables.update(eng.variables)

        log1("运行工作流: {}".format(wf_def.get("name", wf_path)))
        workflow_engine.run_workflow(wf_def, base_dir)

        # 回写变量
        eng.variables.update(workflow_engine._variables)
    except Exception as e:
        log1("运行工作流失败: {}".format(e), "error")


def _handler_workflow_setvar(row, script_dir):
    """
    命令: 工作流变量 — 设置工作流级别的共享变量

    参数:
        params[0]: 变量名
        params[1]: 变量值
    """
    if hasattr(row, 'args'):
        var_name = row.args[0] if len(row.args) > 0 else ""
        var_value = row.args[1] if len(row.args) > 1 else ""
    else:
        var_name = str(row[1].value) if row[1].value else ""
        var_value = row[2].value if row[2].value else ""

    if var_name:
        # 尝试数值转换
        if isinstance(var_value, str):
            try:
                if '.' in var_value:
                    var_value = float(var_value)
                else:
                    var_value = int(var_value)
            except ValueError:
                pass
        workflow_engine._variables[var_name] = var_value
        log1("工作流变量: {} = {}".format(var_name, var_value))
