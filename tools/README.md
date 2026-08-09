# ACRPA 打包和诊断工具

本目录包含用于 ACRPA 项目打包、诊断和维护的辅助工具。

## 📋 工具列表

### 1. diagnose.py - 综合诊断工具

检查代码语法、导入、托盘功能和打包准备状态。

**用法:**
```bash
# 完整诊断
python tools/diagnose.py

# 仅语法检查
python tools/diagnose.py --syntax

# 仅导入检查
python tools/diagnose.py --import

# 仅托盘功能检查
python tools/diagnose.py --tray

# 仅打包准备检查
python tools/diagnose.py --build
```

**输出示例:**
```
============================================================
语法检查
============================================================

✅ src/tray.py
✅ src/ACRPA.py
✅ src/state.py
✅ src/engine.py
✅ src/commands.py

============================================================
诊断结果汇总
============================================================

语法检查        : 通过
导入检查        : 通过
托盘功能        : 通过
打包准备        : 通过

✅ 所有检查通过！可以安全打包。
```

---

### 2. safe_build.py - 安全打包脚本

自动处理进程关闭、文件锁定和打包验证。

**用法:**
```bash
# 标准打包（单文件 EXE）
python tools/safe_build.py

# 清理后打包
python tools/safe_build.py --clean

# 文件夹模式（调试用）
python tools/safe_build.py --dir
```

**自动化流程:**
1. ✅ 检查并关闭 ACRPA.exe 进程
2. ✅ 删除被锁定的 dist/ACRPA.exe
3. ✅ 执行 build.py 打包
4. ✅ 验证输出文件大小和完整性

**优势:**
- 避免 `PermissionError: [WinError 5]` 错误
- 自动验证打包结果
- 提供清晰的测试建议

---

### 3. clean_before_build.py - 打包前清理

快速清理进程、缓存和旧文件。

**用法:**
```bash
python tools/clean_before_build.py
```

**清理内容:**
- ACRPA.exe 进程
- dist/ACRPA.exe 文件
- build/ 缓存目录
- 所有 __pycache__ 目录

---

## 🔧 常见问题解决

### 问题 1: PermissionError 拒绝访问

**现象:**
```
PermissionError: [WinError 5] 拒绝访问。: 'D:\ACRPA\dist\ACRPA.exe'
```

**解决方案:**

方法 1 - 使用自动化工具（推荐）:
```bash
python tools/safe_build.py
```

方法 2 - 手动清理:
```powershell
# PowerShell
Stop-Process -Name ACRPA -Force -ErrorAction SilentlyContinue
Remove-Item -Path dist\ACRPA.exe -Force -ErrorAction SilentlyContinue
python build.py
```

方法 3 - 使用清理脚本:
```bash
python tools/clean_before_build.py
python build.py
```

---

### 问题 2: 托盘图标不显示

**排查步骤:**

1. 运行诊断工具:
   ```bash
   python tools/diagnose.py --tray
   ```

2. 检查图标文件:
   ```bash
   # 确认 res/automation.ico 存在
   dir res\automation.ico
   ```

3. 检查代码集成:
   - 确认 `from tray import SystemTray` 在 ACRPA.py 中
   - 确认 `_ensure_tray()` 在窗口初始化时调用

---

### 问题 3: 打包体积过大

**优化建议:**

1. 检查是否有不必要的依赖:
   ```bash
   pip list
   ```

2. 查看 build.py 中的 EXCLUDE_MODULES:
   - numpy (~20 MB) - 已排除
   - opencv-python (~60 MB) - 已排除
   - matplotlib, scipy, pandas - 已排除

3. 启用 UPX 压缩:
   - 确认 H:\UPX\upx.exe 存在
   - build.py 会自动使用 UPX

4. 清理后重新打包:
   ```bash
   python tools/safe_build.py --clean
   ```

---

## 📊 性能指标

### 托盘功能优化

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| 轮询间隔 | 50ms | 80ms | CPU ↓37.5% |
| 事件处理 | O(n²) | O(n) | 性能↑ |
| 内存占用 | 有额外线程 | 零线程 | 内存↓ |

### 打包体积目标

| 组件 | 大小 |
|------|------|
| Python 运行时 | ~8 MB |
| tkinter | ~5 MB |
| Pillow | ~3 MB |
| pyautogui | ~1 MB |
| requests + 依赖 | ~2 MB |
| 项目代码 | ~1 MB |
| **总计（压缩后）** | **15-25 MB** |

---

## 🎯 最佳实践

### 打包前检查清单

- [ ] 运行 `python tools/diagnose.py` 确保所有检查通过
- [ ] 确认没有 ACRPA.exe 正在运行
- [ ] 确认 res/automation.ico 存在
- [ ] 确认 config.json 配置正确
- [ ] 清理 build 缓存（可选）

### 打包命令选择

| 场景 | 命令 |
|------|------|
| 日常开发测试 | `python build.py --dir` |
| 发布版本 | `python tools/safe_build.py` |
| 彻底清理重打包 | `python tools/safe_build.py --clean` |
| 调试控制台输出 | `python build.py --console` |

### 测试流程

1. **基础功能**
   - 双击运行 ACRPA.exe
   - 检查主窗口正常显示
   - 检查托盘图标出现

2. **托盘交互**
   - 左键单击 → 恢复主窗口
   - 右键单击 → 显示菜单
   - 菜单"退出" → 完全退出

3. **Mini Bar 折叠**
   - 启用折叠模式
   - 最小化到托盘
   - 从托盘恢复 → 应显示 Mini Bar

4. **性能监控**
   - 启动时间 < 3秒
   - 内存占用 < 100 MB
   - CPU 占用 < 5%（空闲时）

---

## 📝 维护建议

### 定期任务

1. **每月**: 检查并更新依赖包
   ```bash
   pip list --outdated
   pip install --upgrade <package>
   ```

2. **每季度**: 审计 EXCLUDE_MODULES
   - 移除新引入的不必要依赖
   - 添加新的需要排除的包

3. **每次发布前**: 运行完整诊断
   ```bash
   python tools/diagnose.py
   ```

### 版本管理

- 修改 `src/updater.py` 中的 VERSION
- 更新 `README.md` 中的版本号
- 提交 git tag: `git tag v0.1.16 && git push --tags`

---

## 🆘 获取帮助

如果遇到问题:

1. 运行诊断工具查看详细错误信息
2. 检查 `build/ACRPA/warn-ACRPA.txt` 警告日志
3. 查看 `build/ACRPA/xref-ACRPA.html` 依赖关系图
4. 联系项目作者: yohoten

---

**最后更新**: 2026-07-19  
**工具版本**: 1.0.0
