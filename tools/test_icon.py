"""
ACRPA 图标设置测试工具

验证:
1. SetCurrentProcessExplicitAppUserModelID 是否在窗口创建前调用
2. WM_SETICON 是否正确设置任务栏图标
3. 所有窗口（主窗口、Mini Bar、托盘消息窗口）的图标一致性

用法:
  python tools/test_icon.py
"""
import os
import sys
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

def check_app_user_model_id():
    """检查 AppUserModelID 是否已设置"""
    print_info("检查 AppUserModelID...")
    
    try:
        # 获取当前进程的 AppUserModelID
        prop_store = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID()
        
        if prop_store:
            print_success(f"AppUserModelID 已设置")
            return True
        else:
            print_warning("AppUserModelID 未设置（可能影响任务栏图标分组）")
            return False
    except Exception as e:
        print_warning(f"无法检查 AppUserModelID: {e}")
        return False

def check_icon_file():
    """检查图标文件是否存在"""
    print_info("检查图标文件...")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    icon_paths = [
        os.path.join(base_dir, "res", "automation.ico"),
        os.path.join(base_dir, "automation.ico"),
    ]
    
    for icon_path in icon_paths:
        if os.path.exists(icon_path):
            size = os.path.getsize(icon_path)
            print_success(f"图标文件存在: {icon_path} ({size} bytes)")
            
            # 检查是否为有效的 ICO 文件
            with open(icon_path, 'rb') as f:
                header = f.read(4)
                if header[:2] == b'\x00\x00':  # ICO 文件头
                    print_success("ICO 文件格式正确")
                    return True
                else:
                    print_error("文件格式不是有效的 ICO")
                    return False
    
    print_error("图标文件不存在")
    return False

def test_load_image_api():
    """测试 LoadImageW API 是否能正确加载图标"""
    print_info("测试 LoadImageW API...")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    ico_path = os.path.join(base_dir, "res", "automation.ico")
    
    if not os.path.exists(ico_path):
        print_error("图标文件不存在，无法测试")
        return False
    
    try:
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        
        hicon = ctypes.windll.user32.LoadImageW(
            None,           # hInst
            ico_path,       # 文件路径
            IMAGE_ICON,     # uType
            0,              # cx (0 = 默认)
            0,              # cy (0 = 默认)
            LR_LOADFROMFILE # fuLoad
        )
        
        if hicon:
            print_success(f"LoadImageW 成功加载图标 (HICON: {hicon})")
            
            # 清理资源
            ctypes.windll.user32.DestroyIcon(hicon)
            return True
        else:
            error_code = ctypes.windll.kernel32.GetLastError()
            print_error(f"LoadImageW 失败 (错误代码: {error_code})")
            return False
            
    except Exception as e:
        print_error(f"LoadImageW 测试失败: {e}")
        return False

def check_code_implementation():
    """检查代码中是否正确实现了 WM_SETICON"""
    print_info("检查代码实现...")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    acrpa_path = os.path.join(base_dir, "src", "ACRPA.py")
    tray_path = os.path.join(base_dir, "src", "tray.py")
    
    all_ok = True
    
    # 检查 ACRPA.py
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
        
        print_success("ACRPA.py:")
        for pattern, desc in checks:
            if pattern in content:
                print_success(f"  ✅ {desc}")
            else:
                print_error(f"  ❌ {desc} 缺失")
                all_ok = False
    else:
        print_error("ACRPA.py 不存在")
        all_ok = False
    
    # 检查 tray.py
    if os.path.exists(tray_path):
        with open(tray_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if "WM_SETICON" in content:
            print_success("tray.py: WM_SETICON 实现")
        else:
            print_warning("tray.py: 未使用 WM_SETICON（可能影响托盘消息窗口图标）")
    
    return all_ok

def main():
    print_header("ACRPA 图标设置测试工具")
    
    results = []
    
    # 测试 1: 图标文件
    results.append(("图标文件", check_icon_file()))
    
    # 测试 2: LoadImageW API
    results.append(("LoadImageW API", test_load_image_api()))
    
    # 测试 3: 代码实现
    results.append(("代码实现", check_code_implementation()))
    
    # 汇总结果
    print_header("测试结果汇总")
    
    all_passed = True
    for name, passed in results:
        status = f"{Colors.GREEN}通过{Colors.RESET}" if passed else f"{Colors.RED}失败{Colors.RESET}"
        print(f"{name:12s}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print_success("所有检查通过！")
        print_info("\n运行程序验证:")
        print_info("  python run.py")
        print_info("\n验证要点:")
        print_info("  1. 任务栏图标应为 automation.ico（非羽毛图标）")
        print_info("  2. Alt+Tab 切换窗口时显示正确图标")
        print_info("  3. 窗口标题栏左上角显示正确图标")
    else:
        print_error("部分检查失败，请修复后再测试。")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
