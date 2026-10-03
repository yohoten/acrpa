# PaddleOCR.dll 原生后端使用说明（已实测可用）

> 本文说明 ACRPA 如何调用 `lib/paddle_ocr/PaddleOCR.dll`。**调用链已打通并实测识别准确**，
> 但运行时（约 300MB）按当前约定放在**外部目录**，需在设置里填路径后启用。

## 1. 现状结论

| 项目 | 状态 |
|---|---|
| `lib/paddle_ocr/PaddleOCR.dll` | 存在（903 KB，x64 原生 C 接口，免费社区版 v6.2.0） |
| 4 个原生依赖 | 不在仓库内（仓库不增大）；需外部运行时目录 |
| 已验证可用运行时 | `D:\paddleocr_runtime`（由绿色版 `PaddleOCR工具绿色版v6.2.0` 复制而来，含 DLL + 依赖 + `inference/` 模型） |
| ACRPA 侧封装 | [`src/paddle_dll.py`](../src/paddle_dll.py)（已完成，默认关闭） |
| 实测结果 | `Initializejson` + `DetectByte` 调用成功；识别文本与坐标均正确（含中文图片路径场景） |
| 启用状态 | **未启用**（`paddle_dll_enabled=false`，历史 OCR 行为完全不变） |

实测记录（源码环境，`img` 测试图）：

```
init True  err(空)  items 2
TEXT: ACRPA OCR TEST 12345 \n PaddleOCR v6.2.0 DLL
RAW : [{"BoxPoints":[{"X":16,"Y":24},...],"Score":0.970,"Text":"ACRPA OCR TEST 12345","cls_label":-1,"cls_score":0.0}, ...]
```

## 2. 真实调用契约（由 .NET 元数据实测确认）

`PaddleOCRSharp.dll` 的 `DllImport` 声明（反射导出，见 [`tools/_dotnet_api.txt`](../tools/_dotnet_api.txt)）：

```
UInt64 Initializejson(String det_infer, String cls_infer, String rec_infer, String keys, String parameterjson)
IntPtr Detect        (UInt64 enginePtr, String imagefile)
IntPtr DetectByte    (UInt64 enginePtr, Byte[] data, Int64 size)
IntPtr DetectBase64  (UInt64 enginePtr, String imagebase64)
IntPtr DetectMat     (UInt64 enginePtr, IntPtr cvmat)
IntPtr DetectByteData(UInt64 enginePtr, IntPtr imgPtr, Int32 w, Int32 h, Int32 nChannel)
Void   FreeEngine    (UInt64 enginePtr)
IntPtr GetError      ()
Void   libaddLicense (String lic)
```

要点（都已在 `src/paddle_dll.py` 中实现）：

1. **参数顺序是 det → cls → rec → keys**（`cls` 在 `rec` 之前，容易写错）。
2. `Initializejson` 返回 **引擎句柄**，`Detect` 系列必须带上它；句柄为 0 视为初始化失败。
3. 输出 JSON 是 **UTF-16LE**（`EnableANSIResult(0)`），按 `c_char_p` 读会被第一个 NUL 截断成 `"["`。
   [`_read_cptr`](../src/paddle_dll.py) 会自动判定宽字符。
4. 参数 JSON 由 `nlohmann::json` **严格类型校验**：布尔键给字符串会报
   `type must be boolean, but is string`（如 `det_db_score_mode` 必须是 `true/false`）。
   实现上优先直接采用绿色版自带的 `inference/PaddleOCR.config.json` 原文。
5. 返回结构：`[{"BoxPoints":[{"X":..,"Y":..},...],"Score":..,"Text":"..","cls_label":..,"cls_score":..}, ...]`
   （`BoxPoints` 是 `{"X","Y"}` 字典数组，[`_norm_bbox`](../src/paddle_dll.py) 已兼容）。
6. 必须 `EnableJsonResult(1)`，否则返回的是内部纯文本格式。
7. **图片路径也必须是纯 ASCII**：`Detect(enginePtr, path)` 内部用 OpenCV 按 ANSI 打开文件，
   路径含中文会报 `files or files may be damaged`（实测 `build\_pdll\中文测试图.png`）。
   因此默认识别原型用 **`ptr_byte`**（Python 先读字节再传入，实测中文路径下识别同样正确）；
   `ptr_file` 仅在纯英文路径下略快。
8. `DetectByte` 的 size 参数是 **Int64**（.NET 声明），用 32 位 int 传会被判成 0。

## 3. 依赖与目录要求

与 `PaddleOCR.dll` **同目录**需有：

```
PaddleOCR.dll          仓库内已有
paddle_inference.dll   94 MB  推理引擎（版本必须与该 DLL 同源）
opencv_world470.dll    64 MB  图像处理
mklml.dll              92 MB  MKL 数学库
mkldnn.dll             27 MB  oneDNN
tbb12.dll / yaml-cpp.dll / libiomp5md.dll / tbbmalloc*.dll
msvcp140*.dll / vcruntime140*.dll / concrt140.dll / vcomp140.dll（VC 运行库）
inference/             模型集合目录
  PP-OCRv5_mobile_det_infer/  (inference.json + inference.pdiparams + inference.yml)
  PP-OCRv5_mobile_rec_infer/
  PP-OCRv5_mobile_cls_infer/
  ppocr_keys.txt              字典
  PaddleOCR.config.json       官方参数（本实现直接采用）
```

### ⚠ 路径必须是纯 ASCII

该 DLL 内部按 **ANSI 路径**打开文件（模型文件与待识别图片都一样），路径含中文/全角字符会失败：

- ❌ 运行时目录 `F:\（8）Desktop\...\PaddleOCR工具绿色版v6.2.0` → 实测报
  `can not found model file in ...\PP-OCRv5_mobile_rec_infer`
- ✅ 运行时目录 `D:\paddleocr_runtime` → 实测初始化成功
- ❌ 图片路径 `build\_pdll\中文测试图.png`（`ptr_file` 原型）→ 实测报 `files or files may be damaged`
- ✅ 同一张中文路径图片改用默认 `ptr_byte` → 实测识别正确
  （ACRPA 的 `screenshots` 目录即便位于中文路径也不会受影响）

`diagnose()` / [`tools/_check_paddle_dll.py`](../tools/_check_paddle_dll.py) 会显式提示这一点。

### 免费社区版限制

- 不支持 GPU（`use_gpu=true` 会被拒）
- **单张图检测框必须 < 100**，超了报 `The free community edition only support box sizes <100`
  （实测 `img/image1.png` 这类整屏截图会触发；应缩小 `识别文字` 的区域参数）
- `.pdmodel` 旧格式自 v6.1 起不再支持，需 `inference.json`+`inference.pdiparams`

## 4. 启用步骤

1. 准备运行时目录（纯英文路径），例如 `D:\paddleocr_runtime`
   （F: 的绿色版可直接整目录复制过去）。
2. ACRPA → 设置 → 高级设置 → OCR 行下方「启用 PaddleOCR.dll」：
   - 勾选「启用 PaddleOCR.dll」
   - `DLL目录` = `D:\paddleocr_runtime`
   - `模型目录` = `D:\paddleocr_runtime\inference`
   - OCR后端下拉选择 **PaddleOCR.dll**（或保持「自动」，它排在 pip 版 PaddleOCR 之后）
   - 点「DLL 自检」查看结论
3. 保存后立即生效（自检按钮会顺带重置后端探测）。

等价的 `config.json` 键（默认不启用，此处仅列出）：

```json
{
  "paddle_dll_enabled": true,
  "paddle_dll_dir": "D:\\paddleocr_runtime",
  "paddle_dll_model_dir": "D:\\paddleocr_runtime\\inference",
  "paddle_dll_proto_init": "json5",
  "paddle_dll_proto_detect": "ptr_byte",
  "paddle_dll_config": "",
  "paddle_dll_license": ""
}
```

- `paddle_dll_proto_init` / `paddle_dll_proto_detect`：调用原型名。init 固定 `json5`
  （`Initializejson(det, cls, rec, keys, json)`）；detect 三选一：
  `ptr_byte`（**默认**，传字节流，任意路径可用）、`ptr_file`（传路径，仅纯英文路径可靠）、
  `ptr_b64`（传 base64 字符串）。
- `paddle_dll_config`：参数 JSON 字符串，整体覆盖 `PaddleOCR.config.json`（可调
  `max_side_len` / `det_db_box_thresh` 等）。
- `paddle_dll_license`：付费版授权串（调用 `libaddLicense`）。

## 5. 自检与验证

```bat
:: 静态检查（DLL / 依赖 / 模型 / 非 ASCII 路径告警）
.venv\Scripts\python.exe tools\_check_paddle_dll.py --dll-dir D:\paddleocr_runtime --model-dir D:\paddleocr_runtime\inference

:: 原型扫描（1 个 init × 3 个 detect，均在子进程中执行，崩溃不影响主进程）
.venv\Scripts\python.exe tools\_check_paddle_dll.py --dll-dir D:\paddleocr_runtime --model-dir D:\paddleocr_runtime\inference --image img\image2.png

:: 彻底扫 ABI（换版本/换发行包时用）
.venv\Scripts\python.exe tools\_probe_paddle_dll.py --dll-dir D:\paddleocr_runtime --model-dir D:\paddleocr_runtime\inference --image img\image2.png
```

退出码：`0` 有可用组合、`2` 静态条件不满足、`3` 组合全失败。

## 6. 代码结构与行为保证

| 位置 | 作用 |
|---|---|
| [`src/paddle_dll.py`](../src/paddle_dll.py) | 引擎封装：目录/依赖探测、布局推断、参数 JSON、初始化、识别、释放、诊断、子进程自检与原型扫描 |
| [`src/ocr_backend.py`](../src/ocr_backend.py) | 注册 `paddle_dll` 后端（`_backend_order` / `_try_paddle_dll_backend` / `_ocr_paddle_dll(_with_positions)`），并复用 bbox 定位分支 |
| [`src/settings_window.py`](../src/settings_window.py) | 高级设置：启用开关、DLL/模型目录浏览、DLL 自检按钮、后端下拉项 |
| [`src/state.py`](../src/state.py) | 7 个配置键（默认关闭） |
| [`build.py`](../build.py) | 仅当 `lib/paddle_ocr` 集齐 DLL+4 依赖时才打进 EXE（否则打印 `[SKIP]`，保持 <15MB 目标） |

**默认零影响**：`paddle_dll_enabled=false` 时后端候选顺序与旧版完全一致
（`auto` → `paddle` → `winrt` → `tesseract`）；启用后若依赖/模型缺失或路径非 ASCII，
会自动回退到其它后端并写一条 warning 日志，不抛异常。

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `Could not find module ... (or one of its dependencies)` | 依赖 DLL 未与 `PaddleOCR.dll` 同目录 |
| `can not found model file in <目录>` | ①路径含中文/全角字符（最常见）②模型目录结构不对 ③模型非新版 IR |
| `type must be boolean, but is string` | 参数 JSON 中布尔键写成了字符串（本实现改用官方 `PaddleOCR.config.json` 原文规避） |
| `The free community edition only support box sizes <100` | 免费版限制，缩小识别区域/简化画面 |
| `does not support gpu` | `paddle_dll_config` 中保持 `"use_gpu": false` |
| 识别结果只返回 `"["` | 输出是 UTF-16LE，需用宽字符读取（本实现已处理） |
| `files or files may be damaged` + OpenCV `imread_('...') can't open/read file` | 图片路径含非 ASCII；改用 `ptr_byte`（默认）或把截图目录/程序目录换成纯英文路径 |
| `the parameter 'size' must greater than 0` | `DetectByte` 的 size 需按 Int64 传（本实现已处理） |
| 程序崩溃退出 | 原型/A B I 不匹配：用 `tools/_probe_paddle_dll.py` 在子进程里试，命中后再写入设置 |
| 首次识别较慢 | 引擎初始化会加载 3 个模型（约 1–3s），之后常驻内存 |
