"""
Script Marketplace for ACRPA — browse and download community automation scripts.

Backend: Gitee/GitHub repository as script storage.
Metadata: Each script has a script.json descriptor.

P0 Optimization: Lazy loading — metadata fetched on demand, scripts downloaded individually.
"""
import os
import time
try:
    import requests
except ImportError:
    requests = None

from utils import log1


# ── Marketplace configuration ──
MARKETPLACE_REPO = "https://gitee.com/yohoten/acrpa-marketplace/raw/master"
MARKETPLACE_INDEX = MARKETPLACE_REPO + "/index.json"
CACHE_TTL = 3600  # Cache index for 1 hour


# ── In-memory cache ──
_index_cache = None
_index_cache_time = 0


class MarketplaceError(Exception):
    """Marketplace operation error."""
    pass


class ScriptInfo:
    """Metadata for a marketplace script."""

    def __init__(self, data):
        self.id = data.get("id", "")
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.category = data.get("category", "")
        self.author = data.get("author", "")
        self.version = data.get("version", "1.0")
        self.downloads = data.get("downloads", 0)
        self.rating = data.get("rating", 0.0)
        self.rating_count = data.get("rating_count", 0)
        self.tags = data.get("tags", [])
        self.icon = data.get("icon", "")
        self.filename = data.get("filename", "")  # .xls file in repo
        self.preview = data.get("preview", "")     # Preview image URL
        self.requires = data.get("requires", [])   # ["dd_driver", "ocr", "pywin32"]

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "category": self.category, "author": self.author, "version": self.version,
            "downloads": self.downloads, "rating": self.rating,
            "rating_count": self.rating_count, "tags": self.tags,
            "icon": self.icon, "filename": self.filename, "preview": self.preview,
            "requires": self.requires,
        }


# ── Built-in fallback scripts (offline mode) ──
BUILTIN_SCRIPTS = [
    {
        "id": "builtin_1", "name": "打开记事本并保存",
        "description": "自动打开记事本，输入内容并保存到桌面",
        "category": "办公", "author": "ACRPA", "version": "1.0",
        "downloads": 1580, "rating": 4.8, "rating_count": 126,
        "tags": ["记事本", "保存", "入门"],
        "filename": "builtin_notepad",
        "requires": [],
    },
    {
        "id": "builtin_2", "name": "浏览器搜索截图",
        "description": "打开Chrome浏览器，搜索关键词并截图保存",
        "category": "办公", "author": "ACRPA", "version": "1.0",
        "downloads": 2340, "rating": 4.7, "rating_count": 189,
        "tags": ["浏览器", "搜索", "截图"],
        "filename": "builtin_browser_search",
        "requires": [],
    },
    {
        "id": "builtin_3", "name": "网银自动对账",
        "description": "自动登录网银系统，下载对账单并导入Excel",
        "category": "财务", "author": "社区", "version": "2.1",
        "downloads": 5230, "rating": 4.9, "rating_count": 312,
        "tags": ["网银", "对账", "财务"],
        "filename": "builtin_bank_recon",
        "requires": ["pywin32"],
    },
    {
        "id": "builtin_4", "name": "批量文件重命名",
        "description": "批量重命名文件夹中的文件，支持序号和模板",
        "category": "系统", "author": "社区", "version": "1.2",
        "downloads": 1890, "rating": 4.5, "rating_count": 97,
        "tags": ["文件", "重命名", "批量"],
        "filename": "builtin_rename",
        "requires": [],
    },
    {
        "id": "builtin_5", "name": "定时截屏监控",
        "description": "每隔N分钟自动截屏保存，用于系统监控",
        "category": "系统", "author": "ACRPA", "version": "1.0",
        "downloads": 3200, "rating": 4.6, "rating_count": 205,
        "tags": ["截屏", "监控", "定时"],
        "filename": "builtin_screen_monitor",
        "requires": [],
    },
    {
        "id": "builtin_6", "name": "Excel数据自动录入",
        "description": "从CSV读取数据，自动填写到网页表单或Excel中",
        "category": "办公", "author": "社区", "version": "1.3",
        "downloads": 1670, "rating": 4.4, "rating_count": 88,
        "tags": ["Excel", "录入", "表单"],
        "filename": "builtin_data_entry",
        "requires": [],
    },
    {
        "id": "builtin_7", "name": "发票批量识别录入",
        "description": "OCR识别发票信息，自动录入到财务系统",
        "category": "财务", "author": "社区", "version": "2.0",
        "downloads": 4100, "rating": 4.8, "rating_count": 256,
        "tags": ["发票", "OCR", "财务"],
        "filename": "builtin_invoice_ocr",
        "requires": ["ocr"],
    },
    {
        "id": "builtin_8", "name": "日报自动生成发送",
        "description": "收集今日工作内容，生成日报并发送邮件",
        "category": "办公", "author": "ACRPA", "version": "1.1",
        "downloads": 2950, "rating": 4.7, "rating_count": 178,
        "tags": ["日报", "邮件", "自动化"],
        "filename": "builtin_daily_report",
        "requires": [],
    },
]


def _load_builtin_scripts():
    """Return built-in scripts as ScriptInfo list."""
    return [ScriptInfo(s) for s in BUILTIN_SCRIPTS]


def fetch_index(force_refresh=False):
    """
    Fetch marketplace index from remote.

    Args:
        force_refresh: Force refresh even if cached

    Returns:
        List of ScriptInfo objects

    Raises:
        MarketplaceError on network failure (falls back to builtin)
    """
    global _index_cache, _index_cache_time

    now = time.time()
    if not force_refresh and _index_cache and (now - _index_cache_time < CACHE_TTL):
        return _index_cache

    # Try remote fetch
    if requests:
        try:
            resp = requests.get(MARKETPLACE_INDEX, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            scripts = [ScriptInfo(s) for s in data.get("scripts", [])]
            _index_cache = scripts
            _index_cache_time = now
            log1("脚本市场: 获取到 {} 个脚本".format(len(scripts)))
            return scripts
        except Exception as e:
            log1("脚本市场远程获取失败: {}，使用内置脚本库".format(e), "warning")

    # Fallback to builtin
    log1("脚本市场: 使用内置脚本库 (离线模式)")
    scripts = _load_builtin_scripts()
    _index_cache = scripts
    _index_cache_time = now
    return scripts


def search_scripts(keyword, scripts=None):
    """
    Search scripts by keyword (name, description, tags, category).

    Args:
        keyword: Search term
        scripts: Script list (uses cached index if None)

    Returns:
        Filtered list of ScriptInfo
    """
    if scripts is None:
        scripts = _index_cache or _load_builtin_scripts()

    if not keyword or not keyword.strip():
        return scripts

    kw = keyword.lower().strip()
    results = []
    for s in scripts:
        text = "{} {} {} {}".format(
            s.name.lower(), s.description.lower(),
            s.category.lower(), " ".join(s.tags).lower())
        if kw in text:
            results.append(s)
    return results


def download_script(script_id, save_dir):
    """
    Download a script .xls file from marketplace.

    Args:
        script_id: Script identifier
        save_dir: Directory to save the file

    Returns:
        Path to saved .xls file

    Raises:
        MarketplaceError on failure
    """
    # Find script in cache
    scripts = _index_cache or _load_builtin_scripts()
    script_info = None
    for s in scripts:
        if s.id == script_id:
            script_info = s
            break

    if script_info is None:
        raise MarketplaceError("脚本未找到: {}".format(script_id))

    # Handle built-in scripts: generate from templates
    if script_info.id.startswith("builtin_"):
        return _generate_builtin_script(script_info, save_dir)

    # Download from remote
    if not requests:
        raise MarketplaceError("网络功能不可用，请安装 requests 库")

    filename = script_info.filename
    if not filename.endswith(".xls"):
        filename += ".xls"

    url = "{}/scripts/{}".format(MARKETPLACE_REPO, filename)
    save_path = os.path.join(save_dir, filename)

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        log1("脚本已下载: {} -> {}".format(script_info.name, save_path))
        return save_path
    except Exception as e:
        raise MarketplaceError("下载失败: {}".format(e))


def _generate_builtin_script(script_info, save_dir):
    """Generate a built-in script as .xls from template definitions."""
    import xlwt
    from scriptdata import ScriptData

    # Built-in script templates
    BUILTIN_TEMPLATES = {
        "builtin_notepad": [
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["win", "r", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["notepad", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["自动化测试内容", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["ctrl", "s", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["test.txt", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
        ],
        "builtin_browser_search": [
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["win", "r", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["chrome", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("等待", ["3", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["https://www.baidu.com", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("等待", ["3", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["Python自动化", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
        ],
        "builtin_bank_recon": [
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("激活窗口", ["网银", "", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["ctrl", "o", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("点图", ["download_btn", "0.9", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["5", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["alt", "f4", "", "", "", "", "", "", ""]),
        ],
        "builtin_rename": [
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["win", "e", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("坐标", ["500", "300", "左", "1", "0.1", "", "", "", ""]),
            ScriptData("按键", ["f2", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("输入", ["new_name_", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
        ],
        "builtin_screen_monitor": [
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("截屏", ["monitor", "", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["300", "", "", "", "", "", "", "", ""]),
            ScriptData("截屏", ["monitor", "", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["300", "", "", "", "", "", "", "", ""]),
            ScriptData("截屏", ["monitor", "", "", "", "", "", "", "", ""]),
        ],
        "builtin_data_entry": [
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("点图", ["field1", "0.9", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["数据1", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["tab", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("输入", ["数据2", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["tab", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("点图", ["submit", "0.9", "", "", "", "", "", "", ""]),
        ],
        "builtin_invoice_ocr": [
            ScriptData("等待", ["2", "", "", "", "", "", "", "", ""]),
            ScriptData("识别文字", ["100", "100", "800", "600", "invoice_text", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("点图", ["input_field", "0.9", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["${invoice_text}", "", "", "", "", "", "", "", ""]),
        ],
        "builtin_daily_report": [
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["win", "r", "", "", "", "", "", "", ""]),
            ScriptData("等待", ["0.5", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["notepad", "", "", "", "", "", "", "", ""]),
            ScriptData("按键", ["enter", "1", "0.1", "", "", "", "", "", ""]),
            ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
            ScriptData("输入", ["今日工作日报...", "", "", "", "", "", "", "", ""]),
            ScriptData("热键", ["ctrl", "s", "", "", "", "", "", "", ""]),
        ],
    }

    rows = BUILTIN_TEMPLATES.get(script_info.id, [])
    if not rows:
        raise MarketplaceError("内置脚本模板未找到: {}".format(script_info.id))

    filename = script_info.filename + ".xls"
    save_path = os.path.join(save_dir, filename)

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    ws.write(0, 0, "命令类型")
    for j in range(1, 10):
        ws.write(0, j, "参数{}".format(j))
    ws.write(1, 0, "（标题行）")
    for i, sd in enumerate(rows, start=2):
        ws.write(i, 0, sd.cmd_type)
        for j in range(9):
            ws.write(i, j + 1, sd.args[j] if j < len(sd.args) else "")
    wb.save(save_path)

    log1("生成内置脚本: {} -> {}".format(script_info.name, save_path))
    return save_path
