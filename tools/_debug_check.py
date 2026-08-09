"""全项目语法编译检查 + 导入冒烟测试 (临时诊断工具)。"""
import os, sys, glob, importlib, traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "src"))

def check_syntax():
    """编译检查所有 .py 文件。"""
    files = (glob.glob(os.path.join(BASE, "src", "*.py"))
             + glob.glob(os.path.join(BASE, "src", "plugins", "*.py"))
             + glob.glob(os.path.join(BASE, "tools", "*.py"))
             + [os.path.join(BASE, "build.py"), os.path.join(BASE, "run.py")])
    failed = 0
    for f in sorted(files):
        try:
            with open(f, "rb") as fh:
                compile(fh.read(), f, "exec")
        except SyntaxError as e:
            failed += 1
            print("[SYNTAX ERROR] {}:{} {}".format(f, e.lineno, e.msg))
    print("[SYNTAX] checked {} files, {} failed".format(len(files), failed))
    return failed

def check_imports():
    """尝试导入不依赖 GUI 的模块。"""
    modules = ["state", "utils", "scriptdata", "commands", "safe_eval",
               "ai_client", "engine", "scheduler", "recorder", "updater",
               "marketplace", "templates", "version_manager", "workflow",
               "dd_backend", "ocr_backend", "browser_backend", "ai_enhance"]
    failed = 0
    for m in modules:
        try:
            importlib.import_module(m)
            print("[IMPORT OK] {}".format(m))
        except Exception as e:
            failed += 1
            print("[IMPORT FAIL] {}: {}".format(m, e))
    print("[IMPORT] {}/{} modules imported".format(len(modules) - failed, len(modules)))
    return failed

if __name__ == "__main__":
    s = check_syntax()
    i = check_imports()
    print("\nDone. syntax_failed={} import_failed={}".format(s, i))
