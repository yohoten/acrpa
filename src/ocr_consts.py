# -*- coding: utf-8 -*-
"""ocr_consts — PaddleOCR 运行时文件清单的**单一定义源** (S1-03)。

为什么单独一个模块
------------------
``PaddleOCR.dll`` 能否真正跑起来, 取决于三类文件是否齐全:

    ① 主模块      PaddleOCR.dll
    ② 同目录依赖  paddle_inference.dll / tbb12.dll / yaml-cpp.dll / opencv_world470.dll
    ③ 模型文件    inference.json + inference.pdiparams (旧版 model.pdmodel / .pdiparams / .yml)

这份清单以前在 ``paddle_dll.py`` 与 ``capabilities.py`` **各写一份**, 靠注释和
一个 ast 测试"自觉同步" —— 任一处改动而漏改另一处, 就会出现
"探测说 READY, 一调用就 FileNotFoundError" 的假就绪。现在下沉到这里:

    src/ocr_consts.py  ──(import)──>  src/paddle_dll.py
                       └─(import)──>  src/capabilities.py

两处 **import 的是同一批对象**, 因此 ``paddle_dll.DEP_DLLS is
capabilities.PADDLE_DEP_DLLS`` 恒为 True —— 不是"逐项相等", 而是同一个 tuple,
结构上不可能再漂移。锁定测试:

    tools/_test_ocr_selfcheck.py  (O1 三处同源 / O2 唯一字面量清单)
    tools/_test_native_check.py   (N8 三处同源)

设计约束 (务必保持)
------------------
* **纯 stdlib**: 本模块**没有任何 import** —— 只有字面量;
* **import 零副作用**: 不读文件 / 不建对象 / 不 import ``utils`` / ``state`` /
  ``tkinter``。它被 ``capabilities`` 在**模块级** import, 必须保证探测链纯净
  (``tools/_test_import_side_effect_free.py`` 的硬约束);
* **只放清单, 不放逻辑**: 目录解析 / 就绪判定留在 ``paddle_dll`` / ``capabilities``。
"""

#: 主模块文件名
DLL_NAME = "PaddleOCR.dll"

#: 运行期必需的同目录原生依赖 (缺任一 → LoadLibrary 直接失败, 且报错不带文件名)
DEP_DLLS = ("paddle_inference.dll", "tbb12.dll", "yaml-cpp.dll",
            "opencv_world470.dll")

#: 模型目录内的必备文件 (新版 Paddle Inference IR)
MODEL_FILES = ("inference.json", "inference.pdiparams")

#: 兼容标记 (旧版 .pdmodel / yml 也能被 v6.1 之前版本识别)
MODEL_FILES_LEGACY = ("model.pdmodel", "model.pdiparams", "inference.yml")

__all__ = ["DLL_NAME", "DEP_DLLS", "MODEL_FILES", "MODEL_FILES_LEGACY"]
