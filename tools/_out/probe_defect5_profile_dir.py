# -*- coding: utf-8 -*-
"""缺陷5探针: _default_profile_dir() 把 CONFIG_PATH(config.json 文件) 当目录拼接。"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import state
import browser_backend

print("state.CONFIG_PATH          = {!r}".format(state.CONFIG_PATH))
print("state.CONFIG_PATH is file  = {}".format(os.path.isfile(state.CONFIG_PATH)))

p = browser_backend._default_profile_dir()
print("_default_profile_dir()     = {!r}".format(p))
print("dirname(p) 存在?            = {}".format(os.path.isdir(os.path.dirname(p))))
print("dirname(p) 是文件?          = {}".format(os.path.isfile(os.path.dirname(p))))

print("=== 模拟 browser_backend.py:272  os.makedirs(user_data_dir, exist_ok=True)")
try:
    os.makedirs(p, exist_ok=True)
    print("makedirs 成功 (缺陷不成立)")
except Exception as e:
    print("makedirs 抛出异常类型 = {}.{}".format(type(e).__module__, type(e).__name__))
    print("异常 repr            = {!r}".format(e))
    print("异常 str             = {}".format(str(e)))
    print("--- traceback ---")
    traceback.print_exc()

print("=== 对照: 正确口径 dirname(CONFIG_PATH)/browser_profile")
alt = os.path.join(os.path.dirname(state.CONFIG_PATH), "browser_profile")
print("alt                        = {!r}".format(alt))
try:
    os.makedirs(alt, exist_ok=True)
    print("alt makedirs 成功")
except Exception as e:
    print("alt makedirs 异常 = {!r}".format(e))
