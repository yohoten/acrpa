"""Command registry — single source of truth for all automation commands."""
_registry = []


def register(name, description, params, handler=None):
    """Register a command. handler=None means engine provides it later."""
    _registry.append((name, description, params, handler))


def get_handler(name):
    for n, _, _, h in _registry:
        if n == name: return h
    return None


def list_names():
    return [n for n, _, _, _ in _registry]


def list_all():
    return list(_registry)


# ── 19 commands — name, description, params ──
register("找图",     "找到目标图并悬停",           "图片名, 精度(默认0.96)")
register("区域找图", "限定范围找图(更快)",          "图片名, 精度, 左, 上, 宽, 高, 灰度")
register("点图",     "找到图片并点击",              "图片名, 精度(默认0.96)")
register("区域点图", "限定范围找图并点击",          "图片名, 精度, 左, 上, 宽, 高, 灰度, 按键, 次数")
register("按键",     "模拟键盘按键",                "按键名, 次数(默认1), 间隔(默认0.1)")
register("热键",     "组合键操作",                  "按键1, 按键2")
register("输入",     "剪贴板方式输入文本",          "内容")
register("写入",     "模拟真实键盘逐字输入（支持DD驱动）", "内容, 按键间隔(秒), 模式(auto/direct/simulate)")
register("等待",     "延时等待(秒)",                "秒数")
register("坐标",     "点击屏幕坐标",                "x, y, 按键(左/右), 次数, 间隔")
register("悬停",     "鼠标移动到坐标",              "x, y")
register("拖拽",     "鼠标拖拽到坐标",              "x, y")
register("滚轮",     "鼠标滚轮滚动",                "距离(负=下, 正=上)")
register("相移",     "相对当前位置移动",            "dx, dy")
register("按下",     "按下按键不松开",              "按键名")
register("释放",     "释放已按下的按键",            "按键名")
register("复制",     "Ctrl+A, Ctrl+C",              "无参数")
register("粘贴",     "Ctrl+A, Ctrl+V",              "无参数")
register("截屏",     "截图并保存",                  "名称, 保存路径")
register("代码",     "执行txt中的Python代码",       "文件名(不含后缀)")
register("如果",     "条件判断（如果为真则执行）",    "条件表达式")
register("否则",     "否则分支（否则执行）",          "无参数")
register("结束如果", "结束条件块",                   "无参数")
register("循环开始", "开始循环",                     "次数或条件表达式")
register("循环结束", "结束当前循环",                 "无参数")
register("跳出循环", "立即跳出循环",                 "无参数")

# ── Window Management Commands (新增窗口管理命令) ──
register("激活窗口", "激活指定标题的窗口（支持模糊匹配）", "窗口标题")
register("关闭窗口", "关闭指定窗口", "窗口标题")
register("最小化窗口", "最小化指定窗口", "窗口标题")
register("最大化窗口", "最大化指定窗口", "窗口标题")
register("获取窗口位置", "获取窗口坐标和大小并保存到变量", "窗口标题, X变量名, Y变量名, 宽变量名, 高变量名")
register("等待窗口", "等待窗口出现或消失", "窗口标题, 超时秒数, 存在/不存在")
register("窗口坐标", "相对于窗口左上角的坐标点击（窗口移动后仍准确）", "窗口标题, x, y, 按键(左/右), 次数, 间隔")
register("设置变量", "设置变量值", "变量名, 值")
register("读取剪贴板", "读取剪贴板内容到变量", "变量名")
register("字符串处理", "截取/替换字符串", "源变量, 操作类型(截取/替换), 参数, 目标变量")
register("数学运算", "执行数学计算", "表达式, 结果变量")

# ── OCR Commands (OCR文字识别命令) ──
register("识别文字", "OCR识别屏幕区域文字并存入变量", "左, 上, 宽, 高, 变量名")
register("等待文字", "等待指定文字出现或消失", "目标文字, 超时秒数, 存在/不存在, 左, 上, 宽, 高")
register("点击文字", "找到文字位置并点击", "目标文字, 置信度, 按键(左/右), 左, 上, 宽, 高")

# ── AI 增强命令 (AI Enhancement Commands) ──
register("AI找图", "AI分析截屏,用自然语言描述找元素并点击", "描述, 置信度(0-1), 操作(click/hover/find), 按键(左/右)")
register("AI识别界面", "AI分析截屏列出所有UI元素存入变量", "变量名前缀(默认ui)")
register("AI优化建议", "AI分析执行数据给出脚本优化建议", "模式(log/save/var), 变量名")

# ── 工作流命令 (Workflow Commands) ──
register("运行工作流", "调用工作流文件(.json)执行多脚本编排", "工作流路径")
register("工作流变量", "设置工作流级共享变量", "变量名, 值")

# ── 浏览器自动化命令 (Browser Automation Commands, 需 pip install playwright) ──
register("打开网页",   "浏览器打开指定URL",           "网址")
register("浏览器点击", "点击页面元素(CSS选择器或text=)", "选择器")
register("浏览器输入", "在输入框中填入文本",          "选择器, 文本")
register("等待元素",   "等待页面元素出现或消失",      "选择器, 超时秒数, 出现/消失")
register("浏览器截图", "截取页面或元素截图",          "名称, 目标(page或选择器)")

def _set_handler(name, handler):
    """Update a registered command's handler (called by engine)."""
    global _registry
    for i, (n, d, p, _) in enumerate(_registry):
        if n == name:
            _registry[i] = (n, d, p, handler)
            return
