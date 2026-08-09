"""
Workflow Engine — 多脚本串联工作流编排

功能:
- 多脚本顺序/并行执行
- 条件分支 (if/then/else)
- 变量跨脚本共享
- YAML/JSON 工作流定义解析

依赖: engine.py, scriptdata.py, safe_eval.py, state.py, utils.py
"""
import os, sys, time, json, threading, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import state
from utils import log1


# ── 工作流步骤类型 ──
STEP_SCRIPT = "script"
STEP_PARALLEL = "parallel"
STEP_CONDITION = "condition"
STEP_WAIT = "wait"


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

    if step.get("type") == "parallel" and "steps" in step:
        step["steps"] = [_normalize_step(s) for s in step["steps"]]

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
        self._variables = {}  # 工作流共享变量
        self._stopped = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        # 执行可视化: 当前执行的步骤索引 (-1=未执行) 与结果标记
        self._current_step = -1
        self._step_results = {}   # {step_index: "running"/"ok"/"error"}

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
        # 外层循环次数 (UI 工具栏设置)
        outer_loops = int(workflow_def.get("loop_count", 1) or 1)
        max_minutes = float(workflow_def.get("max_minutes", 0) or 0)

        log1("=" * 50)
        log1("工作流启动: {}".format(name))
        log1("步骤数: {} | 循环: {} | 基准目录: {}".format(len(steps), outer_loops, base_dir))
        log1("=" * 50)

        state.running = True
        start_time = time.time()
        self._step_results = {}

        try:
            for loop_no in range(max(1, outer_loops)):
                if not self._chk():
                    log1("工作流已停止", "warning")
                    break
                if loop_no > 0:
                    log1("--- 外层循环 第 {}/{} 次 ---".format(loop_no + 1, outer_loops))
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
        """执行单个步骤 (支持 enabled 禁用跳过)。"""
        # 禁用节点跳过
        if step.get("enabled", True) is False:
            log1("  跳过已禁用步骤: {}".format(step.get("comment", "") or step.get("type", "")))
            return

        step_type = step.get("type", "script")

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

        else:
            log1("未知步骤类型: {}".format(step_type), "warning")

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
                log1("未知命令: {}".format(cmd_name), "error")
                return
            # 构造 RowAdapter 兼容对象 (ScriptData)
            from scriptdata import ScriptData
            params = step.get("params", []) or []
            sd = ScriptData(cmd_name, [str(p) if p is not None else "" for p in params][:9])
            eng.execute(sd, base_dir)
            log1("  命令: {} {}".format(cmd_name, params[:3]))
        except Exception as e:
            log1("命令执行失败 {}: {}".format(cmd_name, e), "error")

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
            log1("  脚本加载失败: {}".format(e), "error")
            return

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
            # 回写变量
            self._variables.update(eng.variables)
        except ImportError:
            log1("  无法导入 engine 模块", "error")
        except Exception as e:
            log1("  脚本执行异常: {}".format(e), "error")

    def _run_parallel(self, step, base_dir):
        """并行执行多个子步骤"""
        sub_steps = step.get("steps", [])
        if not sub_steps:
            return

        log1("并行执行 {} 个子步骤...".format(len(sub_steps)))

        # 使用 ThreadPoolExecutor，每个线程操作独立的变量快照
        # 注意：engine.variables 是共享的，并行线程不应直接修改它
        n = len(sub_steps)
        step_results = [{} for _ in range(n)]
        step_errors = [None for _ in range(n)]
        lock = threading.Lock()

        def _run_sub_step(sub_step, idx):
            """在线程中执行子步骤，捕获变量变更到独立快照"""
            try:
                import engine as eng_mod
                eng = eng_mod.engine
                # 引擎 variables 是全局共享的，多个并行线程同时写会数据竞争。
                # 用 lock 串行化实际执行（虽牺牲部分并行度，但保证变量一致）。
                with lock:
                    # 快照：注入工作流变量后执行
                    for k, v in self._variables.items():
                        eng.variables[k] = v
                    self._execute_step(sub_step, base_dir)
                    # 捕获该线程产生的变量变更
                    step_results[idx] = dict(eng.variables)
                log1("  并行子步骤 {} 完成".format(idx + 1))
            except Exception as e:
                with lock:
                    step_errors[idx] = str(e)
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

        # 合并并行结果（按顺序合并变量，后执行的覆盖先执行的）
        for varset in step_results:
            if varset:
                self._variables.update(varset)

        # 报告失败
        failed_indices = [i + 1 for i, e in enumerate(step_errors) if e]
        if failed_indices:
            log1("  并行步骤 {} 执行失败".format(failed_indices), "warning")

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
