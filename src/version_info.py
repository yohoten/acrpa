"""ACRPA 版本号唯一事实来源 (Single Source of Truth).

所有模块应通过 `from version_info import VERSION` 获取版本号，
避免在多个文件中硬编码导致版本号不同步。

VERSION 文件格式 (纯文本; 首行必填, 其余行可选, 以 '#' 开头的行忽略):

    0.1.26
    https://github.com/yohoten/acrpa/releases/download/v0.1.26/ACRPA.zip
    sha256:9f2c...(64 位十六进制)

第三行为下载包校验和, 缺省表示不校验。第二行留空或写 '-' 时,
由 updater 按 GitHub Release 约定自动推导直链 —— 即发版只需改版本号。

VERSION 文件搜索顺序 (先命中先用):
    1. EXE 同级目录           便携版覆盖入口: 手工放一份即可改通道/锁版本
    2. PyInstaller 解压目录   打包内嵌副本 (_MEIPASS)
    3. 项目根目录             源码运行

注意: 此前实现只查项目根目录, 冻结后必然读取失败并回退到
_FALLBACK_VERSION, 导致 EXE 永远自报旧版本号、更新检查结论失真。
新增 EXE 同级目录与 _MEIPASS 两级查找后, 打包产物才能读到真实版本。
"""
import os
import re
import sys

# ── 内置回退版本 (仅当 VERSION 文件全部缺失/损坏时使用) ──
_FALLBACK_VERSION = "0.1.26"

# ── 发布仓库 (直链推导用; 与 updater.UPDATE_SOURCES / DOWNLOAD_MIRRORS 配套) ──
GITHUB_REPO = "yohoten/acrpa"
GITHUB_BRANCH = "main"
RELEASE_ASSET = "ACRPA.zip"

_SHA256_RE = re.compile(r"^sha256\s*[:=]\s*([0-9a-fA-F]{64})$")
_URL_RE = re.compile(r"^https?://", re.I)


def _candidate_roots():
    """按优先级返回可能存放 VERSION 的目录列表 (已去重)。"""
    roots = []
    if getattr(sys, "frozen", False):
        # 冻结后 __file__ 指向临时解压目录, 只有 sys.executable 才是真实安装位置
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.abspath(meipass))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    unique = []
    for r in roots:
        if r and r not in unique:
            unique.append(r)
    return unique


def read_manifest():
    """读取 VERSION 清单 → (文件路径, [有效行]); 全部失败返回 (None, [])。"""
    for root in _candidate_roots():
        path = os.path.join(root, "VERSION")
        try:
            with open(path, encoding="utf-8") as f:
                lines = [l.strip() for l in f
                         if l.strip() and not l.strip().startswith("#")]
        except Exception:
            continue
        if lines:
            return path, lines
    return None, []


def get_manifest_path():
    """返回实际生效的 VERSION 文件路径 (用于诊断)。"""
    return read_manifest()[0]


def get_version():
    """VERSION 首行版本号；全部失败时回退内置版本。"""
    lines = read_manifest()[1]
    if lines:
        return lines[0]
    return _FALLBACK_VERSION


def get_download_url():
    """VERSION 中声明的第一条下载直链；缺省或非 URL 时返回空串。"""
    urls = get_download_urls()
    return urls[0] if urls else ""


def get_download_urls():
    """VERSION 中声明的全部下载直链 (按声明顺序, 注释与 sha256 行除外)。

    语义提醒: 本地 VERSION 描述的是【当前已安装版本】, 因此 updater 只会采纳
    其中版本号与升级目标一致的直链 —— 否则会把用户"升级"回同一个旧包。
    """
    return [line for line in read_manifest()[1][1:] if _URL_RE.match(line)]


def get_sha256():
    """VERSION 中形如 `sha256:<hex>` 的行；缺省返回空串 (表示不校验)。"""
    for line in read_manifest()[1]:
        m = _SHA256_RE.match(line)
        if m:
            return m.group(1).lower()
    return ""


def release_tag(version=None):
    """把版本号规范为 Release tag (补 v 前缀)。"""
    ver = version or get_version()
    ver = str(ver).strip()
    return ver if ver.lower().startswith("v") else "v{}".format(ver)


def release_asset_urls(version=None):
    """按 GitHub Release 约定推导下载直链 (含 tag 写法差异的多种可能)。

    本仓库历史 tag 存在两种写法: 三段段 `v0.1.25` 与四段段 `v0.1.25.0`。
    只推导一种写法, 另一种写法下必然 404 —— 而 404 的表现是"更新可用却下不动",
    对用户是彻底的失败。因此两种写法都给出, 由下载环节逐个尝试: 多试一个 URL
    的代价 (一次极轻量的请求) 远小于链接失效。
    """
    tag = release_tag(version)
    tags = [tag]
    if tag.count(".") == 2:                 # v0.1.26 → 再补一个 v0.1.26.0
        tags.append(tag + ".0")
    return ["https://github.com/{}/releases/download/{}/{}".format(
        GITHUB_REPO, t, RELEASE_ASSET) for t in tags]


def release_asset_url(version=None):
    """按 GitHub Release 约定推导下载直链 (首个候选; 兼容既有调用方)。"""
    return release_asset_urls(version)[0]


def raw_mirror_urls():
    """仓库内 dist/ 副本的镜像直链 (最后的兜底通道)。

    顺序按实测可达性排: jsDelivr 国内有节点且实测 200; raw.githubusercontent 在
    部分网络被 TLS 阻断; Gitee 的 raw 路径对 dist/ 下的大文件实测返回 403, 放最后。

    这些地址不带版本号, 永远返回"仓库里最后提交的那份包", 因此只能作为最后
    手段 —— 由调用方用 sha256 或包内 VERSION 兜住"拿到旧包"的风险。
    """
    return [
        "https://cdn.jsdelivr.net/gh/{}@{}/dist/{}".format(
            GITHUB_REPO, GITHUB_BRANCH, RELEASE_ASSET),
        "https://raw.githubusercontent.com/{}/{}/dist/{}".format(
            GITHUB_REPO, GITHUB_BRANCH, RELEASE_ASSET),
        "https://gitee.com/{}/raw/master/dist/{}".format(
            GITHUB_REPO, RELEASE_ASSET),
    ]


def get_app_dir():
    """可写的应用目录 (更新包落盘处)。

    冻结时为 EXE 所在目录 (便携版, 与 state.CONFIG_PATH 口径一致),
    源码运行时为项目根目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 模块级常量: 模块导入时即确定当前版本 ──
VERSION = get_version()
