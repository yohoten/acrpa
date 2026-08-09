"""核心功能回归测试 (临时诊断工具) — 验证本次修复 + 核心模块逻辑。"""
import os, sys, json, tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "src"))

FAILURES = []

def check(name, cond, detail=""):
    status = "✓" if cond else "✗"
    if not cond:
        FAILURES.append(name)
    print("  {} {} {}".format(status, name, detail if not cond else ""))

# ── 1. safe_eval 条件/数学评估 ──
print("[1] safe_eval 测试")
from safe_eval import safe_eval_condition, safe_eval_math, EvalError
# 注: engine._evaluate_condition 会先做 ${var} → var 替换, 此处直接传替换后表达式
check("条件 x > 5", safe_eval_condition("x > 5", {"x": 10}) is True)
check("条件 x == 1", safe_eval_condition("x == 1", {"x": 1}) is True)
check("数学 x + y * 2", safe_eval_math("x + y * 2", {"x": 3, "y": 4}) == 11)
check("数学 (a+b)/2", safe_eval_math("(a + b) / 2", {"a": 10, "b": 4}) == 7.0)
try:
    safe_eval_math("__import__('os')", {})
    check("拒绝危险表达式 __import__", False, "未抛错")
except EvalError:
    check("拒绝危险表达式 __import__", True)
try:
    safe_eval_condition("x > 1 and x < 10", {"x": 5}) is True
    check("复合 and 条件", True)
except Exception as e:
    check("复合 and 条件", False, str(e))

# ── 2. ScriptData 序列化 ──
print("[2] ScriptData 测试")
from scriptdata import ScriptData
sd = ScriptData("坐标", ["100", "200", "左", "1", "0.1", None, None, None, None])
check("args 中 None → 空串", sd.args[5] == "")
check("to_tuple 长度", len(sd.to_tuple()) == 10)
sd2 = ScriptData.from_xlrd_row_values(["等待", "2", "", "", "", "", "", "", "", ""])
check("from_xlrd_row_values", sd2.cmd_type == "等待" and len(sd2.args) == 9)

# ── 3. commands 注册表 ──
print("[3] commands 注册表测试")
import commands
from engine import engine
check("命令总数 >= 50", len(commands.list_all()) >= 50)
check("找图 handler 已绑定", commands.get_handler("找图") is not None)
check("AI找图 handler 已绑定", commands.get_handler("AI找图") is not None)
check("运行工作流 handler 已绑定", commands.get_handler("运行工作流") is not None)
check("打开网页 handler 已绑定", commands.get_handler("打开网页") is not None)

# ── 4. version_manager 保存/恢复/对比 ──
print("[4] version_manager 测试")
from version_manager import VersionManager
tmp_db = os.path.join(tempfile.gettempdir(), "acrpa_test_versions.db")
if os.path.exists(tmp_db): os.remove(tmp_db)
vm = VersionManager(db_path=tmp_db)
rows1 = [ScriptData("等待", ["1"] + [""]*8), ScriptData("坐标", ["100","200","左","1","0.1","","","",""])]
rows2 = [ScriptData("等待", ["2"] + [""]*8), ScriptData("坐标", ["300","400","左","1","0.1","","","",""]),
         ScriptData("按键", ["enter","1","0.1","","","","","",""])]
v1 = vm.save_version("/test/script.xls", rows1, "v1")
v2 = vm.save_version("/test/script.xls", rows2, "v2")
check("保存版本 v1", v1 == 1)
check("保存版本 v2", v2 == 2)
hist = vm.get_history("/test/script.xls")
check("历史 2 条", len(hist) == 2)
restored = vm.restore_version("/test/script.xls", 1)
check("恢复 v1", restored is not None and restored[0].args[0] == "1")
diff = vm.diff_versions("/test/script.xls", 1, 2)
check("diff 含变更", "变更:" in diff)
os.remove(tmp_db)

# ── 5. scheduler calc_next_run ──
print("[5] scheduler 测试")
import state
state.SCHED_REPEAT_MODE = "daily"
state.SCHED_HOUR = 8
state.SCHED_MINUTE = 30
from scheduler import calc_next_run
candidate = calc_next_run()
check("每日调度计算", candidate is not None and candidate.minute == 30)

# ── 6. workflow 解析 ──
print("[6] workflow 解析测试")
from workflow import parse_workflow, _normalize_step, WorkflowParseError
wf_path = os.path.join(tempfile.gettempdir(), "acrpa_test_wf.json")
wf_def = {
    "name": "测试工作流",
    "steps": [
        "a.xls",
        {"type": "wait", "seconds": 1},
        {"type": "condition", "if": "${ok}", "then": "t.xls", "else": "e.xls"},
        {"type": "parallel", "steps": ["p1.xls", "p2.xls"]},
    ]
}
with open(wf_path, "w", encoding="utf-8") as f:
    json.dump(wf_def, f)
parsed = parse_workflow(wf_path)
check("工作流 4 步骤", len(parsed["steps"]) == 4)
check("简写脚本规范化", parsed["steps"][0] == {"type": "script", "path": "a.xls"})
check("condition 分支规范化", isinstance(parsed["steps"][2]["then"], dict))
check("parallel 子步骤", len(parsed["steps"][3]["steps"]) == 2)
os.remove(wf_path)

# ── 7. plugins 加载 ──
print("[7] plugins 测试")
from plugins import discover_and_load, list_plugins
discover_and_load()
plist = list_plugins()
print("  已加载插件: {}".format(plist))
check("list_plugins 可执行", isinstance(plist, list))

# ── 8. updater 版本比较 ──
print("[8] updater 版本比较测试")
from updater import _compare_versions
check("v1.0 < v1.1", _compare_versions("0.1.24", "0.1.25") is True)
check("v1.1 > v1.0", _compare_versions("0.1.25", "0.1.24") is False)

# ── 汇总 ──
print("\n" + "=" * 50)
if FAILURES:
    print("FAILED ({})：{}".format(len(FAILURES), ", ".join(FAILURES)))
else:
    print("全部通过 ✓")
sys.exit(1 if FAILURES else 0)
