# A/C RPA - Automation Workflow Tool

version: v0.1.25

A lightweight desktop automation tool built with Python + tkinter + pyautogui. It drives automation workflows via Excel scripts, supporting image recognition, mouse/keyboard control, and more.

## ✨ Key Features

- Lightweight and fast startup with minimal resource usage; Excel-script driven for ease of use;
- Image recognition for precise click targeting;
- Supports recording & playback (including drag recording), scheduled tasks;
- Dark/light theme switching, error retry, log export, and embedded Python scripts;
- **Window Management**: Directly control Windows windows without image recognition; supports window-relative coordinates;
- **Workflow Orchestration**: Multi-script chaining + visual flowchart (nodes / connections / drag-and-drop);
- **Variable System**: Supports complex data processing and math operations;
- **AI Enhancement**: Visual element location, smart retry, anomaly detection, natural language debugging;
- **Debugger**: Breakpoints / conditional breakpoints (expression shown beside marker) / step execution / variable watch / call stack.

## [Format] Quick Start

### Requirements

- Python 3.7+
- pywin32 (required for window management features)

### Installation & Running

```bash
# Create virtual environment
py -3.7 -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

cd ACRPA && python run.py \\ cd src && python ACRPA.py  # Run
```

Or simply double-click `ACRPA.exe` (Windows) from the releases.

## 📄 Script Format

Excel file (`.xls`), with row 1 as the header and commands starting from row 3.

| Column | Field      | Description                                                |
| ------ | ---------- | ---------------------------------------------------------- |
| A      | Command    | Operation type (find image, key press, wait, etc.)         |
| B      | Param 1    | Main parameter (image name, key name, duration)            |
| C–G   | Param 2–6 | Auxiliary parameters (accuracy, coordinates, region, etc.) |

### Command Reference

| Command                        | Example Parameters             | Description                                                      |
| :----------------------------- | :----------------------------- | :--------------------------------------------------------------- |
| **Basic Commands**       |                                |                                                                  |
| Find Image                     | button.png, 0.9                | Search for image on full screen                                  |
| Click Image                    | submit.png, 0.9                | Find and click image                                             |
| Region Find                    | icon.png, 0.8, 100,100,500,500 | Search within a specified region                                 |
| Key Press                      | enter, 1                       | Press a key (with optional repeat count)                         |
| Hotkey                         | ctrl, c                        | Key combination                                                  |
| Input                          | Hello World                    | Paste text input (Ctrl+V)                                        |
| Type                           | Hello, 0.05, auto              | Character-by-character input (supports DD driver)                |
| Wait                           | 2.5                            | Delay in seconds (supports random range, e.g. 1.0-3.0)           |
| Coordinate                     | 500, 300                       | Click at screen coordinates                                      |
| Scroll                         | -3                             | Scroll by number of lines                                        |
| **Window Management**    |                                |                                                                  |
| Activate Window                | Notepad                        | Activate specified window (fuzzy match supported)                |
| Close Window                   | Untitled - Notepad             | Close specified window                                           |
| Minimize Window                | Calculator                     | Minimize window to taskbar                                       |
| Maximize Window                | File Explorer                  | Maximize window                                                  |
| Get Window Position            | Notepad, x, y, w, h            | Get window coordinates and size                                  |
| Wait Window                    | Chrome, 10, exist              | Wait for window to appear/disappear                              |
| Window Coordinate              | Notepad, 100, 50               | Click relative to window's top-left corner [★] NEW              |
| **Variable Enhancement** |                                |                                                                  |
| Set Variable                   | count, 100                     | Set a variable value                                             |
| Read Clipboard                 | clip_text                      | Read clipboard content into a variable                           |
| String Operation               | text, slice, 0:5, result       | String manipulation                                              |
| Math Operation                 | x+{y}, sum                     | Mathematical calculation                                         |
| **Workflow**             |                                |                                                                  |
| Run Workflow                   | workflow.json                  | Execute a workflow file for multi-script orchestration           |
| Workflow Variable              | var_name, value                | Set a workflow-level shared variable                             |
| **AI Enhancement**       |                                |                                                                  |
| AI Find Image                  | Login Button, 0.8, click       | AI analyzes screenshot to locate element by natural language     |
| AI Recognize UI                | ui                             | AI lists all UI elements and stores them in variables            |
| AI Optimization                | log                            | AI analyzes execution data and provides optimization suggestions |
| [AI] Debug                     | Click [AI] Debug button        | Ask questions in natural language; AI analyzes logs and responds |

**Variable Reference Syntax**: Use `${variable_name}` to reference variables.

**VERSION version number global synchronization:** Simply write the first line of the VERSION file, and the rest of the modules will automatically synchronize; --Verify will scan src/confirm that there are no old version numbers remaining. Run Python tools/bumpyversion. py -- patch (or specify version number: Python tools/bumpyversion. py 0. x.xx) Run Python tools/bumpyversion. py -- verify to confirm no residue

## ⚙️ Configuration

`config.json` file:

```json
{
  "dark_mode": false,         // Toggle dark/light theme
  "retry_max": 1,             // Maximum retry attempts
  "retry_interval": 2.0,      // Retry interval (seconds)
  "image_timeout": 3.0,       // Image search timeout (seconds)
  "api_key": "",              // AI API key, e.g. skr-XXXXXX...
  "api_model": "",            // AI model selection
  "ai_smart_retry": false,    // AI smart retry (requires API Key)
  "ai_anomaly_detect": false, // AI anomaly detection (requires API Key)
   ...
}
```

## ❓ FAQ

**Image not found?**

- Check the image path, recognition accuracy (recommended: 0.8–0.95), and ensure the target window is in the foreground.

**Image recognition is slow?**

- **Prefer Window Management commands** over image recognition — they are 10–100× faster.
- Use region-based search to narrow the area, lower accuracy threshold, or reduce image size.

**Script format error?**

- Must use `.xls` format (`.xlsx` is not supported). Use Excel's "Save As" to convert.

**Recording is inaccurate?**

- Close unrelated windows before recording, operate at a steady pace, and manually correct the script afterward.

**Window management not available?**

- Ensure pywin32 is installed: `pip install pywin32`
- Running ACRPA as Administrator may improve results.

## 📚 Documentation

- [Window Management Guide](docs/窗口管理功能指南.md)
- [DD Driver Integration Guide](docs/DD_DRIVER_GUIDE.md) [★] NEW
- [User Manual](res/使用说明.txt)
- [Script Template](template/脚本模板.xls)
- [Test Script](template/窗口管理测试.xls)

### 🆕 DD Driver Enhancement (Optional)

ACRPA v0.1.22 supports the **DD Driver** as a high-performance input backend:

**Advantages**:

- ✅ Kernel-level simulation, hard to detect, with automatic fallback to PyAutoGUI;
- ✅ Background operation — no need to activate the target window; improved input speed;

**Quick Setup**:

1. Download `dd.54900.dll` from the [official repository](https://github.com/ddxoft/master)
2. Place it in the `ACRPA/lib/dd_driver/` directory
3. Set `"use_dd_driver": true` in `config.json`
4. Run the program as Administrator

**Excel Examples**:

```excel
Type, Hello World, 0.02, direct    # Use DD_str for direct input (fastest)
Type, 你好世界,    0.05, simulate  # Simulate keystrokes (supports Chinese)
Type, Test@#$%,   0.02, auto       # Auto-select the best method
```

See: [DD Driver Integration Guide](docs/DD_DRIVER_GUIDE.md)

**Contact**: yoho12138@aliyun.com

**Changelog**:

- v0.1.25 (2026-08-10): Workflow tab deep customization (operation library category tree + search + recent usage, new command/variable/loop/log node types, unified config forms, right-click copy/paste/disable/comment, outer loop count & max execution time controls, execution highlight, variable manager), workflow engine support for new node types and enabled/comment skip, settings navigation & hotkey list optimization, unified global version management (bump_version.py), open README/manual online-first with local fallback, fixed startup crash / recent-usage not working / inline-edit popdown crash / image search OpenCV fallback / workflow widgets not changing color in dark mode / missing packaged modules
- v0.1.24 (2026-08-09): Add "Advanced Settings" operation logic optimization to the settings page, fix workflow drag and drop node crashes, and change the settings page tab to the right side of the title bar ⚙  Set button, AI model dropdown box changes to unified model registry, DD DLL path configuration really takes effect, fixes claude model endpoint mapping errors, and cleans up redundant code
- v0.1.23 (2026-07-20): Template dialog redesigned (horizontal scroll buttons top + vertical scroll preview bottom), tray toggle crash protection, reset defaults button color changed to green, fixed recording actions not loading into editor bug, fixed card_log variable name conflict, fixed emoji Tcl compatibility
- v0.1.22 (2026-07-20): Structured logging upgrade (LogLevel filtering / date+time timestamps / daily rotation / auto cleanup), settings page refactored into 6 independent cards, recording button added to script editor toolbar, new log config options (log_level / enable_log_saving / log_retention_days), design improvements inspired by AutomationOperation
- v0.1.21 (2026-07-18): System tray icon (Shell_NotifyIcon + WNDPROC subclassing), Mini Bar collapse mode (always-on-top floating status bar), tray right-click menu (restore/quit), tray balloon notifications, Mini Bar drag/theme sync/real-time status updates, fixed minimize_to_tray missing restore entry point
- v0.1.20 (2026-07-17): Workflow flowchart visualization (nodes + connections + drag-and-drop), window-relative coordinate system, recording enhancements (drag / scroll / window activation), conditional breakpoint UI enhancement (expression shown beside marker), image cache LRU eviction, pre-compiled regex optimization, state.py Model-layer change notification, exclude turtle module from packaging
- v0.1.19 (2026-07-16): AI Enhancement Module — visual element location (AI Find Image / AI Recognize UI), smart retry, anomaly detection, workflow optimization suggestions, natural language debugging
- v0.1.18 (2026-07-06): OCR text recognition command, script marketplace panel, debugger enhancements (execution timing / skip line / run to cursor / in-place variable editing), reduced package size
- v0.1.17 (2026-07-05): Integrated DD Driver v63330, added high-performance input backend; optimized settings UI with DD driver configuration options; improved packaging config to auto-include DLL files; created detailed DD driver integration docs and test scripts
- v0.1.16 (2026-07-05): Fixed sys.exit() issue, implemented unified exit cleanup mechanism; improved program stability and resource release
- v0.1.15 (2026-06-05): Integrated DD Driver as optional backend, implemented dual-mode input for "Type" command (direct/simulate), with automatic fallback to PyAutoGUI
- v0.1.14 (2026-05-24): Optimized color recognition and OCR feature descriptions
- v0.1.13 (2026-05-19): Optimized AI generator and templates, improved script standards and win commands
