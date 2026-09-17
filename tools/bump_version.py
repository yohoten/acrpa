"""ACRPA 版本号全局更新工具。

用法 (在项目根目录运行):
    python tools/bump_version.py --show              # 显示当前版本
    python tools/bump_version.py 0.1.25              # 设为指定版本 (支持 0.1.23beta / 0.1.23-beta)
    python tools/bump_version.py --patch             # 递增 patch: 0.1.24 → 0.1.25
    python tools/bump_version.py --minor             # 递增 minor: 0.1.24 → 0.2.0
    python tools/bump_version.py --major             # 递增 major: 0.1.24 → 1.0.0
    python tools/bump_version.py --verify            # 扫描残留旧版本号硬编码

原理:
    版本号唯一事实来源是项目根目录 VERSION 文件 (第一行)。
    所有模块 (updater/dialogs/settings_window/ACRPA) 已统一从
    src/version_info.py 读取，因此只需更新 VERSION 文件即可全局生效。
    本工具还会顺带同步 README 顶部的 `version：vX.Y.Z` 标记行与
    Release 直链中的 tag，避免"文件里写的"和"程序自报的"版本号不一致。

说明:
    - 支持预发布后缀: 0.1.23beta / 0.1.23-beta / 0.1.23rc1
    - --patch/--minor/--major 递增数值部分并【保留后缀】: 0.1.23beta → 0.1.24beta
    - VERSION 第二行起的直链与 sha256 行会原样保留, 不会被清零
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(BASE, "VERSION")
SRC_DIR = os.path.join(BASE, "src")

# 合法版本号: 主.次.修订 + 可选预发布后缀 (beta / -beta / rc1 等)
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)([a-zA-Z0-9.\-]*)$")


def read_version():
    """读取当前版本号 (VERSION 文件第一行)。"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.readline().strip()
    except Exception:
        return None


def read_download_url():
    """读取首条下载直链 (VERSION 中第一条 http(s) 行)。"""
    urls = read_download_urls()
    return urls[0] if urls else ""


def read_download_urls():
    """VERSION 中声明的全部 http(s) 行。"""
    return [l for l in read_manifest_lines()
            if re.match(r"^https?://", l, re.I)]


def read_manifest_lines():
    """读取 VERSION 首行之后的全部有效行 (直链 / sha256), 供原样保留。"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f
                     if l.strip() and not l.strip().startswith("#")]
    except Exception:
        return []
    return lines[1:]


def write_version(old_version, new_version, tail_lines):
    """写入新版本号到 VERSION 文件 (第二行起原样保留)。"""
    out = [new_version] + list(tail_lines or [])
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("[OK] VERSION 已更新: {} → {}".format(old_version or "(无)", new_version))


def sync_readme(old_version, new_version):
    """同步 README 顶部版本标记与 Release 直链中的 tag → [改动文件列表]。"""
    if not old_version:
        return []
    try:
        with open(README_FILE, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return []

    original = text
    text = re.sub(r"(?m)^(version[：:]\s*)v?[\w.\-]+",
                  lambda m: m.group(1) + "v" + new_version, text)
    # Release 直链形如 .../download/v0.1.25/ACRPA.zip
    text = text.replace("/v{}/".format(old_version), "/v{}/".format(new_version))

    if text == original:
        return []
    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return ["README.md"]


def bump(current, part):
    """递增版本号的数值部分，保留预发布后缀。

    part: major / minor / patch
    例: 0.1.23beta --patch → 0.1.24beta
    """
    m = _VERSION_RE.match(current.strip())
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    suffix = m.group(4)  # 例如 "beta" / "-beta" / "" (正式版)

    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:  # patch
        patch += 1

    return "{}.{}.{}{}".format(major, minor, patch, suffix)


def verify(old_version):
    """扫描 src/ 下是否残留旧版本号的【字符串字面量】硬编码。

    只匹配引号包裹的版本号 (如 VERSION = "0.1.24"), 而不是注释或文档字符串里
    作为示例出现的版本号 —— 后者是正常写法, 报出来只会让人习惯性忽略告警。
    跳过 version_info.py 的内置回退值。
    """
    pattern = re.compile(r"""['"]v?""" + re.escape(old_version) + r"""['"]""")
    findings = []
    for root, _, files in os.walk(SRC_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(root, fn)
            if fn == "version_info.py":
                continue  # 回退值属正常
            with open(fp, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if not pattern.search(line):
                        continue
                    if "_compare_versions" in line or ("版本" in line and "test" in fn):
                        continue
                    findings.append((os.path.relpath(fp, BASE), i, line.strip()))
    return findings


def main():
    args = sys.argv[1:]
    current = read_version() or ""
    tail_lines = read_manifest_lines()

    if "--show" in args or not args:
        print("当前版本: {}".format(current or "(未设置)"))
        print("下载直链: {}".format(read_download_url() or "(未设置, 由 updater 自动推导)"))
        print("VERSION 附加行: {}".format(len(tail_lines)))
        print("用法见文件头注释")
        return 0

    if "--verify" in args:
        findings = verify(current)
        if findings:
            print("发现 {} 处旧版本号残留:".format(len(findings)))
            for fp, ln, txt in findings:
                print("  {}:{}  {}".format(fp, ln, txt))
            return 1
        print("[OK] 未发现旧版本号残留 (全部模块已从 version_info 读取)")
        return 0

    # ── 计算新版本号 ──
    new_version = None
    bump_part = None
    for flag, part in (("--patch", "patch"), ("--minor", "minor"), ("--major", "major")):
        if flag in args:
            bump_part = part
            if not current:
                print("[ERROR] 当前 VERSION 为空，无法 {} 递增。请先用位置参数指定版本号。"
                      .format(flag), file=sys.stderr)
                return 1
            new_version = bump(current, part)
            if new_version is None:
                print("[ERROR] 当前版本号格式无法识别: '{}'".format(current), file=sys.stderr)
                return 1
            break

    if new_version is None:
        # 位置参数作为指定版本号 (支持后缀)
        for a in args:
            if _VERSION_RE.match(a):
                new_version = a
                break

    if new_version is None:
        print("[ERROR] 无效参数。用法见文件头注释", file=sys.stderr)
        return 1

    # 防呆: 目标版本与当前相同
    if new_version == current:
        print("[SKIP] 目标版本 {} 与当前相同，无需更新".format(new_version))
        return 0

    write_version(current, new_version, tail_lines)
    print("  版本: {} → {}{}".format(
        current or "(无)", new_version,
        "  [{}递增]".format(bump_part) if bump_part else ""))

    changed = sync_readme(current, new_version)
    if changed:
        print("[OK] 已同步: {}".format(", ".join(changed)))
    else:
        print("[SKIP] README 未发现需同步的版本标记")

    # 自动验证
    if current:
        findings = verify(current)
        if findings:
            print("警告: 以下位置可能残留旧版本号，请人工检查:")
            for fp, ln, txt in findings:
                print("  {}:{}  {}".format(fp, ln, txt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
