"""
ACRPA 代码检查和打包诊断工具

用法:
  python tools/diagnose.py              # 完整诊断
  python tools/diagnose.py --syntax     # 仅语法检查
  python tools/diagnose.py --import     # 仅导入检查
  python tools/diagnose.py --tray       # 仅托盘功能检查
  python tools/diagnose.py --icon       # 仅图标设置检查
  python tools/diagnose.py --build      # 仅打包准备检查
"""
import os
import sys
import ast
import subprocess
import ctypes

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_success(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.RESET}")

def print_error(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.RESET}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.RESET}")

def print_info(msg):
    print(f"{Colors.BLUE}ℹ️  {msg}{Colors.RESET}")

def print_header(msg):
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{msg}{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")

# ======================================================================
# 语法检查
# ======================================================================

def check_syntax():
    """检查核心文件的语法"""
    print_header("语法检查")
    
    files = [
        "src/tray.py",
        "src/ACRPA.py",
        "src/state.py",
        "src/engine.py",
        "src/commands.py",
    ]
    
    all_ok = True
    for filepath in files:
        full_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filepath)
        if not os.path.exists(full_path):
            print_error(f"{filepath} 不存在")
            all_ok = False
            continue
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                ast.parse(f.read())
            print_success(f"{filepath}")
        except SyntaxError as e:
            print_error(f"{filepath}: {e.msg} (line {e.lineno})")
            all_ok = False
    
    return all_ok

# ======================================================================
# 导入检查
# ======================================================================

def check_imports():
    """检查模块导入是否正常"""
    print_header("导入检查")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    sys.path.insert(0, os.path.join(base_dir, "src"))
    
    modules = [
        ("tray", "SystemTray"),
        ("state", None),
        ("utils", None),
        ("engine", None),
    ]
    
    all_ok = True
    for module_name, class_name in modules:
        try:
            module = __import__(module_name)
            if class_name:
                cls = getattr(module, class_name, None)
                if cls:
                    print_success(f"{module_name}.{class_name}")
                else:
                    print_error(f"{module_name}.{class_name} 不存在")
                    all_ok = False
            else:
                print_success(f"{module_name}")
        except ImportError as e:
            print_error(f"{module_name}: {e}")
            all_ok = False
    
    return all_ok

# ======================================================================
# 托盘功能检查
# ======================================================================

def check_tray():
    """检查托盘功能完整性"""
    print_header("托盘功能检查")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    sys.path.insert(0, os.path.join(base_dir, "src"))
    
    all_ok = True
    
    # 1. 检查 SystemTray 类
    try:
        from tray import SystemTray
        print_success("SystemTray 类可导入")
        
        # 检查公开 API
        required_attrs = ['active', 'create', 'destroy', 'show_balloon', 'update_tip']
        for attr in required_attrs:
            if hasattr(SystemTray, attr):
                print_success(f"  - {attr}")
            else:
                print_error(f"  - {attr} 缺失")
                all_ok = False
    except Exception as e:
        print_error(f"SystemTray 导入失败: {e}")
        all_ok = False
    
    # 2. 检查图标文件
    icon_paths = [
        os.path.join(base_dir, "res", "automation.ico"),
        os.path.join(base_dir, "automation.ico"),
    ]
    
    icon_found = False
    for icon_path in icon_paths:
        if os.path.exists(icon_path):
            size = os.path.getsize(icon_path)
            print_success(f"图标文件存在: {icon_path} ({size} bytes)")
            icon_found = True
            break
    
    if not icon_found:
        print_error("图标文件不存在")
        all_ok = False
    
    # 3. 检查 ACRPA.py 中的托盘集成
    acrpa_path = os.path.join(base_dir, "src", "ACRPA.py")
    if os.path.exists(acrpa_path):
        with open(acrpa_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        checks = [
            ("from tray import SystemTray", "托盘模块导入"),
            ("_ensure_tray()", "托盘创建函数"),
            ("_destroy_tray()", "托盘销毁函数"),
            ("_restore_from_tray()", "托盘恢复回调"),
            ("_quit_from_tray()", "托盘退出回调"),
        ]
        
        for pattern, desc in checks:
            if pattern in content:
                print_success(f"ACRPA.py: {desc}")
            else:
                print_warning(f"ACRPA.py: {desc} 未找到")
    
    return all_ok

# ======================================================================
# 图标设置检查
# ======================================================================

def check_icon():
    """检查图标设置是否正确实现"""
    print_header("图标设置检查")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    all_ok = True
    
    # 1. 检查图标文件
    icon_paths = [
        os.path.join(base_dir, "res", "automation.ico"),
        os.path.join(base_dir, "automation.ico"),
    ]
    
    icon_found = False
    for icon_path in icon_paths:
        if os.path.exists(icon_path):
            size = os.path.getsize(icon_path)
            print_success(f"图标文件存在: {icon_path} ({size} bytes)")
            
            # 验证 ICO 格式
            with open(icon_path, 'rb') as f:
                header = f.read(4)
                if header[:2] == b'\x00\x00':
                    print_success("ICO 文件格式正确")
                else:
                    print_error("文件格式不是有效的 ICO")
                    all_ok = False
            
            icon_found = True
            break
    
    if not icon_found:
        print_error("图标文件不存在")
        return False
    
    # 2. 测试 LoadImageW API
    print_info("测试 LoadImageW API...")
    try:
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        
        hicon = ctypes.windll.user32.LoadImageW(
            None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        
        if hicon:
            print_success(f"LoadImageW 成功加载图标 (HICON: {hicon})")
            ctypes.windll.user32.DestroyIcon(hicon)
        else:
            print_error("LoadImageW 加载失败")
            all_ok = False
    except Exception as e:
        print_error(f"LoadImageW 测试失败: {e}")
        all_ok = False
    
    # 3. 检查代码实现
    acrpa_path = os.path.join(base_dir, "src", "ACRPA.py")
    tray_path = os.path.join(base_dir, "src", "tray.py")
    
    if os.path.exists(acrpa_path):
        with open(acrpa_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        checks = [
            ("SetCurrentProcessExplicitAppUserModelID", "AppUserModelID 设置"),
            ("WM_SETICON", "WM_SETICON 消息"),
            ("SendMessageW(hwnd, WM_SETICON, ICON_BIG", "大图标设置 (任务栏)"),
            ("SendMessageW(hwnd, WM_SETICON, ICON_SMALL", "小图标设置 (标题栏)"),
            ("LoadImageW", "LoadImageW API"),
        ]
        
        print_success("ACRPA.py 代码检查:")
        for pattern, desc in checks:
            if pattern in content:
                print_success(f"  ✅ {desc}")
            else:
                print_error(f"  ❌ {desc} 缺失")
                all_ok = False
    
    if os.path.exists(tray_path):
        with open(tray_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if "WM_SETICON" in content:
            print_success("tray.py: WM_SETICON 实现")
        else:
            print_warning("tray.py: 未使用 WM_SETICON")
    
    return all_ok

# ======================================================================
# 打包准备检查
# ======================================================================

def check_build():
    """检查打包准备工作"""
    print_header("打包准备检查")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    all_ok = True
    
    # 1. 检查 PyInstaller
    try:
        import PyInstaller
        print_success(f"PyInstaller {PyInstaller.__version__} 已安装")
    except ImportError:
        print_error("PyInstaller 未安装")
        print_info("运行: pip install pyinstaller")
        all_ok = False
    
    # 2. 检查 UPX
    upx_paths = [
        r"H:\UPX\upx.exe",
        r"C:\UPX\upx.exe",
        os.path.join(base_dir, "upx.exe"),
    ]
    
    upx_found = False
    for upx_path in upx_paths:
        if os.path.exists(upx_path):
            print_success(f"UPX 找到: {upx_path}")
            upx_found = True
            break
    
    if not upx_found:
        print_warning("UPX 未找到（可选，用于压缩）")
    
    # 3. 检查资源文件
    res_dir = os.path.join(base_dir, "res")
    if os.path.isdir(res_dir):
        files = os.listdir(res_dir)
        print_success(f"res 目录存在 ({len(files)} 个文件)")
        for f in files:
            print_info(f"  - {f}")
    else:
        print_error("res 目录不存在")
        all_ok = False
    
    # 4. 检查 build.py
    build_py = os.path.join(base_dir, "build.py")
    if os.path.exists(build_py):
        print_success("build.py 存在")
    else:
        print_error("build.py 不存在")
        all_ok = False
    
    # 5. 检查 hooks_override
    hooks_dir = os.path.join(base_dir, "hooks_override")
    if os.path.isdir(hooks_dir):
        hooks = os.listdir(hooks_dir)
        print_success(f"hooks_override 目录存在 ({len(hooks)} 个 hook)")
    else:
        print_warning("hooks_override 目录不存在")
    
    # 6. 检查是否有运行的进程
    print_info("\n检查是否有 ACRPA.exe 正在运行...")
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ACRPA.exe"],
            capture_output=True, text=True, encoding='gbk'
        )
        if "ACRPA.exe" in result.stdout:
            print_error("ACRPA.exe 正在运行！")
            print_info("请先关闭程序或使用以下命令:")
            print_info("  Stop-Process -Name ACRPA -Force")
            all_ok = False
        else:
            print_success("没有 ACRPA.exe 正在运行")
    except Exception as e:
        print_warning(f"无法检查进程: {e}")
    
    return all_ok

# ======================================================================
# 主函数
# ======================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ACRPA 代码检查和打包诊断工具")
    parser.add_argument("--syntax", action="store_true", help="仅语法检查")
    parser.add_argument("--import", dest="imports", action="store_true", help="仅导入检查")
    parser.add_argument("--tray", action="store_true", help="仅托盘功能检查")
    parser.add_argument("--icon", action="store_true", help="仅图标设置检查")
    parser.add_argument("--build", action="store_true", help="仅打包准备检查")
    args = parser.parse_args()
    
    # 如果没有指定任何选项，执行全部检查
    run_all = not any([args.syntax, args.imports, args.tray, args.icon, args.build])
    
    results = []
    
    if run_all or args.syntax:
        results.append(("语法检查", check_syntax()))
    
    if run_all or args.imports:
        results.append(("导入检查", check_imports()))
    
    if run_all or args.tray:
        results.append(("托盘功能", check_tray()))
    
    if run_all or args.icon:
        results.append(("图标设置", check_icon()))
    
    if run_all or args.build:
        results.append(("打包准备", check_build()))
    
    # 汇总结果
    print_header("诊断结果汇总")
    
    all_passed = True
    for name, passed in results:
        status = f"{Colors.GREEN}通过{Colors.RESET}" if passed else f"{Colors.RED}失败{Colors.RESET}"
        print(f"{name:12s}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print_success("所有检查通过！可以安全打包。")
        print_info("\n打包命令:")
        print_info("  python build.py              # 单文件 EXE")
        print_info("  python build.py --clean      # 清理后打包")
    else:
        print_error("部分检查失败，请修复后再打包。")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
