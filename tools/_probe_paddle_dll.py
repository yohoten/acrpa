# -*- coding: utf-8 -*-
"""PaddleOCR.dll 调用契约探针（换版本 / 换发行包时重新校准时使用）

当前 v6.2.0 的 ABI 已实测确认（见 docs/paddle-ocr-dll-使用说明.md 与
tools/_dotnet_api.txt）：

    UInt64 Initializejson(det_infer, cls_infer, rec_infer, keys, parameterjson)
    IntPtr Detect       (enginePtr, imagefile)
    IntPtr DetectByte   (enginePtr, data, Int64 size)
    IntPtr DetectBase64 (enginePtr, imagebase64)

本脚本把上述调用在**独立子进程**中逐个试跑（猜错原型导致的 Access Violation
只杀子进程），用于确认新版本 DLL 是否仍是同一套契约。

用法:
    python -X utf8 tools/_probe_paddle_dll.py --dll-dir D:\\paddleocr_runtime \
        --model-dir D:\\paddleocr_runtime\\inference --image img\\image2.png
    python -X utf8 tools/_probe_paddle_dll.py ... --one 1     # 只跑第 1 个候选
    python -X utf8 tools/_probe_paddle_dll.py --list          # 只打印候选清单
"""
import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import paddle_dll  # noqa: E402

#: (导出名, 参数列表)。{ptr} = Initializejson 返回的引擎句柄
CANDIDATES = [
    ("Detect",       ["{ptr}", "{path}"]),
    ("DetectBase64", ["{ptr}", "{b64}"]),
    ("DetectByte",   ["{ptr}", "{data}", "{size}"]),
    # 旧版可能的形态（保留探测能力）
    ("Detect",       ["{path}"]),
    ("DetectBase64", ["{b64}"]),
]


def _expand(token, eng, image_path):
    k = (token or "").strip().lower()
    if k == "{path}":
        return os.path.abspath(image_path)
    if k == "{cfgfile}":
        for cand in ("PaddleOCR.config.json", "PaddleOCRStructure.config.json"):
            p = os.path.join(eng.model_dir or "", cand)
            if os.path.isfile(p):
                return p
        return ""
    if k == "{cfgjson}":
        return paddle_dll.build_config_json(eng.model_dir)
    if k == "{det}":
        return eng.layout.get("det") or ""
    if k == "{rec}":
        return eng.layout.get("rec") or ""
    if k == "{cls}":
        return eng.layout.get("cls") or ""
    if k == "{keys}":
        return eng.layout.get("keys") or ""
    if k == "{model}":
        return eng.model_dir or ""
    return token


def _build_args(tokens, eng, image_path, data_bytes):
    out = []
    for t in tokens:
        k = (t or "").strip().lower()
        if k == "{ptr}":
            out.append(ctypes.c_uint64(eng._engine_ptr))
        elif k == "{data}":
            out.append(data_bytes + b"\x00")
        elif k == "{b64}":
            out.append(base64.b64encode(data_bytes) + b"\x00")
        elif k == "{size}":
            out.append(ctypes.c_int64(len(data_bytes)))   # .NET 声明是 Int64
        else:
            out.append(_expand(t, eng, image_path).encode("utf-8"))
    return out


def run_one(idx, dll_dir, model_dir, image):
    export, tokens = CANDIDATES[idx]
    eng = paddle_dll.PaddleDllEngine(dll_dir=dll_dir, model_dir=model_dir)
    res = {"idx": idx, "export": export, "tokens": tokens, "init": False,
           "engine_ptr": 0, "error": "", "raw": "", "text": "", "items": 0}
    if not eng.load():
        res["error"] = "load: " + eng.error
        return res
    if not eng.initialize():
        res["error"] = "init: " + eng.error
        return res
    res["init"] = True
    res["engine_ptr"] = eng._engine_ptr

    with open(image, "rb") as fh:
        data_bytes = fh.read()
    args = _build_args(tokens, eng, image, data_bytes)
    fn = getattr(eng._lib, export)
    try:
        fn.restype = ctypes.c_void_p
        ptr = fn(*args)
    except Exception as e:
        res["error"] = "call: {}".format(e)
        return res
    if not ptr:
        res["error"] = "空指针; GetError=" + (eng.get_error() or "")
        return res
    raw = paddle_dll._read_cptr(ptr)
    res["raw"] = raw[:1500]
    items = paddle_dll.parse_result(raw)
    res["items"] = len(items)
    res["text"] = "\n".join(i["text"] for i in items)[:300]
    if not items:
        res["error"] = "GetError=" + (eng.get_error() or "")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll-dir", default=r"D:\paddleocr_runtime")
    ap.add_argument("--model-dir", default=r"D:\paddleocr_runtime\inference")
    ap.add_argument("--image", default=os.path.join(_ROOT, "img", "image1.png"))
    ap.add_argument("--one", type=int, default=-1)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or args.one >= 0:
        for i, (e, t) in enumerate(CANDIDATES):
            print("[{:2d}] {} {}".format(i, e, t))
        if args.list:
            return 0

    if args.one >= 0:
        r = run_one(args.one, args.dll_dir, args.model_dir, args.image)
        print("@@" + json.dumps(r, ensure_ascii=False))
        return 0 if r["items"] else 1

    hits = []
    for i in range(len(CANDIDATES)):
        cmd = [sys.executable, "-X", "utf8", os.path.abspath(__file__),
               "--dll-dir", args.dll_dir, "--model-dir", args.model_dir,
               "--image", args.image, "--one", str(i)]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=180)
            out = (proc.stdout or b"").decode("utf-8", "replace")
        except Exception as e:
            print("[{:2d}] {} {} → 超时/异常 {}".format(
                i, CANDIDATES[i][0], CANDIDATES[i][1], e))
            continue
        idx = out.find("@@")
        if idx < 0:
            print("[{:2d}] {} {} → 子进程崩溃(exit={}) —— 该原型错误".format(
                i, CANDIDATES[i][0], CANDIDATES[i][1], proc.returncode))
            continue
        r = json.loads(out[idx + 2:])
        print("[{:2d}] {} {} → {} | items={} text={} err={}".format(
            i, r["export"], r["tokens"], "命中" if r["items"] else "无结果",
            r["items"], (r["text"] or "").replace("\n", " / ")[:80],
            (r["error"] or "")[:110]))
        if r["items"]:
            hits.append(r)

    print("\n命中候选: {}".format([h["idx"] for h in hits] or "无"))
    if hits:
        print("建议: proto_init/json5 + proto_detect/{}".format(
            CANDIDATES[hits[0]["idx"]][1] == ["{ptr}", "{path}"] and "ptr_file"
            or "见候选表"))
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
