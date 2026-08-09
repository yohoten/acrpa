"""ExecutionEngine — interprets ScriptData rows at runtime.

P0 Optimization #1: Lazy imports for heavy libraries
P0 Optimization #3: Image location caching for faster repeated searches
P1 Enhancement: Event-driven variable watcher
P1 Enhancement: Conditional breakpoints support
P1 Enhancement: Call stack tracking for nested loops/conditions
P2 Enhancement: Window management using pywin32

Note: Image recognition uses Pillow backend (not OpenCV) to reduce package size.
      pyautogui automatically falls back to Pillow when OpenCV is not available.
      Performance is slightly slower (~150-200ms vs ~100ms) but sufficient for RPA tasks.
"""
import os, time, datetime, re
import state
from utils import _safe_get, log1
import commands
from safe_eval import safe_eval_condition, safe_eval_math, EvalError

# ── 预编译正则 (避免每次调用编译) ──
_VAR_REF_RE = re.compile(r'\$\{([^}]+)\}')

# ── RowAdapter Cell 类模块级预创建 (避免每次 __init__ 动态 type()) ──
class _Cell:
    __slots__ = ('value',)

class RowAdapter:
    """Adapts ScriptData objects to xlrd-style row access (row[index].value)."""
    __slots__ = ('cells',)
    def __init__(self, script_data):
        c0 = _Cell(); c0.value = script_data.cmd_type
        self.cells = [c0]
        for arg in script_data.args:
            c = _Cell()
            c.value = arg if arg else None
            self.cells.append(c)
    def __getitem__(self, index):
        if index < len(self.cells):
            return self.cells[index]
        return _Cell()  # 返回空 Cell，不再动态 type()


# P0 Optimization #1: Lazy imports for heavy libraries (P0 optimization #1)
_pyautogui = None
_pyperclip = None
_win32gui = None
_win32con = None
_dd_backend = None

def get_pyautogui():
    """Lazy load pyautogui on first use"""
    global _pyautogui
    if _pyautogui is None:
        import pyautogui as pa
        # Configure pyautogui to use Pillow backend (lighter than OpenCV)
        # This reduces package size by ~60MB while maintaining good performance
        pa.USE_IMAGE_NOT_FOUND_EXCEPTION = False
        
        # Use configurable fail-safe setting from state
        # Default True for safety, can be disabled in settings for RPA automation
        pa.FAILSAFE = getattr(state, 'FAILSAFE', True)
        
        _pyautogui = pa
    return _pyautogui

# ── OpenCV 可用性检测 (pyautogui 的 confidence 相似度匹配需要 OpenCV) ──
# 项目默认不打包 OpenCV (减小体积), 未安装时找图自动降级为 Pillow 匹配。
_cv2_checked = False
_cv2_ok = False
_cv2_hint_logged = False

def _has_cv2():
    """Return True if OpenCV is importable (needed for confidence matching)."""
    global _cv2_checked, _cv2_ok
    if not _cv2_checked:
        _cv2_checked = True
        try:
            import cv2  # noqa: F401
            _cv2_ok = True
        except Exception:
            _cv2_ok = False
    return _cv2_ok

def get_pyperclip():
    """Lazy load pyperclip on first use"""
    global _pyperclip
    if _pyperclip is None:
        import pyperclip as pc
        _pyperclip = pc
    return _pyperclip

def get_win32gui():
    """Lazy load win32gui on first use"""
    global _win32gui, _win32con
    if _win32gui is None:
        try:
            import win32gui
            import win32con
            _win32gui = win32gui
            _win32con = win32con
        except ImportError:
            log1("警告:pywin32未安装，窗口管理功能不可用。请运行: pip install pywin32", "error")
            raise
    return _win32gui, _win32con

def get_dd_backend():
    """Lazy load DD backend on first use (optional high-performance input)"""
    global _dd_backend
    if _dd_backend is None:
        try:
            from dd_backend import get_dd_backend as _get_dd
            _dd_backend = _get_dd()
        except Exception as e:
            log1(f" DD驱动模块加载失败: {e}", "warning")
            _dd_backend = None
    return _dd_backend


# P0 Optimization #3: Image location cache with LRU eviction
# Format: {image_path: (x, y, timestamp)}
_image_cache = {}
_CACHE_TTL = 5.0       # Cache validity in seconds
_CACHE_MAX = 50         # Maximum cache entries (LRU eviction)
_CACHE_ORDER = []       # LRU access order list


# P1 Enhancement: Event-driven variable watcher
class VariableWatcher:
    """Observer pattern for variable changes - replaces polling with events"""
    def __init__(self):
        self._observers = []
        self._last_values = {}
    
    def register_observer(self, callback):
        """Register a callback function to be called when variables change"""
        if callback not in self._observers:
            self._observers.append(callback)
    
    def unregister_observer(self, callback):
        """Remove an observer callback"""
        if callback in self._observers:
            self._observers.remove(callback)
    
    def notify_changes(self, current_variables):
        """Notify observers only when variables actually change"""
        changed_vars = {}
        for var_name, var_value in current_variables.items():
            if var_name not in self._last_values or self._last_values[var_name] != var_value:
                changed_vars[var_name] = var_value
        
        if changed_vars:
            self._last_values.update(current_variables)
            for callback in self._observers:
                try:
                    callback(changed_vars)
                except Exception as e:
                    log1("变量观察者错误: {}".format(e), "warning")


# P1 Enhancement: Call stack for tracking execution context
class CallStack:
    """Tracks nested loop/condition execution context"""
    def __init__(self):
        self.stack = []  # List of dicts: {type, row, iteration, condition}
    
    def push(self, block_type, row_index, **kwargs):
        """Push a new execution context onto the stack"""
        context = {
            'type': block_type,  # 'loop', 'if', 'else'
            'row': row_index,
            'start_time': time.time(),
        }
        context.update(kwargs)
        self.stack.append(context)
    
    def pop(self):
        """Pop the top execution context"""
        if self.stack:
            return self.stack.pop()
        return None
    
    def peek(self):
        """Peek at the top execution context without removing it"""
        return self.stack[-1] if self.stack else None
    
    def get_depth(self):
        """Get current nesting depth"""
        return len(self.stack)
    
    def get_formatted_stack(self):
        """Get human-readable call stack representation"""
        if not self.stack:
            return "无嵌套"
        
        lines = []
        for i, ctx in enumerate(self.stack):
            indent = "  " * i
            if ctx['type'] == 'loop':
                iteration = ctx.get('iteration', 0)
                max_iter = ctx.get('max_iterations', '∞')
                lines.append("{}{}循环 第{}/{}次 (行{})".format(
                    indent, ctx.get('condition', ''), iteration, max_iter, ctx['row']))
            elif ctx['type'] == 'if':
                condition = ctx.get('condition', '')
                result = ctx.get('result', '?')
                lines.append("{}如果 {} → {}".format(indent, condition, result))
            elif ctx['type'] == 'else':
                lines.append("{}否则分支 (行{})".format(indent, ctx['row']))
        
        return "\n".join(lines)


class ExecutionEngine:
    def __init__(self):
        self.retry = state.RETRY_MAX
        self.retry_interval = state.RETRY_INTERVAL
        # Variable storage for condition/loop support
        self.variables = {}
        
        # P1 Enhancement: Initialize variable watcher and call stack
        self.variable_watcher = VariableWatcher()
        self.call_stack = CallStack()
        
        # P1 Enhancement: Conditional breakpoints storage
        # Format: {row_number: condition_string}
        self.conditional_breakpoints = {}

    def execute_script(self, rows, script_dir, _nested=False):
        """
        Execute a complete script with support for conditions and loops.
        This is the main entry point for script execution.
        """
        if not _nested:
            # Clear variables from previous run to prevent cross-run pollution
            self.variables.clear()
        i = 0
        total_rows = len(rows)
        
        while i < total_rows:
            if state.quit2:
                break
            
            # Check skip-row signal (debugger enhancement)
            if hasattr(state, '_skip_next_row') and state._skip_next_row:
                state._skip_next_row = False
                i += 1
                continue
            
            row = rows[i]
            # ScriptData objects have cmd_type attribute, not indexable
            cmd_type = row.cmd_type if hasattr(row, 'cmd_type') else str(row[0].value) if row[0].value is not None else ""
            
            # Update highlight row for debugger and progress tracking
            state.highlight_row = i + 1  # 1-based indexing
            state.exec_state["row"] = i + 1
            
            # P1 Enhancement: Check conditional breakpoints before execution
            # conditional_breakpoints use 1-based row_number (matching tree index in GUI)
            if state.debug_mode and (i + 1) in self.conditional_breakpoints:
                condition = self.conditional_breakpoints[i + 1]
                if self._evaluate_condition(condition):
                    log1("触发条件断点 - 行{}: {}".format(i + 1, condition), "info")
                    state.pause_event.clear()
                    while not state.quit2 and not state.pause_event.is_set():
                        time.sleep(0.1)
                    if state.quit2:
                        break

            # Debugger enhancement: check run-to-cursor signal
            if hasattr(state, '_run_to_row') and state._run_to_row > 0:
                if (i + 1) == state._run_to_row:
                    log1("运行到光标 - 第{}行".format(i + 1), "info")
                    state._run_to_row = 0
                    state.pause_event.clear()
                    while not state.quit2 and not state.pause_event.is_set():
                        time.sleep(0.1)
                    if state.quit2:
                        break

            # Python 3.7 compatible: Use if-elif-else instead of match-case
            if cmd_type == "如果":
                # Evaluate condition and track in call stack
                condition = row.args[0] if hasattr(row, 'args') and len(row.args) > 0 else ""
                result = self._evaluate_condition(condition)
                self.call_stack.push('if', i+1, condition=condition, result=result)
                
                # Evaluate condition and skip to matching 否则 or 结束如果
                i = self._handle_if(rows, i, script_dir)
                
                self.call_stack.pop()
                # _handle_if returns the index after the block, so we continue to next iteration
                continue
            
            elif cmd_type == "否则":
                # Skip to matching 结束如果 (should not reach here normally)
                self.call_stack.push('else', i+1)
                i = self._find_endif(rows, i)
                self.call_stack.pop()
                continue

            elif cmd_type == "结束如果":
                i += 1  # Just move past
                continue

            elif cmd_type == "循环开始":
                # Execute loop body with call stack tracking
                loop_param = row.args[0] if hasattr(row, 'args') and len(row.args) > 0 else ""
                
                # Determine loop iterations
                try:
                    max_iterations = int(loop_param)
                    is_conditional = False
                except ValueError:
                    max_iterations = 1000
                    is_conditional = True
                
                self.call_stack.push('loop', i+1,
                                   condition=loop_param,
                                   max_iterations=max_iterations,
                                   iteration=0)
                
                i = self._handle_loop(rows, i, script_dir)
                
                self.call_stack.pop()
                continue

            elif cmd_type in ("循环结束", "跳出循环"):
                # These are typically handled by their respective block handlers or skipped
                i += 1
                continue
            
            else:
                # Regular command execution with timing (debugger enhancement)
                _t0 = time.time()
                self.execute(row, script_dir)
                _elapsed = time.time() - _t0
                
                # Debugger enhancement: record execution timing
                if state.debug_mode:
                    row_num = i + 1
                    if not hasattr(state, '_exec_timings'):
                        state._exec_timings = {}
                    state._exec_timings[row_num] = {
                        "cmd": cmd_type,
                        "elapsed": _elapsed,
                        "success": True,
                        "time": time.time(),
                    }
                
                i += 1
            
            # P1 Enhancement: Notify variable watchers and handle step-mode debugging
            if state.debug_mode:
                self.variable_watcher.notify_changes(self.variables)
                
                # Check debug mode - pause at every step if in step mode (for executed commands)
                if state.step_mode:
                    log1("单步执行暂停 - 第{}行: {}".format(state.highlight_row, cmd_type), "info")
                    state.pause_event.clear()
                    while not state.quit2 and not state.pause_event.is_set():
                        time.sleep(0.1)
                    if state.quit2:
                        break

    def _evaluate_condition(self, condition_str):
        """
        Evaluate a condition expression safely using AST parsing.
        Supports simple comparisons and variable references.
        Examples: "${x} > 5", "${found} == True", "1 == 1"
        """
        try:
            # Build variables dict from ${var} references and engine variables
            variables = {}
            var_refs = _VAR_REF_RE.findall(condition_str)
            for vn in var_refs:
                vn = vn.strip()
                value = self.variables.get(vn, None)
                if value is None:
                    log1("警告: 变量 '{}' 未定义，使用默认值 0".format(vn), "warning")
                    value = 0
                variables[vn] = value

            # Strip ${} wrappers so the AST sees plain variable names
            evaluated = _VAR_REF_RE.sub(r'\1', condition_str)
            return safe_eval_condition(evaluated, variables)
        except EvalError as e:
            log1("条件评估错误 '{}': {}".format(condition_str, e), "error")
            return False
        except Exception as e:
            log1("条件评估异常 '{}': {}".format(condition_str, e), "error")
            return False

    def set_conditional_breakpoint(self, row_number, condition):
        """Set a conditional breakpoint at specified row"""
        if condition:
            self.conditional_breakpoints[row_number] = condition
            log1("设置条件断点 - 行{}: {}".format(row_number, condition))
        else:
            # Remove conditional breakpoint if exists
            if row_number in self.conditional_breakpoints:
                del self.conditional_breakpoints[row_number]
                log1("移除条件断点 - 行{}".format(row_number))

    def get_call_stack_info(self):
        """Get current call stack information for debugger"""
        return {
            'depth': self.call_stack.get_depth(),
            'formatted': self.call_stack.get_formatted_stack(),
            'stack': list(self.call_stack.stack)
        }

    def _find_matching_block_end(self, rows, start_idx, start_cmd, end_cmd, alt_cmd=None):
        """
        Find the matching end command for a block (if/loop).
        Handles nested blocks correctly.
        """
        depth = 1
        i = start_idx + 1
        total = len(rows)
        
        while i < total:
            row = rows[i]
            cmd = row.cmd_type if hasattr(row, 'cmd_type') else str(row[0].value) if row[0].value is not None else ""
            if cmd == start_cmd:
                depth += 1
            elif cmd == end_cmd or (alt_cmd and cmd == alt_cmd):
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        
        return total  # Not found, return end

    def _find_endif(self, rows, start_idx):
        """Find the matching 结束如果 for current position"""
        return self._find_matching_block_end(rows, start_idx, "如果", "结束如果", "否则")

    def _handle_if(self, rows, start_idx, script_dir):
        """
        Handle if-else-endif block.
        Returns the index after the entire if block.
        """
        condition_row = rows[start_idx]
        # ScriptData uses args[0] for parameter 1 (index 0 in args array)
        condition = condition_row.args[0] if hasattr(condition_row, 'args') and len(condition_row.args) > 0 else ""
        
        # Find else and endif positions
        else_idx = None
        endif_idx = None
        depth = 1
        i = start_idx + 1
        total = len(rows)
        
        while i < total and depth > 0:
            row = rows[i]
            cmd = row.cmd_type if hasattr(row, 'cmd_type') else str(row[0].value) if row[0].value is not None else ""
            if cmd == "如果":
                depth += 1
            elif cmd == "结束如果":
                depth -= 1
                if depth == 0:
                    endif_idx = i
                    break
            elif cmd == "否则" and depth == 1:
                else_idx = i
            i += 1
        
        if endif_idx is None:
            log1("错误：未找到匹配的'结束如果'", "error")
            return start_idx + 1
        
        # Evaluate condition
        if self._evaluate_condition(condition):
            # Condition is true, execute if block
            log1("条件为真，执行IF块")
            if else_idx is not None:
                # Execute from start+1 to else_idx
                self.execute_script(rows[start_idx+1:else_idx], script_dir, _nested=True)
                return endif_idx + 1
            else:
                # No else, execute to endif
                self.execute_script(rows[start_idx+1:endif_idx], script_dir, _nested=True)
                return endif_idx + 1
        else:
            # Condition is false
            if else_idx is not None:
                # Execute else block
                log1("条件为假，执行ELSE块")
                self.execute_script(rows[else_idx+1:endif_idx], script_dir, _nested=True)
            else:
                log1("条件为假，跳过IF块")
            return endif_idx + 1

    def _handle_loop(self, rows, start_idx, script_dir):
        """
        Handle loop block.
        Supports: fixed count loops and conditional loops.
        Examples: "5" (loop 5 times), "${count} < 10" (loop while condition)
        """
        loop_row = rows[start_idx]
        # ScriptData uses args[0] for parameter 1
        loop_param = loop_row.args[0] if hasattr(loop_row, 'args') and len(loop_row.args) > 0 else ""
        
        # Find loop end
        loop_end_idx = self._find_matching_block_end(rows, start_idx, "循环开始", "循环结束")
        
        if loop_end_idx >= len(rows):
            log1("错误：未找到匹配的'循环结束'", "error")
            return start_idx + 1
        
        # Determine loop type and iterations
        max_iterations = 1000  # Safety limit
        iteration_count = 0
        
        # Try to parse as number first
        try:
            max_iterations = int(loop_param)
            is_conditional = False
            log1("开始固定次数循环: {} 次".format(max_iterations))
        except ValueError:
            # It's a condition expression
            is_conditional = True
            log1("开始条件循环: {}".format(loop_param))
        
        # Execute loop body
        loop_start = start_idx + 1
        loop_body = rows[loop_start:loop_end_idx]
        
        while iteration_count < 1000:  # Safety limit
            if state.quit2:
                break
            
            # Check loop condition
            if is_conditional:
                if not self._evaluate_condition(loop_param):
                    log1("循环条件不满足，退出循环")
                    break
            else:
                if iteration_count >= max_iterations:
                    log1("达到最大循环次数，退出循环")
                    break
            
            # Execute loop body (支持嵌套 如果/循环开始 块命令)
            should_break = False
            j = 0
            while j < len(loop_body):
                body_row = loop_body[j]
                cmd = body_row.cmd_type if hasattr(body_row, 'cmd_type') else str(body_row[0].value) if body_row[0].value is not None else ""
                if cmd == "跳出循环":
                    should_break = True
                    log1("执行跳出循环")
                    break
                elif cmd == "如果":
                    # 整个 IF 块由 _handle_if 递归执行，跳转到块之后
                    ret = self._handle_if(loop_body, j, script_dir)
                    j = ret - 1
                elif cmd == "循环开始":
                    # 整个内层循环块由 _handle_loop 递归执行，跳转到块之后
                    ret = self._handle_loop(loop_body, j, script_dir)
                    j = ret - 1
                else:
                    self.execute(body_row, script_dir)
                j += 1
            
            iteration_count += 1
            
            if should_break:
                break
        
        log1("循环结束，共执行 {} 次".format(iteration_count))
        return loop_end_idx + 1

    def execute(self, row, script_dir):
        # Support both ScriptData objects and xlrd rows
        if hasattr(row, 'cmd_type'):
            # ScriptData object
            adapted_row = RowAdapter(row)
        else:
            # xlrd row object (original format)
            adapted_row = row
        
        cv = str(adapted_row[0].value) if adapted_row[0].value is not None else ""
        h = commands.get_handler(cv)
        if h:
            _t0 = time.time()
            success = False
            last_error = ""
            for attempt in range(self.retry + 1):
                try:
                    h(adapted_row, script_dir)
                    success = True
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt < self.retry:
                        log1("命令 '{}' 失败 (尝试 {}/{}) — {} 秒后重试".format(
                            cv, attempt+1, self.retry, self.retry_interval), "warning")
                        time.sleep(self.retry_interval)
                    else:
                        log1("命令 '{}' 最终失败: {}".format(cv, e), "error")
                        # Auto-screenshot on failure
                        try:
                            ts = datetime.datetime.now().strftime("%m%d_%H%M%S")
                            fp = os.path.join(os.path.dirname(state.CONFIG_PATH),
                                "screenshots", "fail_{}_{}.png".format(cv, ts))
                            os.makedirs(os.path.dirname(fp), exist_ok=True)
                            get_pyautogui().screenshot(fp)
                            log1("失败截图已保存: {}".format(fp), "warning")
                        except Exception:
                            pass
                        
                        # AI 增强: 智能重试分析
                        if hasattr(state, 'AI_SMART_RETRY') and state.AI_SMART_RETRY:
                            try:
                                cmd_args = list(adapted_row[1:10])
                                cmd_args = [c.value if hasattr(c, 'value') else c for c in cmd_args]
                                from ai_enhance import ai_smart_retry
                                plan = ai_smart_retry(cv, cmd_args[:4], last_error, script_dir,
                                                       attempt, self.retry)
                                if plan and plan.get("action") == "wait_and_retry":
                                    wait_s = plan.get("wait_seconds", 2)
                                    log1("AI 建议: 等待 {}s 后重试...".format(wait_s))
                                    time.sleep(wait_s)
                                    try:
                                        h(adapted_row, script_dir)
                                        success = True
                                        log1("AI 智能重试成功!")
                                    except Exception:
                                        pass
                            except Exception:
                                pass  # AI 增强失败不影响主流程
            
            _elapsed = (time.time() - _t0) * 1000
            # AI 增强: 异常检测记录
            if hasattr(state, 'AI_ANOMALY_DETECT') and state.AI_ANOMALY_DETECT:
                try:
                    from ai_enhance import anomaly_detector
                    # 获取当前行号(如果可用)
                    current_row = state.exec_state.get("row", 0) if hasattr(state, 'exec_state') else 0
                    anomaly_detector.record(cv, _elapsed, success, current_row)
                except Exception:
                    pass
        else:
            success = True  # No handler found, but not a failure
        return True

    def _chk(self):
        if state.quit2: return False
        while not state.pause_event.is_set():
            if state.quit2: return False
            time.sleep(0.05)
        return True

    def _find_cached(self, img_path, confidence, region=None, grayscale=True):
        """
        Find image with LRU-caching support (max {} entries).
        Returns pyautogui Point object (with .x/.y attributes) or None.
        """.format(_CACHE_MAX)
        now = time.time()
        
        # Check cache first
        if img_path in _image_cache:
            x, y, ts = _image_cache[img_path]
            if now - ts < _CACHE_TTL:
                # LRU: move to end (most recently used)
                if img_path in _CACHE_ORDER:
                    _CACHE_ORDER.remove(img_path)
                _CACHE_ORDER.append(img_path)
                log1("使用缓存位置: {} ({:.0f}ms)".format(img_path, (now - ts) * 1000))
                return (x, y)
            else:
                # Cache expired, remove it
                del _image_cache[img_path]
                if img_path in _CACHE_ORDER:
                    _CACHE_ORDER.remove(img_path)
        
        # Not in cache or expired, perform actual search
        pa = get_pyautogui()
        if _has_cv2():
            loc = pa.locateCenterOnScreen(img_path, confidence=confidence,
                                         region=region, grayscale=grayscale)
        else:
            # OpenCV 未安装: pyautogui 的 confidence 参数不可用,
            # 降级为 Pillow 匹配 (保持小体积设计), 首次运行时提示一次
            global _cv2_hint_logged
            if not _cv2_hint_logged:
                _cv2_hint_logged = True
                log1("未检测到 OpenCV, 找图使用 Pillow 精确匹配 (不支持 confidence 相似度). "
                     "安装 opencv-python 可启用相似度匹配", "warning")
            loc = pa.locateCenterOnScreen(img_path,
                                         region=region, grayscale=grayscale)
        
        if loc:
            # LRU eviction: if cache is full, remove oldest entry
            if len(_image_cache) >= _CACHE_MAX:
                oldest = _CACHE_ORDER.pop(0) if _CACHE_ORDER else None
                if oldest and oldest in _image_cache:
                    del _image_cache[oldest]
            # Update cache
            _image_cache[img_path] = (loc.x, loc.y, now)
            _CACHE_ORDER.append(img_path)
        
        return loc

    def _image_search_loop(self, row, z, action="hover", region=False,
                           button="left", click_count=1):
        """统一的图像搜索+操作循环 (合并 _find/_rfind/_click/_rclick 重复逻辑)

        Args:
            row: 当前命令行 (RowAdapter 兼容对象)
            z: 图片目录路径
            action: "hover" 悬停 / "click" 点击
            region: True=限定区域查找 (使用参数3-7作为区域和灰度)
            button: 点击按键 ("left"/"right")
            click_count: 点击次数
        """
        self._chk()
        img = "{}/{}.png".format(z, str(row[1].value))
        confidence = row[2].value
        if not isinstance(confidence, (int, float)) or confidence > 1.0:
            confidence = 0.96

        region_tuple = None
        grayscale = True
        if region:
            region_tuple = (
                _safe_get(row[3].value, 0, int), _safe_get(row[4].value, 0, int),
                _safe_get(row[5].value, 800, int), _safe_get(row[6].value, 800, int))
            grayscale = row[7].value
            if not isinstance(grayscale, bool):
                grayscale = True

        max_attempts = int(state.IMAGE_TIMEOUT / 0.5)  # 秒 → 尝试次数 (0.5s/次)
        for _ in range(max_attempts):
            self._chk()
            loc = self._find_cached(img, confidence, region=region_tuple,
                                    grayscale=grayscale)
            if loc:
                pa = get_pyautogui()
                if action == "click":
                    pa.click(loc.x, loc.y, clicks=click_count,
                             interval=0.2, duration=0.2, button=button)
                    log1("鼠标{}键点击了{}次{}".format(button, click_count, img))
                else:
                    pa.moveTo(loc.x, loc.y)
                    log1("成功找到图片{}".format(img))
                return loc
            log1("没有找到图片{}".format(img))
            time.sleep(0.5)

        # 超时提示 (保持与原命令一致的名称)
        cmd_name = ("区域点图" if region else "点图") if action == "click" \
            else ("区域找图" if region else "查找图片")
        log1("{}超时({}s)，跳过".format(cmd_name, state.IMAGE_TIMEOUT), "warning")
        return None

    def _find(self, row, z):
        """找图 — 找到目标图并悬停"""
        self._image_search_loop(row, z, action="hover")

    def _rfind(self, row, z):
        """区域找图 — 限定范围找图(更快)并悬停"""
        self._image_search_loop(row, z, action="hover", region=True)

    def _click(self, row, z):
        """点图 — 找到图片并点击"""
        self._image_search_loop(row, z, action="click")

    def _rclick(self, row, z):
        """区域点图 — 限定范围找图并点击"""
        button = "right" if str(row[8].value) == "右" else "left"
        click_count = _safe_get(row[9].value, 1, int)
        self._image_search_loop(row, z, action="click", region=True,
                                button=button, click_count=click_count)

    def _key(self, row, z):
        self._chk()
        count = _safe_get(row[2].value, 1, int)
        interval = row[3].value
        if not isinstance(interval, float):
            interval = 0.1
        pa = get_pyautogui()
        pa.press(str(row[1].value), count, interval)
        log1("按下 {} 键 {} 次".format(row[1].value, count))

    def _scroll(self,row,z):
        self._chk(); s=row[1].value
        if not isinstance(s,int): s=-300
        pa = get_pyautogui()
        pa.scroll(s); log1("滚动鼠标{}".format(s))

    def _input(self,row,z):
        self._chk(); v=row[1].value
        if v is None: v="没有设置输入内容"
        pc = get_pyperclip()
        pc.copy(v)
        pa = get_pyautogui()
        pa.hotkey("ctrl","v")
        log1("输入了{}".format(v))

    def _write(self, row, z):
        """
        写入命令 - 模拟真实键盘逐字输入（支持DD驱动增强）
        
        Excel格式: 写入,文本内容,按键间隔(秒),输入模式
        输入模式: auto(自动), direct(直接输入), simulate(模拟按键)
        
        Examples:
            写入,Hello World,0.05,auto      # 自动选择最佳方式
            写入,Test@#$%,0.02,direct       # 使用DD_str直接输入（最快）
            写入,你好世界,0.05,simulate     # 模拟按键（支持中文）
        """
        self._chk()
        
        # Get parameters
        text = row.args[0] if hasattr(row, 'args') and len(row.args) > 0 else ""
        if not text:
            text = str(row[1].value) if row[1].value else ""
        
        if not text:
            log1("警告: 写入内容为空", "warning")
            return
        
        # Get interval (default 0.05s)
        interval = row.args[1] if hasattr(row, 'args') and len(row.args) > 1 else 0.05
        if isinstance(interval, str):
            try:
                interval = float(interval)
            except ValueError:
                interval = 0.05
        
        # Get input mode (default "auto")
        mode = row.args[2] if hasattr(row, 'args') and len(row.args) > 2 else "auto"
        if isinstance(mode, str):
            mode = mode.strip().lower()
            if mode not in ["auto", "direct", "simulate"]:
                mode = "auto"
        else:
            mode = "auto"
        
        # Try DD backend first if enabled and mode allows
        dd = get_dd_backend()
        dd_success = False
        
        if dd and dd.enabled and mode in ["auto", "direct"]:
            # Use DD driver for faster input
            dd_mode = "auto" if mode == "auto" else mode
            dd_success = dd.type_text(text, mode=dd_mode)
            
            if dd_success:
                log1("✅ 使用DD驱动写入({}模式): {}".format(dd_mode, text[:30] + "..." if len(text) > 30 else text))
                return
        
        # Fallback to PyAutoGUI
        if not dd_success:
            pa = get_pyautogui()
            pa.typewrite(text, interval=interval)
            log1("⌨️ 使用PyAutoGUI写入: {}".format(text[:30] + "..." if len(text) > 30 else text))

    def _wait(self, row, z):
        raw_val = str(row[1].value) if row[1].value else "1.0"
        # 支持随机时间范围: "1.0-3.0" 表示 1.0~3.0 秒之间随机
        if '-' in raw_val:
            parts = raw_val.split('-')
            try:
                lo = float(parts[0].strip())
                hi = float(parts[1].strip())
                import random
                wait_time = random.uniform(lo, hi)
            except (ValueError, IndexError):
                wait_time = _safe_get(row[1].value, 1.0, float)
        else:
            wait_time = _safe_get(row[1].value, 1.0, float)
        log1("等待{:.2f}秒......".format(wait_time))
        elapsed = 0.0
        while elapsed < wait_time:
            self._chk()
            time.sleep(0.1)
            elapsed += 0.1
        log1("等待结束")

    def _hotkey(self,row,z):
        self._chk()
        pa = get_pyautogui()
        pa.hotkey(row[1].value,row[2].value)
        log1("按下了{}+{}键".format(row[1].value,row[2].value))

    def _coord(self, row, z):
        self._chk()
        button = "right" if str(row[3].value) == "右" else "left"
        count = _safe_get(row[4].value, 1, int)
        duration = row[5].value
        if isinstance(duration, str):
            duration = 0.1
        pa = get_pyautogui()
        # Convert coordinate values to integers to prevent file path errors
        x = int(row[1].value) if row[1].value is not None else 0
        y = int(row[2].value) if row[2].value is not None else 0
        pa.click(x, y, clicks=count, interval=0.2, duration=duration, button=button)
        log1("点击了{}次坐标{},{}".format(count, x, y))

    def _copy(self,row,z):
        self._chk()
        pa = get_pyautogui()
        pa.hotkey("ctrl","a"); pa.hotkey("ctrl","c")
        log1("执行了复制")

    def _paste(self,row,z):
        self._chk()
        pa = get_pyautogui()
        pa.hotkey("ctrl","a"); pa.hotkey("ctrl","v")
        log1("执行了粘贴")

    def _hover(self, row, z):
        self._chk()
        duration = _safe_get(row[3].value, 0.1, float)
        x = _safe_get(row[1].value, 0, int)
        y = _safe_get(row[2].value, 0, int)
        pa = get_pyautogui()
        pa.moveTo(x, y, duration=duration)
        log1("鼠标移动到了{},{}位置".format(x, y))

    def _drag(self, row, z):
        self._chk()
        duration = _safe_get(row[3].value, 0.1, float)
        pa = get_pyautogui()
        current_pos = pa.position()
        pa.dragTo(_safe_get(row[1].value, 0, int), _safe_get(row[2].value, 0, int),
                  duration=duration)
        log1("从{}拖拽到了{},{}位置".format(current_pos, row[1].value, row[2].value))

    def _shot(self, row, z):
        self._chk()
        name = str(row[1].value) if row[1].value is not None else "screenshot"
        name += datetime.datetime.now().strftime("%m%d%H%M%S")
        save_dir = (row[2].value if row[2].value and isinstance(row[2].value, str)
                    else os.path.join(os.path.dirname(state.CONFIG_PATH), "screenshots"))
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        pa = get_pyautogui()
        pa.screenshot("{}/{}.png".format(save_dir, name))
        log1("截图已保存至{}/{}.png".format(save_dir, name))

    def _kdown(self,row,z):
        self._chk()
        pa = get_pyautogui()
        pa.keyDown(row[1].value)
        log1("按下了{}键".format(row[1].value))

    def _kup(self,row,z):
        self._chk()
        pa = get_pyautogui()
        pa.keyUp(row[1].value)
        log1("释放了{}键".format(row[1].value))

    def _mrel(self, row, z):
        self._chk()
        pa = get_pyautogui()
        current_pos = pa.position()
        dx = _safe_get(row[1].value, 0, int)
        dy = _safe_get(row[2].value, 0, int)
        duration = _safe_get(row[3].value, 0.1, float)
        pa.dragRel(dx, dy, duration=duration)
        log1("从{}相对移动{},{}".format(current_pos, dx, dy))

    def _exec(self,row,z):
        """执行外部Python脚本文件
        
        ⚠️ 安全警告: 此命令会执行任意Python代码，仅用于可信脚本！
        建议: 使用沙盒环境或白名单机制限制可执行的文件路径
        """
        self._chk()
        
        # Get script file path
        if hasattr(row, 'args'):
            script_name = row.args[0] if len(row.args) > 0 else ""
        else:
            script_name = str(row[1].value) if row[1].value else ""
        
        if not script_name:
            log1("错误: 未指定脚本文件名", "error")
            return
        
        cp = "{}/{}.txt".format(z, script_name)
        
        # Security check: prevent directory traversal
        abs_path = os.path.abspath(cp)
        base_dir = os.path.abspath(z)
        if not abs_path.startswith(base_dir):
            log1("安全错误: 禁止访问脚本目录外的文件", "error")
            return
        
        # Check if file exists
        if not os.path.exists(cp):
            log1("错误: 脚本文件不存在: {}".format(cp), "error")
            return
        
        try:
            with open(cp, encoding="utf-8") as f:
                code = f.read()
            
            # Execute with restricted globals/locals for safety
            # Only allow basic operations, no import/module access
            safe_globals = {
                '__builtins__': {
                    'print': print,
                    'len': len,
                    'str': str,
                    'int': int,
                    'float': float,
                    'bool': bool,
                    'list': list,
                    'dict': dict,
                    'tuple': tuple,
                    'set': set,
                    'range': range,
                    'enumerate': enumerate,
                    'zip': zip,
                    'map': map,
                    'filter': filter,
                    'sum': sum,
                    'min': min,
                    'max': max,
                    'abs': abs,
                    'round': round,
                }
            }
            safe_locals = {}
            
            exec(code, safe_globals, safe_locals)
            log1("✅ 执行了脚本: {}".format(cp))
        except Exception as e:
            log1("❌ 代码执行失败 {}: {}".format(cp, e), "error")

    # ======================================================================
    # SECTION: Window Management Methods (新增窗口管理方法)
    # ======================================================================

    def _activate_window(self, row, z):
        """激活指定标题的窗口（支持模糊匹配）"""
        self._chk()
        # Support both ScriptData objects and xlrd rows
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        try:
            win32gui, win32con = get_win32gui()
            
            # Find window by title pattern (supports regex)
            hwnd = self._find_window_by_pattern(title_pattern)
            
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            
            # Bring window to foreground
            win32gui.SetForegroundWindow(hwnd)
            log1("成功激活窗口: {}".format(title_pattern))
            
        except Exception as e:
            log1("激活窗口失败: {}".format(e), "error")

    def _activate_window_by_title(self, title_pattern):
        """激活指定标题的窗口（简化接口，供 main_run 窗口绑定使用）。
        
        Args:
            title_pattern: 窗口标题（支持模糊匹配）
        """
        if not title_pattern:
            return
        try:
            win32gui, win32con = get_win32gui()
            hwnd = self._find_window_by_pattern(title_pattern)
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            win32gui.SetForegroundWindow(hwnd)
            log1("成功激活窗口: {}".format(title_pattern))
        except Exception as e:
            log1("激活窗口失败: {}".format(e), "error")

    def _close_window(self, row, z):
        """关闭指定窗口"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        try:
            win32gui, win32con = get_win32gui()
            
            hwnd = self._find_window_by_pattern(title_pattern)
            
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            
            # Send WM_CLOSE message
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            log1("已发送关闭命令到窗口: {}".format(title_pattern))
            
        except Exception as e:
            log1("关闭窗口失败: {}".format(e), "error")

    def _minimize_window(self, row, z):
        """最小化指定窗口"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        try:
            win32gui, win32con = get_win32gui()
            
            hwnd = self._find_window_by_pattern(title_pattern)
            
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            
            # Minimize window
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            log1("已最小化窗口: {}".format(title_pattern))
            
        except Exception as e:
            log1("最小化窗口失败: {}".format(e), "error")

    def _maximize_window(self, row, z):
        """最大化指定窗口"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        try:
            win32gui, win32con = get_win32gui()
            
            hwnd = self._find_window_by_pattern(title_pattern)
            
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            
            # Maximize window
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            log1("已最大化窗口: {}".format(title_pattern))
            
        except Exception as e:
            log1("最大化窗口失败: {}".format(e), "error")

    def _get_window_position(self, row, z):
        """获取窗口位置并保存到变量"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
            var_x = row.args[1] if len(row.args) > 1 and row.args[1] else "win_x"
            var_y = row.args[2] if len(row.args) > 2 and row.args[2] else "win_y"
            var_w = row.args[3] if len(row.args) > 3 and row.args[3] else "win_width"
            var_h = row.args[4] if len(row.args) > 4 and row.args[4] else "win_height"
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
            var_x = str(row[2].value) if row[2].value else "win_x"
            var_y = str(row[3].value) if row[3].value else "win_y"
            var_w = str(row[4].value) if row[4].value else "win_width"
            var_h = str(row[5].value) if row[5].value else "win_height"
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        try:
            win32gui, win32con = get_win32gui()
            
            hwnd = self._find_window_by_pattern(title_pattern)
            
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            
            # Get window position and size
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            
            # Save to variables
            self.variables[var_x] = left
            self.variables[var_y] = top
            self.variables[var_w] = width
            self.variables[var_h] = height
            
            log1("窗口位置: X={}, Y={}, 宽={}, 高={}".format(left, top, width, height))
            
        except Exception as e:
            log1("获取窗口位置失败: {}".format(e), "error")

    def _window_coord(self, row, z):
        """相对于窗口左上角的坐标点击（窗口移动后仍准确）"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
            rel_x = int(row.args[1]) if len(row.args) > 1 and row.args[1] else 0
            rel_y = int(row.args[2]) if len(row.args) > 2 and row.args[2] else 0
            lr = "right" if (len(row.args) > 3 and str(row.args[3]) == "右") else "left"
            cs = int(row.args[4]) if len(row.args) > 4 and row.args[4] else 1
            ys = float(row.args[5]) if len(row.args) > 5 and row.args[5] else 0.1
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
            rel_x = int(row[2].value) if row[2].value is not None else 0
            rel_y = int(row[3].value) if row[3].value is not None else 0
            lr = "right" if str(row[4].value) == "右" else "left"
            cs = _safe_get(row[5].value, 1, int)
            ys = row[6].value if row[6].value else 0.1
            if isinstance(ys, str):
                ys = 0.1
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        try:
            win32gui, win32con = get_win32gui()
            hwnd = self._find_window_by_pattern(title_pattern)
            if hwnd == 0:
                log1("警告: 未找到匹配的窗口 '{}'".format(title_pattern), "warning")
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            abs_x = left + rel_x
            abs_y = top + rel_y
            pa = get_pyautogui()
            pa.click(abs_x, abs_y, clicks=cs, interval=0.2, duration=ys, button=lr)
            log1("窗口坐标点击: 窗口'{}' 相对({},{}) 绝对({},{})".format(
                title_pattern, rel_x, rel_y, abs_x, abs_y))
        except Exception as e:
            log1("窗口坐标点击失败: {}".format(e), "error")

    def _wait_for_window(self, row, z):
        """等待窗口出现或消失"""
        self._chk()
        if hasattr(row, 'args'):
            title_pattern = row.args[0] if len(row.args) > 0 else ""
            timeout = float(row.args[1]) if len(row.args) > 1 and row.args[1] else 10.0
            should_exist = row.args[2].lower() != "不存在" if len(row.args) > 2 and row.args[2] else True
        else:
            title_pattern = str(row[1].value) if row[1].value else ""
            timeout = _safe_get(row[2].value, 10, float)
            should_exist = str(row[3].value).lower() != "不存在" if row[3].value else True
        
        if not title_pattern:
            log1("错误: 未指定窗口标题", "error")
            return
        
        start_time = time.time()
        found = False
        
        while time.time() - start_time < timeout:
            if state.quit2:
                break
            
            hwnd = self._find_window_by_pattern(title_pattern)
            found = (hwnd != 0)
            
            if should_exist and found:
                log1("窗口 '{}' 已出现".format(title_pattern))
                return
            elif not should_exist and not found:
                log1("窗口 '{}' 已消失".format(title_pattern))
                return
            
            time.sleep(0.5)
        
        # Timeout
        if should_exist:
            log1("超时: 窗口 '{}' 未在 {}秒内出现".format(title_pattern, timeout), "warning")
        else:
            log1("超时: 窗口 '{}' 未在 {}秒内消失".format(title_pattern, timeout), "warning")

    def _set_variable(self, row, z):
        """设置变量值"""
        self._chk()
        if hasattr(row, 'args'):
            var_name = row.args[0] if len(row.args) > 0 else ""
            var_value = row.args[1] if len(row.args) > 1 else None
        else:
            var_name = str(row[1].value) if row[1].value else ""
            var_value = row[2].value
        
        if not var_name:
            log1("错误: 未指定变量名", "error")
            return
        
        # Try to convert to number if possible
        if isinstance(var_value, str):
            try:
                if '.' in var_value:
                    var_value = float(var_value)
                else:
                    var_value = int(var_value)
            except ValueError:
                pass  # Keep as string
        
        self.variables[var_name] = var_value
        log1("设置变量 {}: {}".format(var_name, var_value))

    def _read_clipboard(self, row, z):
        """读取剪贴板内容到变量"""
        self._chk()
        if hasattr(row, 'args'):
            var_name = row.args[0] if len(row.args) > 0 and row.args[0] else "clipboard"
        else:
            var_name = str(row[1].value) if row[1].value else "clipboard"
        
        try:
            pc = get_pyperclip()
            content = pc.paste()
            self.variables[var_name] = content
            log1("读取剪贴板到变量 {}: {}字符".format(var_name, len(content)))
        except Exception as e:
            log1("读取剪贴板失败: {}".format(e), "error")

    def _string_operation(self, row, z):
        """字符串处理操作"""
        self._chk()
        if hasattr(row, 'args'):
            source_var = row.args[0] if len(row.args) > 0 else ""
            operation = row.args[1] if len(row.args) > 1 else ""
            param = row.args[2] if len(row.args) > 2 else ""
            target_var = row.args[3] if len(row.args) > 3 and row.args[3] else "result"
        else:
            source_var = str(row[1].value) if row[1].value else ""
            operation = str(row[2].value) if row[2].value else ""
            param = str(row[3].value) if row[3].value else ""
            target_var = str(row[4].value) if row[4].value else "result"
        
        if not source_var or source_var not in self.variables:
            log1("错误: 源变量 '{}' 不存在".format(source_var), "error")
            return
        
        source_value = str(self.variables[source_var])
        result = source_value
        
        try:
            if operation == "截取":
                # Format: start:end or start
                parts = param.split(":")
                if len(parts) == 2:
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if parts[1] else len(source_value)
                    result = source_value[start:end]
                elif len(parts) == 1:
                    start = int(parts[0])
                    result = source_value[start:]
            elif operation == "替换":
                # Format: old_text:new_text
                parts = param.split(":")
                if len(parts) == 2:
                    result = source_value.replace(parts[0], parts[1])
            elif operation == "转大写":
                result = source_value.upper()
            elif operation == "转小写":
                result = source_value.lower()
            elif operation == "去空格":
                result = source_value.strip()
            else:
                log1("警告: 未知的字符串操作 '{}'".format(operation), "warning")
                return
            
            self.variables[target_var] = result
            log1("字符串处理结果: {}".format(result))
            
        except Exception as e:
            log1("字符串处理失败: {}".format(e), "error")

    def _math_operation(self, row, z):
        """数学运算"""
        self._chk()
        if hasattr(row, 'args'):
            expression = row.args[0] if len(row.args) > 0 else ""
            result_var = row.args[1] if len(row.args) > 1 and row.args[1] else "result"
        else:
            expression = str(row[1].value) if row[1].value else ""
            result_var = str(row[2].value) if row[2].value else "result"
        
        if not expression:
            log1("错误: 未指定表达式", "error")
            return
        
        try:
            # Build variables dict from ${var} references and engine variables
            variables = {}
            var_refs = _VAR_REF_RE.findall(expression)
            for vn in var_refs:
                vn = vn.strip()
                variables[vn] = self.variables.get(vn, 0)

            # Strip ${} wrappers so the AST sees plain variable names
            evaluated = _VAR_REF_RE.sub(r'\1', expression)
            result = safe_eval_math(evaluated, variables)

            self.variables[result_var] = result
            log1("数学运算结果: {} = {}".format(expression, result))

        except EvalError as e:
            log1("数学运算错误: {}".format(e), "error")
        except Exception as e:
            log1("数学运算失败: {}".format(e), "error")

    # ======================================================================
    # SECTION: OCR Commands (OCR文字识别命令)
    # ======================================================================

    def _ocr_read(self, row, z):
        """OCR识别屏幕区域文字并存入变量"""
        self._chk()
        # Support both ScriptData and xlrd row
        if hasattr(row, 'args'):
            left = _safe_get(row.args[0] if len(row.args) > 0 else None, 0, int)
            top = _safe_get(row.args[1] if len(row.args) > 1 else None, 0, int)
            width = _safe_get(row.args[2] if len(row.args) > 2 else None, 800, int)
            height = _safe_get(row.args[3] if len(row.args) > 3 else None, 600, int)
            var_name = row.args[4] if len(row.args) > 4 and row.args[4] else "ocr_text"
        else:
            left = _safe_get(row[1].value, 0, int)
            top = _safe_get(row[2].value, 0, int)
            width = _safe_get(row[3].value, 800, int)
            height = _safe_get(row[4].value, 600, int)
            var_name = str(row[5].value) if row[5].value else "ocr_text"

        log1("OCR识别区域: ({},{},{},{}) -> 变量 '{}'".format(left, top, width, height, var_name))

        try:
            from ocr_backend import ocr_read_region
            text = ocr_read_region((left, top, width, height), z)
            if text:
                self.variables[var_name] = text
                preview = text[:80].replace('\n', '↵') + ("..." if len(text) > 80 else "")
                log1("OCR结果 [{}]: {}".format(var_name, preview))
            else:
                self.variables[var_name] = ""
                log1("OCR未识别到文字", "warning")
        except Exception as e:
            log1("OCR识别失败: {}".format(e), "error")
            self.variables[var_name] = ""

    def _ocr_wait_text(self, row, z):
        """等待指定文字出现或消失"""
        self._chk()
        if hasattr(row, 'args'):
            target_text = row.args[0] if len(row.args) > 0 else ""
            timeout = _safe_get(row.args[1] if len(row.args) > 1 else None, 10.0, float)
            should_exist = (str(row.args[2]).lower() != "不存在") if (len(row.args) > 2 and row.args[2]) else True
            left = _safe_get(row.args[3] if len(row.args) > 3 else None, 0, int)
            top = _safe_get(row.args[4] if len(row.args) > 4 else None, 0, int)
            width = _safe_get(row.args[5] if len(row.args) > 5 else None, 800, int)
            height = _safe_get(row.args[6] if len(row.args) > 6 else None, 600, int)
        else:
            target_text = str(row[1].value) if row[1].value else ""
            timeout = _safe_get(row[2].value, 10, float)
            should_exist = str(row[3].value).lower() != "不存在" if row[3].value else True
            left = _safe_get(row[4].value, 0, int)
            top = _safe_get(row[5].value, 0, int)
            width = _safe_get(row[6].value, 800, int)
            height = _safe_get(row[7].value, 600, int)

        if not target_text:
            log1("错误: 未指定等待文字", "error")
            return

        region = (left, top, width, height) if (width > 0 and height > 0) else None
        log1("等待文字 '{}' {}...".format(target_text, "出现" if should_exist else "消失"))

        start_time = time.time()
        from ocr_backend import ocr_read_region

        while time.time() - start_time < timeout:
            if state.quit2:
                break
            self._chk()

            text = ocr_read_region(region, z)
            found = target_text.lower() in text.lower() if text else False

            if should_exist and found:
                log1("文字 '{}' 已出现".format(target_text))
                return
            elif not should_exist and not found:
                log1("文字 '{}' 已消失".format(target_text))
                return

            time.sleep(0.5)

        status = "出现" if should_exist else "消失"
        log1("超时: 文字 '{}' 未在 {}秒内{}".format(target_text, timeout, status), "warning")

    def _ocr_click_text(self, row, z):
        """找到文字位置并点击"""
        self._chk()
        if hasattr(row, 'args'):
            target_text = row.args[0] if len(row.args) > 0 else ""
            confidence = _safe_get(row.args[1] if len(row.args) > 1 else None, 0.7, float)
            button = "右" if (len(row.args) > 2 and str(row.args[2]) == "右") else "左"
            left = _safe_get(row.args[3] if len(row.args) > 3 else None, 0, int)
            top = _safe_get(row.args[4] if len(row.args) > 4 else None, 0, int)
            width = _safe_get(row.args[5] if len(row.args) > 5 else None, 800, int)
            height = _safe_get(row.args[6] if len(row.args) > 6 else None, 600, int)
        else:
            target_text = str(row[1].value) if row[1].value else ""
            confidence = _safe_get(row[2].value, 0.7, float)
            button = "右" if str(row[3].value) == "右" else "左"
            left = _safe_get(row[4].value, 0, int)
            top = _safe_get(row[5].value, 0, int)
            width = _safe_get(row[6].value, 800, int)
            height = _safe_get(row[7].value, 600, int)

        if not target_text:
            log1("错误: 未指定点击文字", "error")
            return

        region = (left, top, width, height) if (width > 0 and height > 0) else None
        log1("查找并点击文字: '{}' (置信度: {})".format(target_text, confidence))

        from ocr_backend import ocr_find_text_position
        pos = ocr_find_text_position(target_text, region, confidence)

        if pos:
            pa = get_pyautogui()
            pa.click(pos[0], pos[1], button=button)
            log1("点击文字 '{}' 位置 ({}, {})".format(target_text, pos[0], pos[1]))
        else:
            log1("未找到文字 '{}'".format(target_text), "warning")

    def _find_window_by_pattern(self, pattern):
        """
        Find window handle by title pattern (supports regex)
        Returns hwnd (0 if not found)
        """
        try:
            win32gui, _ = get_win32gui()
            
            # First try exact match
            hwnd = win32gui.FindWindow(None, pattern)
            if hwnd != 0:
                return hwnd
            
            # If not found, try partial match using EnumWindows
            matching_hwnds = []
            
            def enum_callback(hwnd, lParam):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        title = win32gui.GetWindowText(hwnd)
                        if title and re.search(pattern, title, re.IGNORECASE):
                            matching_hwnds.append((hwnd, title))
                    except:
                        pass
                return True
            
            win32gui.EnumWindows(enum_callback, 0)
            
            if matching_hwnds:
                # Return the first match
                log1("找到 {} 个匹配窗口，使用第一个: {}".format(
                    len(matching_hwnds), matching_hwnds[0][1]))
                return matching_hwnds[0][0]
            
            return 0
            
        except Exception as e:
            log1("查找窗口失败: {}".format(e), "error")
            return 0

engine = ExecutionEngine()

# Wire command handlers to registry
commands._set_handler("区域找图", engine._rfind)
commands._set_handler("找图", engine._find)
commands._set_handler("点图", engine._click)
commands._set_handler("区域点图", engine._rclick)
commands._set_handler("按键", engine._key)
commands._set_handler("滚轮", engine._scroll)
commands._set_handler("输入", engine._input)
commands._set_handler("写入", engine._write)  # 新增写入命令（支持DD驱动）
commands._set_handler("等待", engine._wait)
commands._set_handler("热键", engine._hotkey)
commands._set_handler("坐标", engine._coord)
commands._set_handler("复制", engine._copy)
commands._set_handler("粘贴", engine._paste)
commands._set_handler("悬停", engine._hover)
commands._set_handler("拖拽", engine._drag)
commands._set_handler("截屏", engine._shot)
commands._set_handler("按下", engine._kdown)
commands._set_handler("释放", engine._kup)
commands._set_handler("相移", engine._mrel)
commands._set_handler("代码", engine._exec)

# Window Management Commands (新增窗口管理命令注册)
commands._set_handler("激活窗口", engine._activate_window)
commands._set_handler("关闭窗口", engine._close_window)
commands._set_handler("最小化窗口", engine._minimize_window)
commands._set_handler("最大化窗口", engine._maximize_window)
commands._set_handler("获取窗口位置", engine._get_window_position)
commands._set_handler("等待窗口", engine._wait_for_window)
commands._set_handler("窗口坐标", engine._window_coord)

# Variable Enhancement Commands (变量增强命令注册)
commands._set_handler("设置变量", engine._set_variable)
commands._set_handler("读取剪贴板", engine._read_clipboard)
commands._set_handler("字符串处理", engine._string_operation)
commands._set_handler("数学运算", engine._math_operation)

# OCR Commands (OCR文字识别命令注册)
commands._set_handler("识别文字", engine._ocr_read)
commands._set_handler("等待文字", engine._ocr_wait_text)
commands._set_handler("点击文字", engine._ocr_click_text)

# AI 增强命令注册 (AI Enhancement Commands)
try:
    from ai_enhance import _handler_ai_find, _handler_ai_describe, _handler_ai_optimize
    commands._set_handler("AI找图", _handler_ai_find)
    commands._set_handler("AI识别界面", _handler_ai_describe)
    commands._set_handler("AI优化建议", _handler_ai_optimize)
except ImportError:
    pass

# 工作流命令注册 (Workflow Commands)
try:
    from workflow import _handler_workflow_run, _handler_workflow_setvar
    commands._set_handler("运行工作流", _handler_workflow_run)
    commands._set_handler("工作流变量", _handler_workflow_setvar)
except ImportError:
    pass

# 浏览器自动化命令注册 (Browser Automation Commands, 可选安装 playwright)
try:
    from browser_backend import (
        _browser_navigate, _browser_click, _browser_input,
        _browser_wait_element, _browser_screenshot,
    )
    commands._set_handler("打开网页", _browser_navigate)
    commands._set_handler("浏览器点击", _browser_click)
    commands._set_handler("浏览器输入", _browser_input)
    commands._set_handler("等待元素", _browser_wait_element)
    commands._set_handler("浏览器截图", _browser_screenshot)
except ImportError:
    pass

# ── 插件系统: 自动发现并加载 plugins/ 目录 ──
try:
    from plugins import discover_and_load
    discover_and_load()
    # 插件已在 commands.register() 中传入 handler，无需额外绑定
    # 如需覆盖内置 handler，插件应在 register 时提供非 None handler
except Exception:
    pass
