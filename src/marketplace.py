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

import script_package
from utils import log1


# ── Marketplace configuration ──
MARKETPLACE_REPO = "https://gitee.com/yohoten/acrpa-marketplace/raw/master"
MARKETPLACE_INDEX = MARKETPLACE_REPO + "/index.json"
MARKETPLACE_PKG_DIR = MARKETPLACE_REPO + "/packages"   # v2 包存放目录约定
CACHE_TTL = 3600  # Cache index for 1 hour
MIN_APP_VERSION = "0.1.26"  # 兼容最低 ACRPA 版本 (兜底)
USER_AGENT = "ACRPA-Market/2.0"
_CHUNK = 1024 * 1024


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
        # ── v2 包模式可选字段 (旧声明缺省时保持向后兼容) ──
        self.pkg = data.get("pkg", "")               # 包相对路径 (如 packages/<id>-<ver>.acrpapkg)
        self.sha256 = data.get("sha256", "")         # 整个 .acrpapkg 的 sha256
        self.size = data.get("size", 0)              # 包字节数
        self.url = data.get("url", "")               # 可选直链覆盖
        self.min_app_version = data.get("min_app_version", "")  # 兼容最低 ACRPA 版本

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "category": self.category, "author": self.author, "version": self.version,
            "downloads": self.downloads, "rating": self.rating,
            "rating_count": self.rating_count, "tags": self.tags,
            "icon": self.icon, "filename": self.filename, "preview": self.preview,
            "requires": self.requires,
            "pkg": self.pkg, "sha256": self.sha256, "size": self.size,
            "url": self.url, "min_app_version": self.min_app_version,
        }

    @property
    def is_package(self):
        """是否为 v2 包模式条目 (含 pkg 字段)；旧 filename-only 条目返回 False。"""
        return bool(self.pkg)

    @classmethod
    def from_dict(cls, data):
        """与 to_dict 对称的构造入口 (内部即 cls(data)，兼容缺省字段)。"""
        return cls(data or {})


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


def _cache_ttl():
    """索引缓存秒数：state.MARKET_INDEX_CACHE_TTL 优先，缺省回退 CACHE_TTL 常量。"""
    try:
        import state
        v = int(getattr(state, "MARKET_INDEX_CACHE_TTL", CACHE_TTL) or CACHE_TTL)
        return v if v > 0 else CACHE_TTL
    except Exception:
        return CACHE_TTL


def fetch_index(force_refresh=False, allow_builtin_fallback=True):
    """
    Fetch marketplace index from remote.

    Args:
        force_refresh: Force refresh even if cached
        allow_builtin_fallback: 远端失败时是否静默回退内置脚本库。
            默认 True —— 保持旧行为(离线可用)。UI 传 False 以区分「真在线数据」
            与「离线回退」，从而让错误态可见。

    Returns:
        List of ScriptInfo objects

    Raises:
        MarketplaceError: 在线获取失败且 allow_builtin_fallback=False
    """
    global _index_cache, _index_cache_time

    now = time.time()
    if not force_refresh and _index_cache and (now - _index_cache_time < _cache_ttl()):
        return _index_cache

    # Try remote fetch
    err = None
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
            err = e
            log1("脚本市场远程获取失败: {}，使用内置脚本库".format(e), "warning")

    if not allow_builtin_fallback:
        raise MarketplaceError("脚本市场在线获取失败: {}".format(err or "网络功能不可用"))

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


def _find_script(script_id, scripts):
    """在 ScriptInfo 列表中按 id 查找；未命中返回 None。"""
    for s in scripts or []:
        if getattr(s, "id", "") == script_id:
            return s
    return None


def resolve_download_url(info):
    """解析下载地址。

    优先级: info.url > MARKETPLACE_PKG_DIR/<pkg> (包模式) >
            MARKETPLACE_REPO/scripts/<filename>.xls (旧模式)。
    """
    if info is None:
        return ""
    url = getattr(info, "url", "") or ""
    if url:
        return url
    pkg = getattr(info, "pkg", "") or ""
    if pkg:
        p = str(pkg).replace("\\", "/").lstrip("/")
        if p.startswith("packages/"):
            return "{}/{}".format(MARKETPLACE_REPO, p)
        return "{}/{}".format(MARKETPLACE_PKG_DIR, os.path.basename(p))
    filename = getattr(info, "filename", "") or ""
    if not filename:
        return ""
    if not filename.endswith(".xls"):
        filename += ".xls"
    return "{}/scripts/{}".format(MARKETPLACE_REPO, filename)


def market_install_root(save_dir=None):
    """包模式安装根目录。

    优先取 state.MARKET_INSTALL_DIR (自定义)，为空则 <save_dir 或 CONFIG 同级>/market_scripts。
    """
    custom = ""
    try:
        import state
        custom = getattr(state, "MARKET_INSTALL_DIR", "") or ""
    except Exception:
        custom = ""
    if custom:
        return custom
    base = save_dir
    if not base:
        try:
            import state
            base = os.path.dirname(state.CONFIG_PATH)
        except Exception:
            base = os.getcwd()
    return os.path.join(base, "market_scripts")


def _verify_download(path, expect_sha256="", expect_size=0):
    """下载产物校验 → (ok, 原因)。拒绝空文件 / 非 ZIP / 体积与 sha256 不符。"""
    if not os.path.exists(path):
        return False, "文件不存在"
    actual = os.path.getsize(path)
    if actual == 0:
        return False, "下载内容为空"
    if expect_size and actual != expect_size:
        return False, "体积不符: 期望 {} 实际 {}".format(expect_size, actual)
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception as e:
        return False, "无法读取下载文件: {}".format(e)
    if not head.startswith(b"PK\x03\x04"):
        return False, "不是有效的 .acrpapkg 包 (疑似错误页或半截文件)"
    if expect_sha256:
        got = script_package.compute_sha256(path)
        if got.lower() != str(expect_sha256).lower():
            return False, "sha256 校验失败 (期望 {}... 实际 {}...)".format(
                str(expect_sha256)[:12], got[:12])
    return True, ""


def _download_file(url, dest_path, progress=None, cancel=None,
                   expected_sha256="", expected_size=0, timeout=30):
    """流式下载 + 校验 → (ok, msg)。失败清理 .part。

    requests 失败时回退 urllib 双栈 (与 updater._download_one 同思路)。
    progress(got, total)  total 可能为 0；cancel() 返回 True 时中止。
    """
    try:
        d = os.path.dirname(dest_path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
    except Exception as e:
        return False, "无法创建下载目录: {}".format(e)

    part = dest_path + ".part"
    headers = {"User-Agent": USER_AGENT}
    resp = None
    last_err = ""
    if requests is not None:
        try:
            resp = requests.get(url, timeout=timeout, stream=True, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            last_err = "{} {}".format(type(e).__name__, e)
            resp = None
    if resp is None:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
        except Exception as e2:
            if last_err:
                return False, "下载失败: {} / {}".format(last_err, e2)
            return False, "下载失败: {}".format(e2)

    try:
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        got = 0
        with open(part, "wb") as f:
            while True:
                if cancel and cancel():
                    raise MarketplaceError("已取消")
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    try:
                        progress(got, total)
                    except Exception:
                        pass
    except MarketplaceError as e:
        script_package._safe_remove(part)
        return False, str(e)
    except Exception as e:
        script_package._safe_remove(part)
        return False, "下载中断: {}".format(e)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    ok, msg = _verify_download(part, expected_sha256, expected_size)
    if not ok:
        script_package._safe_remove(part)
        return False, msg
    try:
        os.replace(part, dest_path)
    except Exception as e:
        script_package._safe_remove(part)
        return False, "落盘失败: {}".format(e)
    return True, dest_path


def install_package(pkg_path, dest_root, expected_sha256=None):
    """薄封装 script_package.install_package。

    实际安装目录为 <dest_root>/<manifest.id>；返回 manifest (含 _install)。
    """
    try:
        manifest = script_package.read_manifest(pkg_path)
    except Exception:
        manifest = None
    sid = (manifest or {}).get("id") or ""
    dest_dir = os.path.join(dest_root, sid) if sid else dest_root
    return script_package.install_package(pkg_path, dest_dir,
                                          expected_sha256=expected_sha256)


def _download_and_install_package(info, save_dir, progress=None, cancel=None):
    """包模式：下载 .acrpapkg → sha256 校验 → 解包安装 → 返回 .xls 绝对路径。"""
    if not getattr(info, "sha256", ""):
        # 设计 §7.2：包模式要求 sha256，缺失即拒绝 (不静默降级为「不可校验」)
        # 纯校验，先于网络依赖检查，保证离线也能给出正确结论。
        raise MarketplaceError("该脚本包缺少 sha256 校验值，已拒绝安装: {}".format(info.id))
    url = resolve_download_url(info)
    if not url:
        raise MarketplaceError("无法解析下载地址: {}".format(info.id))

    pkg_name = os.path.basename(str(info.pkg).replace("\\", "/")) or \
        "{}-{}{}".format(info.id, info.version, script_package.PKG_EXT)
    cache_dir = os.path.join(save_dir or os.getcwd(), "market_packages")
    pkg_path = os.path.join(cache_dir, pkg_name)

    ok, msg = _download_file(url, pkg_path, progress=progress, cancel=cancel,
                             expected_sha256=info.sha256,
                             expected_size=getattr(info, "size", 0) or 0)
    if not ok:
        raise MarketplaceError("脚本包下载失败: {}".format(msg))

    dest_root = market_install_root(save_dir)
    dest_dir = os.path.join(dest_root, info.id)
    try:
        manifest = script_package.install_package(
            pkg_path, dest_dir, expected_sha256=info.sha256)
    except Exception as e:
        raise MarketplaceError("脚本包安装失败: {}".format(e))

    script_path = (manifest.get("_install") or {}).get("script_path") or ""
    if not script_path or not os.path.exists(script_path):
        raise MarketplaceError("安装后未找到脚本文件: {}".format(info.id))
    log1("脚本已安装: {} -> {}".format(info.name, dest_dir))
    return script_path


def check_update(script_id, local_version):
    """远端 version > local_version 时返回 True (供 market_auto_check_update)。"""
    scripts = _index_cache or _load_builtin_scripts()
    info = _find_script(script_id, scripts)
    if info is None:
        try:
            scripts = fetch_index(force_refresh=True)
        except Exception:
            scripts = []
        info = _find_script(script_id, scripts)
    if info is None:
        return False
    remote = getattr(info, "version", "") or ""
    if not remote:
        return False
    return script_package.version_gt(remote, local_version)


def download_script(script_id, save_dir, progress=None, cancel=None):
    """
    Download (or install) a marketplace script and return the .xls path.

    【向后兼容扩展】相对旧签名 `download_script(script_id, save_dir)` 仅新增带
    默认值的可选参数，返回值语义不变 (始终返回 .xls 路径)。

        * script_id 未命中缓存 → 自动 fetch_index() 后重查；
        * builtin_*       → 沿用 _generate_builtin_script；
        * info.is_package → 下载 .acrpapkg → sha256 校验 → 解包安装到
                            <save_dir>/market_scripts/<id>/ → 返回该目录下 .xls；
        * 否则            → 旧单文件模式 (平铺 save_dir)，行为与返回值语义不变。

    Args:
        script_id: Script identifier
        save_dir: Directory to save the file
        progress: 可选 progress(got, total) 回调 (仅包模式)
        cancel:   可选 cancel() -> bool 回调 (仅包模式)

    Returns:
        Path to saved .xls file

    Raises:
        MarketplaceError on failure
    """
    # Find script in cache
    scripts = _index_cache or _load_builtin_scripts()
    script_info = _find_script(script_id, scripts)
    if script_info is None:
        # 缓存未命中 → 强制刷新后重查 (旧实现直接报「未找到」)
        try:
            scripts = fetch_index(force_refresh=True)
        except Exception:
            scripts = _load_builtin_scripts()
        script_info = _find_script(script_id, scripts)

    if script_info is None:
        raise MarketplaceError("脚本未找到: {}".format(script_id))

    # Handle built-in scripts: generate from templates
    if script_info.id.startswith("builtin_"):
        return _generate_builtin_script(script_info, save_dir)

    # v2 包模式：下载 → sha256 校验 → 解包安装 (图片与脚本同目录)
    if getattr(script_info, "is_package", False):
        return _download_and_install_package(script_info, save_dir, progress, cancel)

    # Download from remote (旧单文件模式；行为与返回值语义保持不变)
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
