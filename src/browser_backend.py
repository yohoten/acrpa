"""
Browser Automation Backend for ACRPA — Playwright-powered web automation.

Supports:
- 打开网页: Navigate to URL
- 浏览器点击: Click element by selector/text
- 浏览器输入: Type text into element
- 等待元素: Wait for element to appear/disappear
- 浏览器截图: Screenshot page or element

体积说明:
  - pip install playwright: ~5 MB (Python package)
  - playwright install chromium: ~150 MB (Chromium browser)
  - 总计约 155 MB，不打包进 ACRPA.exe
  - 未安装时命令会提示安装方法

Usage in Excel scripts:
  打开网页, https://example.com
  浏览器点击, #login-btn
  浏览器输入, #username, admin
  等待元素, .result, 10, 出现
  浏览器截图, result, page
"""

import os
import time
import state
from utils import log1

# ── Lazy Playwright singleton ──
_playwright = None          # Playwright instance
_browser = None             # Browser instance
_page = None                # Current page
_available = None           # None=未检测, True=可用, False=不可用
_init_attempted = False     # 是否已尝试初始化


def _check_playwright_available():
    """Check if playwright is installed (import only, no browser launch)."""
    global _available
    if _available is not None:
        return _available
    try:
        import playwright.sync_api
        _available = True
    except ImportError:
        _available = False
    return _available


def _get_page():
    """Lazy-init Playwright browser + page. Returns page or None."""
    global _page, _browser, _playwright, _init_attempted

    if _page is not None:
        # Check if page is still alive
        try:
            _page.title()
            return _page
        except Exception:
            # Page closed externally, re-create
            _page = None
            _browser = None

    if _init_attempted and _page is None:
        return None

    if not _check_playwright_available():
        _init_attempted = True
        log1("Playwright 未安装。请运行: pip install playwright && playwright install chromium", "warning")
        return None

    try:
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()

        # Launch browser (headless by default for RPA; can be configured)
        headless = getattr(state, 'BROWSER_HEADLESS', True) if hasattr(state, 'BROWSER_HEADLESS') else True
        slow_mo = getattr(state, 'BROWSER_SLOW_MO', 0) if hasattr(state, 'BROWSER_SLOW_MO') else 0

        _browser = _playwright.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
        )
        _page = _browser.new_page()
        _page.set_default_timeout(30000)  # 30s default timeout

        log1("Playwright 浏览器已启动 (headless={})".format(headless), "info")
        _init_attempted = True
        return _page

    except Exception as e:
        _init_attempted = True
        log1("Playwright 浏览器启动失败: {}".format(e), "error")
        _cleanup()
        return None


def _cleanup():
    """Close browser and stop Playwright."""
    global _page, _browser, _playwright
    try:
        if _page:
            _page.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _page = None
    _browser = None
    _playwright = None


def browser_close():
    """Explicitly close the browser."""
    _cleanup()
    log1("浏览器已关闭")


# ======================================================================
# Command Handlers (called from engine.py)
# ======================================================================

def _browser_navigate(row, script_dir=""):
    """打开网页 — navigate to URL.
    
    Excel: 打开网页, https://example.com
    """
    url = ""
    if hasattr(row, 'args'):
        url = row.args[0] if len(row.args) > 0 else ""
    else:
        url = str(row[1].value) if row[1].value else ""

    if not url:
        log1("打开网页: 未指定URL", "error")
        return

    page = _get_page()
    if not page:
        return

    try:
        log1("打开网页: {}".format(url))
        page.goto(url, wait_until="domcontentloaded")
        # Store current URL in variables
        try:
            from engine import engine
            engine.variables["browser_url"] = page.url
            engine.variables["browser_title"] = page.title()
        except Exception:
            pass
        log1("网页已加载: {}".format(page.title()))
    except Exception as e:
        log1("打开网页失败: {}".format(e), "error")


def _browser_click(row, script_dir=""):
    """浏览器点击 — click element by CSS selector or text.
    
    Excel: 浏览器点击, #login-btn
           浏览器点击, text=登录
    """
    selector = ""
    if hasattr(row, 'args'):
        selector = row.args[0] if len(row.args) > 0 else ""
    else:
        selector = str(row[1].value) if row[1].value else ""

    if not selector:
        log1("浏览器点击: 未指定选择器", "error")
        return

    page = _get_page()
    if not page:
        return

    try:
        log1("浏览器点击: {}".format(selector))
        page.click(selector, timeout=15000)
        log1("已点击元素: {}".format(selector))
    except Exception as e:
        log1("浏览器点击失败 '{}': {}".format(selector, e), "error")


def _browser_input(row, script_dir=""):
    """浏览器输入 — type text into input element.
    
    Excel: 浏览器输入, #username, admin
           浏览器输入, #search, ${keyword}
    """
    selector = ""
    text = ""
    if hasattr(row, 'args'):
        selector = row.args[0] if len(row.args) > 0 else ""
        text = row.args[1] if len(row.args) > 1 else ""
    else:
        selector = str(row[1].value) if row[1].value else ""
        text = str(row[2].value) if row[2].value else ""

    if not selector:
        log1("浏览器输入: 未指定选择器", "error")
        return

    page = _get_page()
    if not page:
        return

    try:
        # Resolve ${var} references
        import re
        try:
            from engine import engine
            variables = engine.variables
        except Exception:
            variables = {}
        text = re.sub(r'\$\{([^}]+)\}', lambda m, v=variables: str(
            v.get(m.group(1), '')), str(text))

        log1("浏览器输入: {} ← '{}'".format(selector, text[:50]))
        page.fill(selector, text)
    except Exception as e:
        log1("浏览器输入失败 '{}': {}".format(selector, e), "error")


def _browser_wait_element(row, script_dir=""):
    """等待元素 — wait for element to appear or disappear.
    
    Excel: 等待元素, .result, 10, 出现
           等待元素, .loading, 30, 消失
    """
    selector = ""
    timeout = 10.0
    should_exist = True

    if hasattr(row, 'args'):
        selector = row.args[0] if len(row.args) > 0 else ""
        timeout = float(row.args[1]) if len(row.args) > 1 and row.args[1] else 10.0
        should_exist = (str(row.args[2]).strip() != "消失") if (len(row.args) > 2 and row.args[2]) else True
    else:
        selector = str(row[1].value) if row[1].value else ""
        timeout = float(row[2].value) if row[2].value else 10.0
        should_exist = (str(row[3].value).strip() != "消失") if row[3].value else True

    if not selector:
        log1("等待元素: 未指定选择器", "error")
        return

    page = _get_page()
    if not page:
        return

    try:
        log1("等待元素 '{}' {} (超时: {}s)".format(
            selector, "出现" if should_exist else "消失", timeout))

        wait_ms = int(timeout * 1000)
        if should_exist:
            page.wait_for_selector(selector, state="visible", timeout=wait_ms)
            log1("元素 '{}' 已出现".format(selector))
        else:
            page.wait_for_selector(selector, state="hidden", timeout=wait_ms)
            log1("元素 '{}' 已消失".format(selector))
    except Exception as e:
        log1("等待元素 '{}' 超时: {}".format(selector, e), "warning")


def _browser_screenshot(row, script_dir=""):
    """浏览器截图 — take screenshot of page or element.
    
    Excel: 浏览器截图, result, page
           浏览器截图, header, #header
    """
    name = "browser"
    target = "page"  # "page" or CSS selector

    if hasattr(row, 'args'):
        name = row.args[0] if len(row.args) > 0 and row.args[0] else "browser"
        target = row.args[1] if len(row.args) > 1 and row.args[1] else "page"
    else:
        name = str(row[1].value) if row[1].value else "browser"
        target = str(row[2].value) if row[2].value else "page"

    page = _get_page()
    if not page:
        return

    try:
        import datetime
        ts = datetime.datetime.now().strftime("%m%d_%H%M%S")
        ss_dir = os.path.join(os.path.dirname(state.CONFIG_PATH), "screenshots")
        os.makedirs(ss_dir, exist_ok=True)

        fp = os.path.join(ss_dir, "browser_{}_{}.png".format(name, ts))

        if target == "page":
            page.screenshot(path=fp, full_page=False)
        else:
            element = page.locator(target)
            element.screenshot(path=fp)

        log1("浏览器截图已保存: {}".format(fp))
    except Exception as e:
        log1("浏览器截图失败: {}".format(e), "error")


# ======================================================================
# Browser is available? (for status reporting)
# ======================================================================

def browser_get_status():
    """Return browser availability status."""
    available = _check_playwright_available()
    if available:
        return {"available": True, "name": "Playwright (Chromium)", "connected": _page is not None}
    return {"available": False, "name": None, "connected": False, "hint": "pip install playwright && playwright install chromium"}
