# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:/CodingEmber/ACRPA/run.py'],
    pathex=['D:/CodingEmber/ACRPA/src'],
    binaries=[],
    datas=[('D:/CodingEmber/ACRPA/res', 'res'), ('D:/CodingEmber/ACRPA/src/plugins', 'src/plugins'), ('D:/CodingEmber/ACRPA/使用说明.txt', '.'), ('D:/CodingEmber/ACRPA/lib/dd_driver/dd63330.dll', '.')],
    hiddenimports=['state', 'scriptdata', 'templates', 'utils', 'commands', 'engine', 'recorder', 'scheduler', 'updater', 'marketplace', 'ocr_backend', 'dd_backend', 'safe_eval', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageGrab', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin', 'pyautogui', 'pyperclip', 'xlrd', 'xlwt', 'requests', 'urllib3', 'certifi', 'idna', 'queue', 'json', 'datetime', 'ctypes', 'shutil', 're', 'threading', 'ast', 'asyncio', 'csv', 'configparser'],
    hookspath=['D:/CodingEmber/ACRPA/hooks_override'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'numpy.*', 'numpy._core', 'numpy._core.*', 'numpy._globals', 'numpy._globals.*', 'numpy.char', 'numpy.ctypeslib', 'numpy.distutils', 'numpy.dtypes', 'numpy.exceptions', 'numpy.f2py', 'numpy.fft', 'numpy.lib', 'numpy.lib.*', 'numpy.linalg', 'numpy.linalg.*', 'numpy.ma', 'numpy.matlib', 'numpy.matrixlib', 'numpy.polynomial', 'numpy.random', 'numpy.random.*', 'numpy.rec', 'numpy.strings', 'numpy.testing', 'numpy.typing', 'numpy.version', 'matplotlib', 'scipy', 'pandas', 'PIL.ImageQt', 'PIL.ImageDraw2', 'PIL.ImageFont', 'PIL.ImageFilter', 'PIL.ImageMath', 'PIL.ImagePath', 'PIL.ImageStat', 'PIL.ImageWin', 'PIL.TiffImagePlugin', 'PIL.WebPImagePlugin', 'PIL.PdfImagePlugin', 'PIL.EpsImagePlugin', 'PIL.GifImagePlugin', 'PIL.MpoImagePlugin', 'PIL.PcxImagePlugin', 'PIL.FpxImagePlugin', 'PIL.MicImagePlugin', 'PIL.XbmImagePlugin', 'PIL.IcoImagePlugin', 'PIL.ImImagePlugin', 'PIL.PalmImagePlugin', 'PIL.PpmImagePlugin', 'PIL.SgiImagePlugin', 'PIL.TgaImagePlugin', 'PIL.XpmImagePlugin', 'PIL.FitsImagePlugin', 'cv2', 'cv2.*', 'opencv_python', 'opencv_python.*', 'PyQt5', 'PyQt5.*', 'PySide2', 'PySide6', 'wx', 'kivy', 'aiohttp', 'flask', 'django', 'tornado', 'sqlalchemy', 'pymysql', 'psycopg2', 'smtplib', 'imaplib', 'poplib', 'pygame', 'pydub', 'astropy', 'IPython', 'IPython.*', 'jupyter', 'notebook', 'nbformat', 'pip', 'setuptools', 'pkg_resources', 'wheel', 'pytest', 'unittest', 'distutils', 'docx', 'docutils', 'markdown', 'sphinx', 'cryptography', 'cryptography.*', 'cffi', 'bcrypt', 'paramiko', 'bs4', 'html5lib', 'selenium', 'pythonwin', 'pythonwin.*', 'win32com', 'win32com.*', 'win32comext', 'win32comext.*', 'adodbapi', 'adodbapi.*', 'isapi', 'isapi.*', 'PyInstaller', 'tkinter.test', 'tkinter.test.*', 'turtle', 'turtle.*', 'lib2to3', 'lib2to3.*', 'multiprocessing', 'concurrent.futures.process', 'html', 'html.*', 'http.server', 'xml', 'xml.*', 'xmlrpc', 'xmlrpc.*', 'argparse', 'bz2', 'lzma', 'ctypes.test', 'ctypes.test.*', 'unittest', 'unittest.*', 'pydoc', 'doctest', 'venv', 'ensurepip', 'wsgiref', 'wsgiref.*', 'plistlib', 'netrc', 'getpass', 'getopt'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ACRPA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/CodingEmber/ACRPA/res/automation.ico'],
)
