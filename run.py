import os, sys
# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

def cleanup_and_exit(exit_code=0):
    
    try:
        import state
        
        # 设置关闭标志
        state._closing = True
        state.quit2 = True
        state.pause_event.set()
        
        # 停止调度器（如果运行中）
        try:
            import scheduler as sched
            sched.stop_scheduler()
        except Exception:
            pass
        
        # 保存配置
        try:
            state.save_config()
        except Exception:
            pass
        
        # 强制刷新日志到磁盘
        try:
            from utils import _tlog
            if _tlog is not None and hasattr(_tlog, 'force_flush'):
                _tlog.force_flush()
        except Exception:
            pass
            
    except Exception as e:
        print(f"清理过程出错: {e}")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    missing = []
    for mod in ["pyautogui", "xlrd", "pyperclip", "PIL"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        print("缺少依赖库,请运行:")
        print("  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
        print(f"\n缺失: {', '.join(missing)}")
        cleanup_and_exit(1)

    try:
        from ACRPA import root
        root.mainloop()
    except KeyboardInterrupt:
        print("\n程序已退出")
        cleanup_and_exit(0)
    except Exception as e:
        print(f"\n程序异常: {e}")
        import traceback
        traceback.print_exc()
        cleanup_and_exit(1)
