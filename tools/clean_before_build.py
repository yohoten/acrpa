"""
ACRPA 打包前清理脚本

自动处理:
1. 关闭 ACRPA.exe 进程
2. 删除被锁定的 dist/ACRPA.exe
3. 清理 build 缓存

用法:
  python tools/clean_before_build.py
"""
import os
import sys
import subprocess
import time
import shutil

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

def kill_process_ps(process_name):
    """使用 PowerShell 强制关闭进程"""
    try:
        cmd = f"Get-Process -Name {process_name} -ErrorAction SilentlyContinue | Stop-Process -Force"
        result = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, encoding='utf-8'
        )
        return True
    except Exception as e:
        print_warning(f"关闭进程时出错: {e}")
        return False

def wait_for_process_exit(process_name, timeout=5):
    """等待进程完全退出"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            result = subprocess.run(
                ["powershell", "-Command", 
                 f"if (Get-Process -Name {process_name} -ErrorAction SilentlyContinue) {{ 'running' }} else {{ 'stopped' }}"],
                capture_output=True, text=True, encoding='utf-8'
            )
            if result.stdout.strip() == 'stopped':
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def remove_file_force(filepath):
    """强制删除文件"""
    if not os.path.exists(filepath):
        return True
    
    try:
        # 尝试普通删除
        os.remove(filepath)
        return True
    except PermissionError:
        # 如果失败，尝试 PowerShell 删除
        try:
            cmd = f"Remove-Item -Path '{filepath}' -Force -ErrorAction SilentlyContinue"
            subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, text=True, encoding='utf-8'
            )
            time.sleep(0.5)
            return not os.path.exists(filepath)
        except Exception:
            return False

def main():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}ACRPA 打包前清理工具{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
    
    # Step 1: 关闭进程
    print_info("步骤 1: 检查并关闭 ACRPA.exe 进程...")
    kill_process_ps("ACRPA")
    
    if wait_for_process_exit("ACRPA", timeout=5):
        print_success("ACRPA.exe 已关闭")
    else:
        print_warning("无法确认进程是否关闭，继续尝试清理...")
    
    # Step 2: 删除 EXE 文件
    exe_path = os.path.join(base_dir, "dist", "ACRPA.exe")
    print_info(f"步骤 2: 删除旧文件 {exe_path}...")
    
    if remove_file_force(exe_path):
        if os.path.exists(exe_path):
            print_error("文件仍存在，可能被其他程序占用")
            print_info("请手动关闭所有 ACRPA 窗口后重试")
            return 1
        else:
            print_success("旧文件已删除")
    else:
        print_error("无法删除文件")
        print_info("请手动删除 dist\\ACRPA.exe 后重试")
        return 1
    
    # Step 3: 清理 build 缓存
    build_dir = os.path.join(base_dir, "build")
    if os.path.exists(build_dir):
        print_info("步骤 3: 清理 build 缓存...")
        try:
            shutil.rmtree(build_dir)
            print_success("build 目录已清理")
        except Exception as e:
            print_warning(f"清理 build 目录失败: {e}")
    
    # Step 4: 清理 __pycache__
    pycache_dirs = []
    for root, dirs, _ in os.walk(base_dir):
        if '__pycache__' in dirs:
            pycache_dirs.append(os.path.join(root, '__pycache__'))
    
    if pycache_dirs:
        print_info("步骤 4: 清理 Python 缓存...")
        for d in pycache_dirs:
            try:
                shutil.rmtree(d)
            except Exception:
                pass
        print_success(f"已清理 {len(pycache_dirs)} 个 __pycache__ 目录")
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print_success("清理完成！现在可以安全打包。")
    print_info("\n打包命令:")
    print_info("  python build.py              # 标准打包")
    print_info("  python build.py --clean      # 清理后打包")
    print_info("  python tools/safe_build.py   # 自动化打包（推荐）")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
