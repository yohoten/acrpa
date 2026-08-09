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

说明:
    - 支持预发布后缀: 0.1.23beta / 0.1.23-beta / 0.1.23rc1
    - --patch/--minor/--major 递增数值部分并【保留后缀】: 0.1.23beta → 0.1.24beta
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
    """读取下载地址 (第二行)。"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            return lines[1] if len(lines) > 1 else ""
    except Exception:
        return ""


def write_version(old_version, new_version, download_url):
    """写入新版本号到 VERSION 文件 (保留第二行下载地址)。"""
    content = "{}\n".format(new_version)
    if download_url:
        content += "{}\n".format(download_url)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print("[OK] VERSION 已更新: {} → {}".format(old_version or "(无)", new_version))


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
    """扫描 src/ 下是否残留旧版本号硬编码 (跳过 version_info.py 回退值和测试)。"""
    pattern = re.compile(re.escape(old_version))
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
                    if pattern.search(line):
                        # 跳过测试/注释中的版本比较
                        if "_compare_versions" in line or ("版本" in line and "test" in fn):
                            continue
                        findings.append((os.path.relpath(fp, BASE), i, line.strip()))
    return findings


def main():
    args = sys.argv[1:]
    current = read_version() or ""
    download_url = read_download_url()

    if "--show" in args or not args:
        print("当前版本: {}".format(current or "(未设置)"))
        print("下载地址: {}".format(download_url or "(未设置)"))
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

    write_version(current, new_version, download_url)
    print("  版本: {} → {}{}".format(
        current or "(无)", new_version,
        "  [{}递增]".format(bump_part) if bump_part else ""))

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
