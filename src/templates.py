"""
Script templates for quick-start automation scenarios.
Also includes AI prompt templates for script generation.

P0 Optimization #2: Lazy loading of templates to reduce startup time and memory usage.
"""

# Template metadata - lightweight, loaded immediately
TEMPLATE_METADATA = {
    "登录流程": {"rows": 10, "category": "办公"},
    "表单填写": {"rows": 9, "category": "办公"},
    "数据采集": {"rows": 8, "category": "办公"},
    "批量点击": {"rows": 5, "category": "其他"},
    "页面截图": {"rows": 6, "category": "系统"},
    "文件下载": {"rows": 6, "category": "系统"},
    "窗口切换": {"rows": 4, "category": "系统"},
    "文本编辑": {"rows": 8, "category": "办公"},
    "滚动浏览": {"rows": 6, "category": "其他"},
    "右键菜单": {"rows": 5, "category": "其他"},
}

# Cache for loaded templates
_TEMPLATES_CACHE = {}


def get_template(name):
    """
    Lazy load a single template on demand (P0 optimization #2).
    
    Args:
        name: Template name (e.g., "登录流程")
        
    Returns:
        List of ScriptData objects or None if not found
    """
    if name in _TEMPLATES_CACHE:
        return _TEMPLATES_CACHE[name]
    
    # Load template data only when requested
    template_data = _load_template_data(name)
    if template_data:
        _TEMPLATES_CACHE[name] = template_data
    
    return template_data


def list_templates():
    """
    Return list of available template names without loading them.
    
    Returns:
        List of template name strings
    """
    return list(TEMPLATE_METADATA.keys())


def get_template_info(name):
    """
    Get metadata for a template without loading the full data.
    
    Args:
        name: Template name
        
    Returns:
        Dict with metadata or None if not found
    """
    return TEMPLATE_METADATA.get(name)


def _load_template_data(name):
    """
    Internal function to load template data from definition.
    Only called when template is first accessed.
    """
    from scriptdata import ScriptData
    
    template_defs = {
        "登录流程": [
            ScriptData("等待", ["2", None, None, None, None, None, None, None]),
            ScriptData("找图", ["login_input", "0.9", None, None, None, None, None, None]),
            ScriptData("坐标", ["<填写X>", "<填写Y>", "左", "1", "0.1", None, None, None]),
            ScriptData("输入", ["username", None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("找图", ["password_input", "0.9", None, None, None, None, None, None]),
            ScriptData("坐标", ["<填写X>", "<填写Y>", "左", "1", "0.1", None, None, None]),
            ScriptData("输入", ["password", None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("找图", ["login_btn", "0.9", None, None, None, None, None, None]),
            ScriptData("点图", ["login_btn", "0.9", None, None, None, None, None, None]),
        ],
        "表单填写": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("点图", ["field1", "0.9", None, None, None, None, None, None]),
            ScriptData("输入", ["value1", None, None, None, None, None, None, None]),
            ScriptData("按键", ["tab", "1", "0.1", None, None, None, None, None]),
            ScriptData("输入", ["value2", None, None, None, None, None, None, None]),
            ScriptData("按键", ["tab", "1", "0.1", None, None, None, None, None]),
            ScriptData("输入", ["value3", None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("点图", ["submit_btn", "0.9", None, None, None, None, None, None]),
        ],
        "数据采集": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("点图", ["export_btn", "0.9", None, None, None, None, None, None]),
            ScriptData("等待", ["2", None, None, None, None, None, None, None]),
            ScriptData("复制", [None, None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("热键", ["alt", "tab", None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("粘贴", [None, None, None, None, None, None, None, None]),
        ],
        "批量点击": [
            ScriptData("点图", ["target", "0.9", None, None, None, None, None, None]),
            ScriptData("等待", ["0.3", None, None, None, None, None, None, None]),
            ScriptData("点图", ["target", "0.9", None, None, None, None, None, None]),
            ScriptData("等待", ["0.3", None, None, None, None, None, None, None]),
            ScriptData("点图", ["target", "0.9", None, None, None, None, None, None]),
        ],
        "页面截图": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("截屏", ["page", "screenshots", None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("滚轮", ["-500", None, None, None, None, None, None, None]),
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("截屏", ["page2", "screenshots", None, None, None, None, None, None]),
        ],
        "文件下载": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("点图", ["download_btn", "0.9", None, None, None, None, None, None]),
            ScriptData("等待", ["3", None, None, None, None, None, None, None]),
            ScriptData("按键", ["down", "1", "0.1", None, None, None, None, None]),
            ScriptData("按键", ["enter", "1", "0.1", None, None, None, None, None]),
            ScriptData("等待", ["2", None, None, None, None, None, None, None]),
        ],
        "窗口切换": [
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("热键", ["alt", "tab", None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("点图", ["target_window", "0.9", None, None, None, None, None, None]),
        ],
        "文本编辑": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("复制", [None, None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.3", None, None, None, None, None, None, None]),
            ScriptData("热键", ["ctrl", "a", None, None, None, None, None, None]),
            ScriptData("等待", ["0.3", None, None, None, None, None, None, None]),
            ScriptData("按键", ["delete", "1", "0.1", None, None, None, None, None]),
            ScriptData("等待", ["0.3", None, None, None, None, None, None, None]),
            ScriptData("粘贴", [None, None, None, None, None, None, None, None]),
        ],
        "滚动浏览": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("滚轮", ["-300", None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("滚轮", ["-300", None, None, None, None, None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("滚轮", ["300", None, None, None, None, None, None, None]),
        ],
        "右键菜单": [
            ScriptData("等待", ["1", None, None, None, None, None, None, None]),
            ScriptData("坐标", ["100", "200", "右", "1", "0.1", None, None, None]),
            ScriptData("等待", ["0.5", None, None, None, None, None, None, None]),
            ScriptData("按键", ["down", "1", "0.1", None, None, None, None, None]),
            ScriptData("按键", ["enter", "1", "0.1", None, None, None, None, None]),
        ],
    }
    
    return template_defs.get(name)


# Backward compatibility: TEMPLATES dict (deprecated, use get_template() instead)
# This will be removed in future versions
class _LazyTemplatesDict(dict):
    """Lazy-loading dictionary that loads templates on first access."""
    def __getitem__(self, key):
        if key not in TEMPLATE_METADATA:
            raise KeyError(key)
        if key not in dict.keys(self):
            template_data = _load_template_data(key)
            if template_data:
                dict.__setitem__(self, key, template_data)
            else:
                raise KeyError(key)
        return dict.__getitem__(self, key)
    
    def __contains__(self, key):
        return key in TEMPLATE_METADATA


# Create lazy-loading TEMPLATES object for backward compatibility
TEMPLATES = _LazyTemplatesDict()


# AI Quick Prompts for common scenarios - 基于实际模板格式优化
AI_QUICK_PROMPTS = {
    "打开记事本输入": "打开记事本程序，输入'Hello World'，按Ctrl+S保存为test.txt到桌面",
    "浏览器搜索": "打开Chrome浏览器，访问百度网站，搜索'Python教程'，等待3秒后截图保存",
    "窗口切换": "按Alt+Tab切换到上一个窗口，等待1秒，然后按Win+D显示桌面",
    "文件复制": "打开资源管理器(Win+E)，选择D盘的文件，复制到E盘备份文件夹",
    "表单填写": "在网页表单中依次输入用户名admin、密码123456，然后点击登录按钮",
    "批量重命名": "选中文件夹中的所有jpg图片，依次重命名为photo_001.jpg、photo_002.jpg等",
    "PDF转图片": "打开PDF阅读器，逐页截图保存为PNG格式到D:/pdf_images文件夹",
    "定时截屏": "每隔5分钟截屏一次，保存到D:/monitoring文件夹，连续执行10次",
    "Excel数据录入": "打开Excel，在A1单元格输入姓名，Tab键移动到B1输入年龄，重复5行后保存",
    "邮件发送": "使用Python代码发送邮件，收件人test@example.com，主题测试，正文Hello",
    "数据抓取": "打开淘宝网站，搜索'机械键盘'，截取前3页的商品名称和价格信息",
    "自动填表": "从CSV文件读取用户数据，逐行填写到网页注册表单并提交",
    "系统清理": "打开运行对话框(Win+R)，输入cleanmgr清理C盘，点击确定开始清理",
    "软件安装": "下载安装包到D盘，双击运行，一路点击下一步直到完成安装",
    "视频录制": "打开OBS软件，点击开始录制，等待10秒后点击停止录制，保存视频文件",
}

def build_ai_prompt(user_description):
    """
    Build a comprehensive AI prompt for RPA script generation.
    Optimized to match the exact format of template Excel files.
    
    Args:
        user_description: User's natural language description of the task
        
    Returns:
        Formatted prompt string for AI model
    """
    
    # 精确的CSV格式规范（基于实际模板）
    format_guide = """## [o] Excel脚本格式规范（严格遵循以下规则）

### 输出格式要求
你必须输出纯CSV文本，内容结构与模板文件完全一致：

**第1行（标题行）：**
操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9

**第2行及之后（数据行）：**
命令名称,参数值1,参数值2,...,参数値9

### 核心规则（必须遵守）
1. **空值表示**：所有未使用的参数位置必须写入 `None`（首字母大写，不带引号）
   ✅ 正确：`等待,2,None,None,None,None,None,None,None,None`
   ❌ 错误：`等待,2,,,,,,` 或 `等待,2,"","",""`

2. **参数数量**：每行必须恰好有10个字段（命令 + 9个参数），即末尾有9个逗号分隔的None
   - 命令字段后跟9个逗号分隔的值，即使全部为None

3. **禁止多余符号**：行末不要有多余逗号或空白

4. **不要添加任何Markdown标记**：禁止输出 ```csv 或 ``` 代码块，直接输出纯文本

5. **不要输出解释性文字**：只输出CSV内容，前后不要添加任何说明

### 标准输出示例（直接从模板中摘录）
```
操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9
热键,win,r,None,None,None,None,None,None,None
等待,2,None,None,None,None,None,None,None,None
输入,cmd,None,None,None,None,None,None,None,None
按键,enter,None,None,None,None,None,None,None,None
热键,ctrl,s,None,None,None,None,None,None,None
```

注意：上例仅为展示格式，实际输出时不要包含 ```csv 标记。"""

    # 增强的Windows自动化命令参考（突出实用技巧）
    command_ref = """## [o] Windows自动化命令详解

你是一位资深Windows自动化专家，熟悉以下所有命令的最佳用法。

### 一、键盘操作（优先使用热键组合）
| 命令 | 参数说明 | 示例 |
|------|----------|------|
| 热键 | 修饰键(ctrl/alt/shift/win), 功能键 | `热键,win,r,None,...` 打开运行 |
| 按键 | 单键名, 次数, 间隔 | `按键,enter,1,0.1,None,...` |
| 输入 | 文本内容（推荐） | `输入,Hello World,None,...` |
| 写入 | 文本, 每字间隔(秒) | `写入,Hello,0.05,None,...` |
| 按下/释放 | 按键名 | `按下,shift,None,...` |

**常用Windows快捷键（优先使用热键）**：
- `热键,win,r` - 打开运行对话框
- `热键,win,e` - 打开文件资源管理器
- `热键,win,d` - 显示桌面
- `热键,alt,tab` - 切换窗口
- `热键,alt,f4` - 关闭当前窗口
- `热键,ctrl,c` / `ctrl,v` / `ctrl,a` / `ctrl,s`

### 二、图像识别（优先使用区域限定）
| 命令 | 参数说明 | 示例 |
|------|----------|------|
| 区域找图 | 图片名, 置信度(0.9-0.98), X, Y, 宽, 高, 灰度(True/False) | `区域找图,btn,0.96,100,100,400,300,True,None,None` |
| 区域点图 | 同上, 再加按键(左/右), 点击次数 | `区域点图,login,0.95,200,150,300,200,True,左,1` |
| 找图/点图 | 全屏版本，参数较少 | `点图,submit,0.96,None,...` |

**经验值**：
- 置信度：首次用0.96，失败后降至0.9
- 灰度匹配设为True可提速30%
- 搜索区域越小越快，尽量使用区域版本

### 三、鼠标与流程控制
| 命令 | 参数 | 示例 |
|------|------|------|
| 坐标 | X, Y, 按键(左/右), 次数, 间隔 | `坐标,500,300,左,1,0.1,None,...` |
| 滚轮 | 滚动量（负下正上） | `滚轮,-300,None,...` |
| 悬停 | X, Y | `悬停,400,250,None,...` |
| 拖拽 | 目标X, 目标Y | `拖拽,600,400,None,...` |
| 等待 | 秒数（支持小数） | `等待,1.5,None,...` |
| 截屏 | 文件名, 保存路径 | `截屏,result,E:/screenshots,None,...` |
| 代码 | 外部Python脚本名 | `代码,mail.py,None,...` |

**流程控制建议**：
- 每个关键操作后添加等待（打开程序后2-3秒，网页加载3-5秒，简单操作0.5-1秒）
- 使用循环实现重试（在Excel中通过重复行或外部脚本实现）
- 复杂逻辑用`代码`调用Python脚本"""

    # 最佳实践场景模板（可直接参考）
    best_practices = """## [o] 典型场景脚本模板

### 模板1：打开记事本并保存文件
```
操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9
等待,2,None,None,None,None,None,None,None,None
热键,win,r,None,None,None,None,None,None,None
等待,1,None,None,None,None,None,None,None,None
输入,notepad,None,None,None,None,None,None,None,None
按键,enter,1,0.1,None,None,None,None,None,None
等待,2,None,None,None,None,None,None,None,None
输入,自动化测试内容,None,None,None,None,None,None,None,None
热键,ctrl,s,None,None,None,None,None,None,None
等待,1,None,None,None,None,None,None,None,None
输入,test.txt,None,None,None,None,None,None,None,None
按键,enter,1,0.1,None,None,None,None,None,None
```

### 模板2：浏览器搜索并截图
```
操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9
热键,win,r,None,None,None,None,None,None,None,None
等待,1,None,None,None,None,None,None,None,None
输入,chrome,None,None,None,None,None,None,None,None
按键,enter,1,0.1,None,None,None,None,None,None
等待,3,None,None,None,None,None,None,None,None
输入,https://www.baidu.com,None,None,None,None,None,None,None,None
按键,enter,1,0.1,None,None,None,None,None,None
等待,3,None,None,None,None,None,None,None,None
输入,Python自动化,None,None,None,None,None,None,None,None
按键,enter,1,0.1,None,None,None,None,None,None
等待,3,None,None,None,None,None,None,None,None
截屏,baidu_result,E:/screenshots,None,None,None,None,None,None
```

### 模板3：图像识别点击按钮（区域限定）
```
操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9
等待,1,None,None,None,None,None,None,None,None
区域找图,login_btn,0.96,200,150,400,300,True,None,None
等待,0.5,None,None,None,None,None,None,None,None
区域点图,login_btn,0.96,200,150,400,300,True,左,1
等待,2,None,None,None,None,None,None,None,None
```

**记住**：以上示例中的逗号分隔是严格的，每个None字段必须保留。实际输出时请根据用户需求调整参数值。"""

    # 最终组装
    prompt = f"""# 角色定义
你是ACRPA RPA自动化专家，拥有10年Windows桌面自动化经验。你必须严格按照以下规范生成脚本。

{format_guide}

{command_ref}

{best_practices}

---

**用户任务描述：**
{user_description}

请根据上述规范生成完整的Excel脚本（CSV格式），从标题行"操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9"开始，每行严格遵守10个字段，未使用的参数写None。不要输出任何其他文字，不要包含Markdown代码块标记。"""

    return prompt


def normalize_ai_output(raw_output: str) -> str:
    """
    对AI生成的CSV内容进行规范化：
    - 移除首尾空白
    - 确保第一行是标题行（如果不是则补充）
    - 修正不完整的None（如缺失的None自动补全）
    - 移除Markdown代码块标记
    
    Args:
        raw_output: AI生成的原始输出字符串
        
    Returns:
        规范化后的CSV字符串
    """
    import re
    
    if not raw_output or not raw_output.strip():
        return ""
    
    # 移除可能的Markdown代码块标记
    raw_output = re.sub(r'^```\w*\n', '', raw_output)
    raw_output = re.sub(r'\n```$', '', raw_output)
    
    # 分割行并清理
    lines = [line.strip() for line in raw_output.strip().split('\n') if line.strip()]
    
    if not lines:
        return ""
    
    # 确保第一行为标题行
    header = "操作,参数1,参数2,参数3,参数4,参数5,参数6,参数7,参数8,参数9"
    if not lines[0].startswith("操作"):
        lines.insert(0, header)
    
    # 处理每一行
    normalized = []
    for line in lines:
        parts = line.split(',')
        
        # 确保有10个字段
        if len(parts) < 10:
            parts.extend(['None'] * (10 - len(parts)))
        elif len(parts) > 10:
            parts = parts[:10]
        
        # 将空字符串转为None
        parts = [p if p.strip() not in ('', '""', "''") else 'None' for p in parts]
        
        normalized.append(','.join(parts))
    
    return '\n'.join(normalized)
