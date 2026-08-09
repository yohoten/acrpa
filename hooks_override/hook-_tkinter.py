#-----------------------------------------------------------------------------
# 覆盖内置 hook-_tkinter.py: 排除 Tcl/Tk 时区数据 tzdata
#
# 内置 hook 会把整个 tcl/tk 数据目录打包 (含 _tcl_data/tzdata 609 个时区文件,
# 压缩后约 318 KB)。本项目仅使用标准 Tk 控件, 不需要 tzdata
# (tzdata 仅被 tkinter.tix 时钟部件使用), 故过滤掉以减小 EXE 体积。
#
# 实现与内置 hook 相同, 仅在 add_datas 前过滤 tzdata 条目。
#-----------------------------------------------------------------------------

from PyInstaller.utils.hooks.tcl_tk import tcltk_info


def hook(hook_api):
    # 与内置 hook 一致的缺失检查
    if tcltk_info.tcl_data_missing:
        raise SystemExit("ERROR: Tcl data/library directory ({!r}) could not be collected!".format(tcltk_info.tcl_data_dir))

    if tcltk_info.tk_data_missing:
        raise SystemExit("ERROR: Tk data/library directory ({!r}) could not be collected!".format(tcltk_info.tk_data_dir))

    # 过滤 tzdata 时区数据 (dest 形如 _tcl_data/tzdata/...)
    files = []
    for entry in tcltk_info.data_files:
        dest = entry[0].replace("\\", "/").lower()
        if "/tzdata/" in dest or dest.endswith("/tzdata"):
            continue
        files.append(entry)

    hook_api.add_datas(files)
