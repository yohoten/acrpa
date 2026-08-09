"""
ACRPA 安全打包脚本

自动处理:
1. 检查并关闭正在运行的 ACRPA.exe
2. 清理旧的构建文件
3. 执行打包
4. 验证输出

用法:
  python tools/safe_build.py              # 标准打包
  python tools/safe_build.py --clean      # 清理后打包
  python tools/safe_build.py --dir        # 文件夹模式
"""
import os
import sys
import subprocess
import time

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

def kill_acrpa_process():
    """检查并关闭 ACRPA.exe 进程"""
    print_info("检查是否有 ACRPA.exe 正在运行...")
    
    try:
        # 使用 PowerShell 命令
        result = subprocess.run(
            ["powershell", "-Command", 
             "Get-Process -Name ACRPA -ErrorAction SilentlyContinue | Stop-Process -Force"],
            capture_output=True, text=True, encoding='utf-8'
        )
        
        # 等待进程完全退出
        time.sleep(1)
        
        # 再次检查
        result = subprocess.run(
            ["powershell", "-Command", 
             "if (Get-Process -Name ACRPA -ErrorAction SilentlyContinue) { 'running' } else { 'stopped' }"],
            capture_output=True, text=True, encoding='utf-8'
        )
        
        if result.stdout.strip() == 'stopped':
            print_success("ACRPA.exe 已关闭")
            return True
        else:
            print_error("无法关闭 ACRPA.exe，请手动关闭后重试")
            return False
            
    except Exception as e:
        print_warning(f"检查进程时出错: {e}")
        return True  # 假设没有进程在运行

def remove_locked_exe():
    """尝试删除被锁定的 EXE 文件"""
    exe_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dist", "ACRPA.exe")
    
    if not os.path.exists(exe_path):
        return True
    
    print_info(f"尝试删除旧文件: {exe_path}")
    
    try:
        os.remove(exe_path)
        print_success("旧文件已删除")
        return True
    except PermissionError:
        print_error("文件被锁定，无法删除")
        print_info("请确保 ACRPA.exe 未运行，然后重试")
        return False
    except Exception as e:
        print_warning(f"删除文件时出错: {e}")
        return True

def run_build(clean=False, dir_mode=False):
    """执行打包"""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    build_script = os.path.join(base_dir, "build.py")
    
    cmd = [sys.executable, build_script]
    
    if clean:
        cmd.append("--clean")
    
    if dir_mode:
        cmd.append("--dir")
    
    print_info(f"执行命令: {' '.join(cmd)}")
    print()
    
    try:
        result = subprocess.run(cmd, cwd=base_dir)
        return result.returncode == 0
    except Exception as e:
        print_error(f"打包失败: {e}")
        return False

def verify_output():
    """验证打包输出"""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    dist_dir = os.path.join(base_dir, "dist")
    exe_path = os.path.join(dist_dir, "ACRPA.exe")
    
    print_header("验证输出")
    
    if not os.path.exists(exe_path):
        print_error("ACRPA.exe 未找到")
        return False
    
    size = os.path.getsize(exe_path)
    size_mb = size / (1024 * 1024)
    
    print_success(f"ACRPA.exe 生成成功")
    print_info(f"  路径: {exe_path}")
    print_info(f"  大小: {size_mb:.2f} MB")
    
    # 检查文件大小是否合理（应该在 15-35 MB 之间）
    if size_mb < 10:
        print_warning("文件过小，可能缺少依赖")
    elif size_mb > 50:
        print_warning("文件过大，检查是否有不必要的依赖")
    else:
        print_success("文件大小正常")
    
    return True

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ACRPA 安全打包脚本")
    parser.add_argument("--clean", action="store_true", help="清理后打包")
    parser.add_argument("--dir", action="store_true", help="文件夹模式")
    args = parser.parse_args()
    
    print_header("ACRPA 安全打包工具")
    
    # Step 1: 关闭进程
    print_header("步骤 1: 关闭进程")
    if not kill_acrpa_process():
        print_error("请先手动关闭 ACRPA.exe")
        return 1
    
    # Step 2: 删除旧文件
    print_header("步骤 2: 清理旧文件")
    if not remove_locked_exe():
        print_error("无法删除旧文件，请手动删除后重试")
        return 1
    
    # Step 3: 执行打包
    print_header("步骤 3: 执行打包")
    if not run_build(clean=args.clean, dir_mode=args.dir):
        print_error("打包失败")
        return 1
    
    # Step 4: 验证输出
    if not verify_output():
        print_error("输出验证失败")
        return 1
    
    print_header("打包完成")
    print_success("ACRPA.exe 已成功生成！")
    print_info("\n测试建议:")
    print_info("  1. 双击运行 ACRPA.exe")
    print_info("  2. 检查托盘图标是否正常显示")
    print_info("  3. 测试右键菜单功能")
    print_info("  4. 测试从托盘恢复主窗口")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
