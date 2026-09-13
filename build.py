"""
ACRPA 精简打包脚本 — 最小化 EXE 体积 (目标: <15 MB)
用法:
  python build.py              → 单文件 EXE（发布用）
  python build.py --clean      → 清理 + 打包
  python build.py --dir        → 文件夹模式（调试用）
  python build.py --console    → 显示控制台（调试用）
"""
import os, sys, shutil, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(BASE, "src")
RES  = os.path.join(BASE, "res")
DIST = os.path.join(BASE, "dist")

APP = "ACRPA"
ICON = os.path.join(RES, "automation.ico")
ENTRY = os.path.join(BASE, "run.py")

# 7-Zip SFX 检测
_7Z_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    os.path.join(BASE, "7z.exe"),
]

# ── 项目模块 (全部纳入) ──
_PROJECT_MODULES = [
    "state", "scriptdata", "templates", "utils", "commands",
    "engine", "recorder", "scheduler", "updater",
    "marketplace", "ocr_backend", "dd_backend", "safe_eval",
]

# ── 核心依赖 hidden imports (仅导入真正用到的子模块) ──
HIDDEN_IMPORTS = _PROJECT_MODULES + [
    # tkinter GUI
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    # 图像处理 (仅 PNG/JPEG, 不用 OpenCV)
    "PIL.Image", "PIL.ImageTk", "PIL.ImageGrab",
    "PIL.PngImagePlugin", "PIL.JpegImagePlugin",
    # 自动化
    "pyautogui", "pyperclip",
    # Excel
    "xlrd", "xlwt",
    # 网络 (AI/更新/市场) — charset_normalizer 由 requests 按需加载，不显式引入
    "requests", "urllib3", "certifi", "idna",
    # stdlib 可能遗漏的 (configparser/csv 由 requests→http.cookiejar 使用, 必须保留)
    "queue", "json", "datetime", "ctypes", "shutil", "re",
    "threading", "ast", "asyncio",
    "csv", "configparser",
]

# ── 附加数据文件 (随 exe 解压) ──
# VERSION 必须随包分发: 冻结后 version_info 优先在 _MEIPASS 内查找该文件,
# 若缺失会回退到内置 _FALLBACK_VERSION, 导致 EXE 自报错误版本、更新检查结论失真。
EXTRA_DATAS = [
    os.path.join(BASE, "使用说明.txt"),
    os.path.join(BASE, "VERSION"),
    # os.path.join(BASE, "README.md"),
]

# DD 驱动 DLL
DD_DRIVER_DIR = os.path.join(BASE, "lib", "dd_driver")
if os.path.isdir(DD_DRIVER_DIR):
    EXTRA_DATAS.append(os.path.join(DD_DRIVER_DIR, "dd63330.dll"))

# ======================================================================
# EXCLUDE — 激进排除以最小化体积 (每项标注预估节省)
# ======================================================================
EXCLUDE_MODULES = [
    # ── numpy 全家桶 (本项目完全不使用, Pillow hook 会错误拉入) (~20 MB) ──
    "numpy", "numpy.*",
    "numpy._core", "numpy._core.*",
    "numpy._globals", "numpy._globals.*",
    "numpy.char", "numpy.ctypeslib", "numpy.distutils",
    "numpy.dtypes", "numpy.exceptions", "numpy.f2py",
    "numpy.fft", "numpy.lib", "numpy.lib.*",
    "numpy.linalg", "numpy.linalg.*",
    "numpy.ma", "numpy.matlib", "numpy.matrixlib",
    "numpy.polynomial", "numpy.random", "numpy.random.*",
    "numpy.rec", "numpy.strings", "numpy.testing",
    "numpy.typing", "numpy.version",

    # ── 科学计算/数据分析 (~60 MB) ──
    "matplotlib", "scipy", "pandas",

    # ── 图像处理子模块 (保留 PNG/JPEG) (~5 MB) ──
    "PIL.ImageQt", "PIL.ImageDraw2", "PIL.ImageFont",
    "PIL.ImageFilter", "PIL.ImageMath", "PIL.ImagePath",
    "PIL.ImageStat", "PIL.ImageWin",
    "PIL.TiffImagePlugin", "PIL.WebPImagePlugin",
    "PIL.PdfImagePlugin", "PIL.EpsImagePlugin",
    "PIL.GifImagePlugin", "PIL.MpoImagePlugin",
    "PIL.PcxImagePlugin", "PIL.FpxImagePlugin",
    "PIL.MicImagePlugin", "PIL.XbmImagePlugin",
    "PIL.IcoImagePlugin", "PIL.ImImagePlugin",
    "PIL.PalmImagePlugin", "PIL.PpmImagePlugin",
    "PIL.SgiImagePlugin", "PIL.TgaImagePlugin",
    "PIL.XpmImagePlugin", "PIL.FitsImagePlugin",

    # ── OpenCV (pyautogui 用 Pillow 回退, 不需要) (~60 MB) ──
    "cv2", "cv2.*", "opencv_python", "opencv_python.*",

    # ── 其他 GUI 框架 (~50 MB) ──
    "PyQt5", "PyQt5.*", "PySide2", "PySide6", "wx", "kivy",

    # ── 网络/Web 框架 (~20 MB) ──
    "aiohttp", "flask", "django", "tornado",

    # ── 数据库 (~15 MB) ──
    "sqlalchemy", "pymysql", "psycopg2",

    # ── 邮件 (不需要) ──
    "smtplib", "imaplib", "poplib",

    # ── 多媒体 (~10 MB) ──
    "pygame", "pydub", "astropy",

    # ── 开发/测试工具 (~15 MB) ──
    "IPython", "IPython.*", "jupyter", "notebook", "nbformat",
    "pip", "setuptools", "pkg_resources", "wheel",
    "pytest", "unittest", "distutils",

    # ── 文档工具 ──
    "docx", "docutils", "markdown", "sphinx",

    # ── 密码学重库 (SSL 靠 certifi + stdlib ssl) (~5 MB) ──
    "cryptography", "cryptography.*", "cffi",
    "bcrypt", "paramiko",

    # ── 爬虫 ──
    "bs4", "html5lib", "selenium",

    # charset_normalizer 由 requests 使用, 不能排除

    # ── pywin32 多余子模块 (pythonwin IDE 不需要) (~0.3 MB) ──
    "pythonwin", "pythonwin.*",
    "win32com", "win32com.*",
    "win32comext", "win32comext.*",
    "adodbapi", "adodbapi.*",
    "isapi", "isapi.*",

    # ── pyinstaller 自身 ──
    "PyInstaller",

    # ── 不需要的 stdlib 模块 ──
    # 注意: http.client, email, logging 由 urllib3/requests 使用, 不能排除
    "tkinter.test", "tkinter.test.*",
    "turtle", "turtle.*",
    "lib2to3", "lib2to3.*",
    "multiprocessing", "concurrent.futures.process",
    # asyncio 由 ocr_backend.py (winrt OCR) 使用，保留
    "html", "html.*",
    "http.server",
    "xml", "xml.*",
    "xmlrpc", "xmlrpc.*",
    # configparser 由 requests→http.cookiejar 使用, AI 功能需要, 不能排除
    # csv 由 requests→http.cookiejar 使用, AI 功能需要, 不能排除
    "argparse",
    "bz2", "lzma",
    # sqlite3 由 version_manager.py 使用 (脚本版本历史), 不能排除
    "ctypes.test", "ctypes.test.*",
    "unittest", "unittest.*",
    "pydoc", "doctest",
    "venv", "ensurepip",
    "wsgiref", "wsgiref.*",
    "plistlib", "netrc",
    "getpass", "getopt",
]

def find_7z():
    """查找 7-Zip 可执行文件路径。"""
    for p in _7Z_PATHS:
        if os.path.exists(p):
            return p
    return None

def check_pyinstaller():
    try:
        import PyInstaller
        print("[OK] PyInstaller {}".format(PyInstaller.__version__))
    except ImportError:
        print("[!] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def clean():
    for d in (DIST, os.path.join(BASE, "build")):
        if os.path.exists(d): shutil.rmtree(d)
    spec = os.path.join(BASE, "{}.spec".format(APP))
    if os.path.exists(spec): os.remove(spec)
    # 清理 src 下所有 __pycache__（防止垃圾进包）
    for r, dirs, _ in os.walk(SRC):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(r, d))
    print("[CLEAN] Done")

def build(onefile=True, console=False, clean_first=False):
    if clean_first: clean()
    check_pyinstaller()

    # 本地 hook 覆盖目录（解决项目本地 workflow.py 与第三方 workflow 包冲突）
    HOOKS_OVERRIDE = os.path.join(BASE, "hooks_override")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        ENTRY, "--name", APP,
        "--paths", SRC,
        "--distpath", DIST,
        "--workpath", os.path.join(BASE, "build"),
        "--clean", "--noconfirm",
        "--noupx",           # UPX 与 CFG 保护冲突, 禁用
    ]

    if os.path.isdir(HOOKS_OVERRIDE):
        cmd += ["--additional-hooks-dir", HOOKS_OVERRIDE]

    if onefile: cmd.append("--onefile")
    else:       cmd.append("--onedir")

    if not console: cmd.append("--windowed")

    if os.path.exists(ICON): cmd += ["--icon", ICON]
    if os.path.isdir(RES):   cmd += ["--add-data", "{}{}res".format(RES, os.pathsep)]
    # 插件目录: 插件是运行时动态加载的 .py 源码, 必须随包分发;
    # 其余项目模块已由 PYZ 收集, 不再整体打包 src (避免 __pycache__ 等冗余)
    PLUGINS_SRC = os.path.join(SRC, "plugins")
    if os.path.isdir(PLUGINS_SRC):
        cmd += ["--add-data", "{}{}src{}plugins".format(PLUGINS_SRC, os.pathsep, os.sep)]
    for fp in EXTRA_DATAS:
        if os.path.exists(fp):
            cmd += ["--add-data", "{}{}.".format(fp, os.pathsep)]

    for h in HIDDEN_IMPORTS: cmd += ["--hidden-import", h]
    for e in EXCLUDE_MODULES: cmd += ["--exclude-module", e]

    mode_str = '单文件' if onefile else '文件夹'
    print("\n{}\n  打包 {}\n  模式: {}\n{}\n".format('='*50, APP, mode_str, '='*50))

    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        print("\n[FAIL] 返回码: {}".format(r.returncode))
        return 1

    onedir_path = os.path.join(DIST, APP)
    exe_path = os.path.join(onedir_path, "{}.exe".format(APP))
    if os.path.exists(exe_path):
        total = sum(os.path.getsize(os.path.join(rr, f))
            for rr, _, fs in os.walk(onedir_path) for f in fs)
        print("\n[OK] {} (文件夹: {:.1f} MB, 主程序: {:.1f} MB)".format(
            exe_path, total / (1024*1024), os.path.getsize(exe_path) / (1024*1024)))
    elif onefile:
        dist_exe = os.path.join(DIST, "{}.exe".format(APP))
        if os.path.exists(dist_exe):
            size = os.path.getsize(dist_exe) / (1024*1024)
            print("\n[OK] {} ({:.1f} MB)".format(dist_exe, size))
        else:
            print("\n[WARN] EXE not found in {}".format(DIST))
    else:
        print("\n[WARN] EXE not found in {}".format(DIST))
    return 0

def build_sfx():
    """使用 7-Zip 将 onedir 文件夹压缩为自解压 EXE。"""
    z7 = find_7z()
    if not z7:
        print("[SKIP] 7-Zip 未找到，跳过 SFX 打包")
        print("  安装 7-Zip: https://www.7-zip.org/")
        return 1

    onedir_path = os.path.join(DIST, APP)
    if not os.path.isdir(onedir_path):
        print("[FAIL] 未找到 {}，请先运行 --dir 构建".format(onedir_path))
        return 1

    sfx_module = os.path.join(os.path.dirname(z7), "7z.sfx")
    if not os.path.exists(sfx_module):
        print("[FAIL] 未找到 7z.sfx 模块: {}".format(sfx_module))
        return 1

    config_path = os.path.join(DIST, "config.txt")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(';!@Install@!UTF-8!\n')
        f.write('Title="ACRPA 自动化工具"\n')
        f.write('BeginPrompt="是否运行 ACRPA 自动化工具？"\n')
        f.write('RunProgram="ACRPA.exe"\n')
        f.write('Directory="%TEMP%\\\\ACRPA"\n')
        f.write(';!@InstallEnd@!\n')

    payload_path = os.path.join(DIST, "ACRPA_payload.7z")
    sfx_path = os.path.join(DIST, "ACRPA_sfx.exe")

    print("\n[7z] 压缩文件夹 (LZMA2 极限)...")
    r = subprocess.run([
        z7, "a", payload_path,
        os.path.join(onedir_path, "*"),
        "-mx=9", "-mfb=273", "-ms=on", "-mmt=on",
        "-xr!config.json", "-xr!logs", "-xr!screenshots",
    ], cwd=BASE)
    if r.returncode != 0:
        print("[FAIL] 7z 压缩失败")
        return 1

    payload_mb = os.path.getsize(payload_path) / (1024*1024)

    print("[7z] 创建自解压 EXE...")
    with open(sfx_path, "wb") as out:
        with open(sfx_module, "rb") as f:
            out.write(f.read())
        with open(config_path, "rb") as f:
            out.write(f.read())
        with open(payload_path, "rb") as f:
            out.write(f.read())

    sfx_mb = os.path.getsize(sfx_path) / (1024*1024)

    os.remove(payload_path)
    os.remove(config_path)

    total_raw = sum(os.path.getsize(os.path.join(rr, f))
        for rr, _, fs in os.walk(onedir_path) for f in fs) / (1024*1024)
    print("\n[OK] {} ({:.1f} MB)".format(sfx_path, sfx_mb))
    print("  原始: {:.1f} MB → SFX: {:.1f} MB (压缩率 {:.0f}%)".format(
        total_raw, sfx_mb, (1 - sfx_mb / total_raw) * 100))
    return 0

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="ACRPA 精简打包")
    p.add_argument("--clean", action="store_true", help="清理缓存")
    p.add_argument("--dir", action="store_true", help="文件夹模式")
    p.add_argument("--sfx", action="store_true", help="文件夹模式 + 7z 自解压 (需安装 7-Zip)")
    p.add_argument("--console", action="store_true", help="显示控制台")
    args = p.parse_args()

    if args.sfx:
        ret = build(onefile=False, console=args.console, clean_first=args.clean)
        if ret == 0:
            sys.exit(build_sfx())
        sys.exit(ret)
    else:
        sys.exit(build(onefile=not args.dir, console=args.console, clean_first=args.clean))
