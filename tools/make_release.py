"""ACRPA 发版助手 —— 生成校验过、可被自更新直接消费的发布包。

用法 (项目根目录运行):
    python tools/make_release.py                     # 用 dist/ACRPA.exe 打包
    python tools/make_release.py --exe "dist/ACRPA v0.1.26.exe"
    python tools/make_release.py --write-sha256      # 同时把 sha256 回填进 VERSION
    python tools/make_release.py --no-zip            # 只回填 sha256 + 生成 Release 说明
    python tools/make_release.py --no-zip --purge-cdn main   # 清 jsDelivr 分支别名缓存

它解决三个此前只能手工处理、且容易出错的问题:

1. 包内缺 VERSION —— 手工压缩容易漏掉, 导致用户端更新检查拿不到包内版本号。
   本工具强制把 VERSION 放进包根 (与 exe 同级)。
2. 中文文件名在 zip 里变成乱码 —— 此前用资源管理器/旧版 WinRAR 打的包, 模板文件名
   存的是 GBK 字节, 解压后 `使用说明.txt` 会变成 `╩╣╙├╦╡├≈.txt`。
   本工具用 Python zipfile 写入, 非 ASCII 名字自动带 UTF-8 标志位。
3. 校验和无人计算 —— 更新时无法判断拿到的包是否完整。本工具计算 sha256 并可直接
   回填到 VERSION 第三行, 供客户端强制校验。

发布包目录结构 (与既有发行版保持一致):

    ACRPA/
      ACRPA v0.1.26.exe
      VERSION
      README.md
      使用说明.txt
      template/*.xls
"""
import argparse
import hashlib
import os
import re
import sys
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(BASE, "VERSION")
DIST = os.path.join(BASE, "dist")
RELEASES_DIR = os.path.join(BASE, "docs", "releases")

# 发布包内文件名与源路径 (config.json 有意排除: 其中可能含作者本机配置)
PACKAGE_ITEMS = [
    ("README.md", "README.md"),
    ("使用说明.txt", "使用说明.txt"),
    ("VERSION", "VERSION"),
]
PACKAGE_DIRS = [
    ("template", "template"),
    ("res", "res"),
]


def read_version_lines():
    """VERSION 有效行 (注释与空行剔除)。"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f
                    if l.strip() and not l.strip().startswith("#")]
    except Exception:
        return []


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def find_exe(explicit=None):
    """定位待打包的 EXE。"""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    if not os.path.isdir(DIST):
        return None
    cands = [os.path.join(DIST, f) for f in os.listdir(DIST)
             if f.lower().endswith(".exe") and "acrpa" in f.lower()]
    if not cands:
        return None
    cands.sort(key=lambda p: (os.path.getmtime(p), os.path.getsize(p)), reverse=True)
    return cands[0]


def package_version(zip_path):
    """读发布包内嵌 VERSION 的首行; 读不到返回空串 (历史发布包可能确实没有)。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.endswith("/"):
                    continue
                if name.upper().split("/")[-1] == "VERSION":
                    text = z.read(name).decode("utf-8", "replace").strip()
                    return (text.splitlines() or [""])[0].strip()
    except Exception:
        return ""
    return ""


def package_version_text(lines):
    """包内 VERSION 的正文 —— 只保留版本号与下载直链, 剔除 sha256 行。

    sha256 描述的是「装有这份 VERSION 的那个 zip」本身。把它写进被校验的产物里
    必然自相矛盾: 写进去之后哈希就变了, 包内那一行永远是错的。客户端解析清单时
    也只取首行作版本、取 URL 行作直链, 该行在包内纯属误导, 故打包时剔除。
    """
    kept = [l for l in lines[1:] if not l.lower().startswith("sha256")]
    return "\n".join([lines[0]] + kept) + "\n"


def build_zip(version, exe_path, out_zip, version_lines=None):
    """按发行版目录结构打包; 非 ASCII 名自动带 UTF-8 标志位。"""
    exe_name = "ACRPA v{}.exe".format(version)
    written = []
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("ACRPA/", b"")
        z.write(exe_path, "ACRPA/" + exe_name)
        written.append(exe_name)
        for src_rel, dst_rel in PACKAGE_ITEMS:
            src = os.path.join(BASE, src_rel)
            if src_rel == "VERSION" and version_lines:
                z.writestr("ACRPA/" + dst_rel, package_version_text(version_lines))
                written.append(dst_rel)
                continue
            if os.path.exists(src):
                z.write(src, "ACRPA/" + dst_rel)
                written.append(dst_rel)
        for src_rel, dst_rel in PACKAGE_DIRS:
            src = os.path.join(BASE, src_rel)
            if not os.path.isdir(src):
                continue
            z.writestr("ACRPA/{}/".format(dst_rel), b"")
            for name in sorted(os.listdir(src)):
                fp = os.path.join(src, name)
                if os.path.isfile(fp):
                    z.write(fp, "ACRPA/{}/{}".format(dst_rel, name))
                    written.append("{}/{}".format(dst_rel, name))
    return written


def write_sha256_to_version(version, lines, digest):
    """把 sha256 写入 VERSION (存在则替换, 不存在则追加)。"""
    out = [version]
    replaced = False
    for line in lines[1:]:
        if line.lower().startswith("sha256"):
            out.append("sha256:{}".format(digest))
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append("sha256:{}".format(digest))
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return replaced


def read_changelog_entry(version):
    """从 README 更新日志中取本版本条目 (作为 Release 正文首段)。"""
    path = os.path.join(BASE, "README.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return ""
    m = re.search(r"(?m)^-\s+v{}[^\n]*".format(re.escape(version)), text)
    return m.group(0).lstrip("- ").strip() if m else ""


def manifest_tag(lines, version):
    """从 VERSION 的直链行取 Release tag; 取不到则回退 v<版本号>。

    必要性 (实测本仓库): release 不可变, 一个 tag 一旦被某个已发布的 Release 用过就
    不能再用于新 Release —— 即便那个 Release 已被删除, 重新发布会报
    "tag_name was used by an immutable release"。因此 tag 未必等于 v<版本号>, 历史线上
    本来就有 v0.1.25.0 这种四段写法。客户端读的是 VERSION 里的直链, 所以以直链为准,
    才能保证「声明的直链」与「实际 tag」始终一致。
    """
    for line in lines[1:]:
        m = re.search(r"/releases/download/([^/]+)/", line)
        if m:
            return m.group(1)
    return "v{}".format(version)


def write_release_notes(version, zip_name, digest, size, tag=None):
    """生成 docs/releases/vX.Y.Z.md —— 可直接粘贴为 GitHub Release 正文。"""
    os.makedirs(RELEASES_DIR, exist_ok=True)
    entry = read_changelog_entry(version)
    if not tag:
        tag = "v{}".format(version)
    body = []
    body.append("# ACRPA {}\n".format(tag))
    if entry:
        body.append("## 本版更新\n")
        body.append(entry + "\n")
    body.append("## 下载\n")
    body.append("- 直链: `https://github.com/yohoten/acrpa/releases/download/{}/{}`".format(
        tag, zip_name))
    body.append("- 文件: `{}` ({:.1f} MB)".format(zip_name, size / (1024 * 1024)))
    body.append("- SHA-256: `{}`\n".format(digest))
    body.append("## 安装\n")
    body.append("解压后直接运行 `ACRPA/ACRPA v{}.exe`（便携版，无需安装）。".format(version))
    body.append("已装旧版的用户：启动后状态栏会提示新版本，点击即可在应用内下载并重启更新，")
    body.append("**配置、脚本、模板、日志均不受影响**。\n")
    body.append("## 校验\n")
    body.append("```")
    body.append("# Windows")
    body.append("certutil -hashfile {} SHA256".format(zip_name))
    body.append("```")
    if tag != "v{}".format(version):
        body.append("\n> Release tag: `{}`（本仓库 release 不可变，`v{}` 曾被一个已发布的 "
                    "Release 占用，不可复用；客户端两种写法都会尝试）。".format(
                        tag, version))

    # 文件名按【版本号】取, 不按 tag —— 便于跨 tag 写法稳定引用; 正文里的直链才用 tag。
    path = os.path.join(RELEASES_DIR, "v{}.md".format(version))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    return path


def purge_cdn(branch, paths, repo="yohoten/acrpa"):
    """清 jsDelivr 对分支别名 (如 @main) 的缓存。

    必要性 (实测): jsDelivr 会缓存分支别名下的文件内容。推送新 VERSION 之后实测
    仍持续返回旧版本号, 而客户端的第一梯队回退源正是 jsDelivr。缓存不清,
    只能走 jsDelivr 的网络下, 更新检查会被这份"落后的清单"告知"已是最新"。
    (按提交哈希访问的 URL 不受影响, 但客户端无从预知哈希, 故必须清分支别名。)
    """
    import urllib.error
    import urllib.request

    all_ok = True
    for p in paths:
        url = "https://purge.jsdelivr.net/gh/{repo}@{ref}/{path}".format(
            repo=repo, ref=branch, path=p)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            if '"status"' in body and '"finished"' not in body:
                print("[WARN] CDN 缓存清理未确认完成: {}@{}".format(branch, p))
                all_ok = False
            else:
                print("[OK] CDN 缓存已清: @{} / {}".format(branch, p))
        except Exception as e:
            all_ok = False
            print("[WARN] CDN 缓存清理失败 (@{} / {}): {}".format(branch, p, e))
    return all_ok


def main():
    ap = argparse.ArgumentParser(description="ACRPA 发版助手")
    ap.add_argument("--exe", help="待打包的 EXE 路径 (缺省自动选取 dist/ 下最新)")
    ap.add_argument("--out", help="输出 zip 路径 (缺省 dist/ACRPA.zip)")
    ap.add_argument("--write-sha256", action="store_true",
                    help="把 sha256 回填到 VERSION 第三行 (客户端将强制校验)")
    ap.add_argument("--no-zip", action="store_true", help="跳过打包, 只做校验和与 Release 说明")
    ap.add_argument("--purge-cdn", metavar="BRANCH",
                    help="清 jsDelivr 对分支别名的缓存 (如 --purge-cdn main), 发版后必须执行")
    args = ap.parse_args()

    lines = read_version_lines()
    if not lines:
        print("[FAIL] 读不到 VERSION, 请先运行 tools/bump_version.py", file=sys.stderr)
        return 1
    version = lines[0]
    print("版本号: {}".format(version))

    out_zip = args.out or os.path.join(DIST, "ACRPA.zip")
    if not args.no_zip:
        exe = find_exe(args.exe)
        if not exe:
            print("[FAIL] 找不到待打包的 EXE。请先 `python build.py --clean`, "
                  "或用 --exe 指定路径", file=sys.stderr)
            return 1
        print("打包源: {} ({:.1f} MB)".format(
            os.path.relpath(exe, BASE), os.path.getsize(exe) / (1024 * 1024)))

        os.makedirs(os.path.dirname(out_zip), exist_ok=True)
        written = build_zip(version, exe, out_zip, lines)
        print("[OK] 已生成 {} ({:.1f} MB, {} 项)".format(
            os.path.relpath(out_zip, BASE),
            os.path.getsize(out_zip) / (1024 * 1024), len(written)))
        for name in written[:6]:
            print("     - ACRPA/{}".format(name))
        if len(written) > 6:
            print("     - …共 {} 项".format(len(written)))
    else:
        out_zip = out_zip if os.path.exists(out_zip) else None

    digest, size = "", 0
    if out_zip and os.path.exists(out_zip):
        # 关键防线: --no-zip 会沿用已存在的 dist/ACRPA.zip。如果那份还是上一次的
        # 旧包 (甚至没有内嵌 VERSION), 直接给它算校验和并写成"本版本的 Release
        # 说明", 就会发布一个指向旧包的错误校验和 —— 用户拿到手也永远对不上。
        # 包内版本对不上, 一律拒绝生成校验和与说明。
        pkg_ver = package_version(out_zip)
        if pkg_ver != version:
            print("[WARN] {} 的包内版本为 {!r}, 与 VERSION 的 {} 不一致。".format(
                os.path.relpath(out_zip, BASE), pkg_ver or "缺失", version),
                file=sys.stderr)
            print("       已拒绝用它生成校验和与 Release 说明 (否则会发布指向旧包的错误值)。",
                  file=sys.stderr)
            print("       请重新打包: python build.py --clean && python tools/make_release.py",
                  file=sys.stderr)
            out_zip = None

    if out_zip and os.path.exists(out_zip):
        digest = sha256_of(out_zip)
        size = os.path.getsize(out_zip)
        print("SHA-256: {}".format(digest))
        sidecar = out_zip + ".sha256"
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write("{}  {}\n".format(digest, os.path.basename(out_zip)))
        print("[OK] 校验和已写入 {}".format(os.path.relpath(sidecar, BASE)))

        if args.write_sha256:
            replaced = write_sha256_to_version(version, lines, digest)
            print("[OK] VERSION 已{} sha256 行".format("更新" if replaced else "追加"))

        notes = write_release_notes(version, os.path.basename(out_zip), digest, size,
                                    tag=manifest_tag(lines, version))
        print("[OK] Release 说明: {}".format(os.path.relpath(notes, BASE)))
    else:
        print("[WARN] 未找到发布包, 跳过校验和与 Release 说明")

    if args.purge_cdn:
        print()
        purge_cdn(args.purge_cdn, ["VERSION", "dist/ACRPA.zip"])

    tag = manifest_tag(lines, version)
    zip_name = os.path.basename(out_zip) if out_zip else "dist/ACRPA.zip"
    print("\n下一步:")
    print("  A) 发布 Release 并上传 {} —— 一条命令搞定:".format(zip_name))
    print("     python tools/publish_release.py --exe")
    print("     tag 为 {} (取自 VERSION 直链; 改动 VERSION 直链即改 tag)".format(tag))
    print("     正文自动取 docs/releases/v{}.md".format(version))
    print("     客户端优先走 Releases API, 且直链/体积取自 API, 最省事也最不易出错。")
    print("     注意: 本仓库 release 不可变 —— 附件必须在【草稿】阶段上传, 已发布的")
    print("     Release 无法再改。工具已内置该流程; 若某个 tag 曾被已发布的 Release")
    print("     占用过, 则不可复用 (报 was used by an immutable release), 需换 tag 并")
    print("     同步改 VERSION 的直链。")
    print("  B) 镜像仓库 (Gitee) 若也要同步, 记得单独 push 一次并上传 dist/ACRPA.zip:")
    print("     gitee 的 raw 路径对 dist/ 下的大文件实测返回 403, 因此仅作最后兜底。")
    print("  C) 清 CDN 缓存 (否则只走 jsDelivr 的网络会读到旧版本号):")
    print("     python tools/make_release.py --no-zip --purge-cdn main")
    print("\n提醒: VERSION 与 dist/ACRPA.zip 必须同版本同一次提交, 否则校验会拦下旧包。")
    print("      下载环节会用包内 VERSION 复核, 镜像返回旧包时会自动换源而非装错版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
