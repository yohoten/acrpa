# -*- coding: utf-8 -*-
"""PaddleOCR.dll 自检 / 原型扫描（源码环境下运行）

用法:
    python tools/_check_paddle_dll.py                 # 全量自检 + 原型扫描
    python tools/_check_paddle_dll.py --image a.png   # 额外做一次真实识别
    python tools/_check_paddle_dll.py --no-sweep      # 只做静态检查与加载
    python tools/_check_paddle_dll.py --proto dir1,file1   # 只测指定组合

作用:
    1. 打印 DLL / 4 个原生依赖 / 模型目录的现状（缺什么一目了然）；
    2. 依赖齐全时，在 **子进程** 中逐个尝试「初始化原型 × 识别原型」组合，
       命中组合即为该 DLL 的真实调用契约（一次原生崩溃只影响子进程）；
    3. 给出可直接写入 config.json 的键值。

原型表见 src/paddle_dll.py 的 PROTO_INIT / PROTO_DETECT。
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import paddle_dll  # noqa: E402


def _print(msg=""):
    sys.stdout.write(str(msg) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="PaddleOCR.dll self-check")
    ap.add_argument("--image", default="", help="用于真实识别测试的图片")
    ap.add_argument("--no-sweep", action="store_true", help="跳过原型扫描")
    ap.add_argument("--proto", default="", help="只测指定组合: init_proto,detect_proto")
    ap.add_argument("--dll-dir", default="", help="含 PaddleOCR.dll 的目录(如绿色版根目录)")
    ap.add_argument("--model-dir", default="", help="模型目录(如绿色版 inference/)")
    ap.add_argument("--det-dir", default="", help="检测模型目录(dirs4 原型用)")
    ap.add_argument("--rec-dir", default="", help="识别模型目录(dirs4 原型用)")
    ap.add_argument("--cls-dir", default="", help="方向分类模型目录(dirs4 原型用)")
    ap.add_argument("--keys", default="", help="字典文件(ppocr_keys.txt)")
    args = ap.parse_args()

    # 覆盖（仅在本次进程内生效，不写 config.json）
    if args.dll_dir:
        import state
        state.PADDLE_DLL_DIR = args.dll_dir
    if args.model_dir:
        import state
        state.PADDLE_DLL_MODEL_DIR = args.model_dir

    opts = {}
    if args.dll_dir:
        opts["dll_dir"] = args.dll_dir
    if args.model_dir:
        opts["model_dir"] = args.model_dir
    layout = {}
    for _k in ("det", "rec", "cls"):
        _v = getattr(args, _k + "_dir")
        if _v:
            layout[_k] = _v
    if args.keys:
        layout["keys"] = args.keys
    if layout:
        opts["layout"] = layout

    diag = paddle_dll.diagnose()
    _print(paddle_dll.format_diagnosis(diag))
    _print()
    _print("候选目录:")
    for d in diag["candidate_dirs"]:
        _print("  [{}] {}".format("有" if os.path.isfile(os.path.join(d, paddle_dll.DLL_NAME)) else "无", d))
    _print("原型表: Initialize={}".format(", ".join(sorted(paddle_dll.PROTO_INIT))))
    _print("        Detect    ={}".format(", ".join(sorted(paddle_dll.PROTO_DETECT))))

    if not diag["dll_found"]:
        _print("\n结论: 未找到 PaddleOCR.dll，无法调用。")
        return 2

    if diag["missing_deps"]:
        _print("\n缺失原生依赖（必须与 PaddleOCR.dll 放在同一目录）:")
        for d in diag["missing_deps"]:
            _print("  - {}".format(d))
        _print("\n这些文件来自该 DLL 的发行包（PaddleOCRSharp 原生运行时 / CC 运行库）：")
        _print("  paddle_inference.dll（推理引擎，最大）、tbb12.dll（线程库）、")
        _print("  yaml-cpp.dll（模型配置解析）、opencv_world470.dll（图像处理）")
        _print("\n结论: 依赖不全 → 现在调用必然失败（ctypes 报 module not found）。")
        _print("      补齐后再跑本脚本即可拿到可用调用组合。")
        return 2

    if not diag["model_ok"]:
        _print("\n模型目录不可用: {}".format(diag["model_msg"]))
        _print("需要目录内包含 inference.json + inference.pdiparams（PP-OCR 检测/识别模型），")
        _print("或使用「模型集合目录」（子目录各自带 inference.json，如绿色版 inference/）")
        return 2

    if args.model_dir:
        lay = paddle_dll._detect_layout(args.model_dir)
        _print("\n模型布局推断 (model_dir={}):".format(args.model_dir))
        for k in ("det", "rec", "cls", "keys"):
            _print("  {} = {}".format(k, lay.get(k) or "<未找到>"))
        if layout:
            lay.update(layout)
            _print("  (已按命令行参数覆盖)")
        opts["layout"] = lay

    _print("\n开始原型扫描（每个组合都在独立子进程中执行）...")
    combos = []
    if args.proto:
        parts = [p.strip() for p in args.proto.split(",")]
        if len(parts) == 2:
            combos = [(parts[0], parts[1])]
        elif len(parts) == 1 and parts[0]:
            # 只给 init 原型 → 自动扫全部 detect 原型
            combos = [(parts[0], pd) for pd in sorted(paddle_dll.PROTO_DETECT)]
    if combos:
        good = []
        for pi, pd in combos:
            ok, payload = paddle_dll.run_child(pi, pd, args.image or None, **opts)
            if ok:
                good.append((pi, pd, len(payload.get("items") or [])))
                _print("  可用: init={} detect={}".format(pi, pd))
                _print("      结果: items={} error={}".format(
                    len(payload.get("items") or []), payload.get("error") or ""))
                if payload.get("text"):
                    _print("      识别文本: {}".format(payload["text"][:200]))
                elif payload.get("raw"):
                    _print("      原始返回: {}".format(payload["raw"][:200]))
            else:
                err = payload.get("error") if isinstance(payload, dict) else payload
                _print("  不可用: init={} detect={} → {}".format(pi, pd, err))
    else:
        if args.no_sweep:
            _print("\n[--no-sweep] 跳过原型扫描")
            return 0
        good = paddle_dll.sweep(args.image or None, quiet=False, **opts)

    _print()
    if not good:
        _print("结论: 未能找到可用调用组合。多半是模型与引擎版本不匹配")
        _print("      （注意 DLL 内提示: .pdmodel 自 v6.1 起不再支持，需新版 IR）。")
        return 3

    # 优先推荐默认原型（ptr_byte：与路径编码无关），否则取结果最多者
    best = None
    for g in good:
        if g[1] == paddle_dll.DEFAULT_PROTO_DETECT:
            best = g
            break
    if best is None:
        best = max(good, key=lambda x: x[2])
    _print("结论: 可用组合 {} 个，推荐 init={} detect={}".format(
        len(good), best[0], best[1]))
    _print("写入 config.json 后即可在设置中启用:")
    _print(json.dumps({
        "paddle_dll_enabled": True,
        "paddle_dll_proto_init": best[0],
        "paddle_dll_proto_detect": best[1],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
