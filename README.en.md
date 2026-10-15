<div align="center">

<img src="assets/icon.png" width="112" alt="MDReader">

# MDReader

[简体中文](README.md) | **English**

**A local Markdown reader, editor, and project organizer for Windows.**

Tabbed editing, tables, math, and Word/PDF export — available offline.

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-3A4254)
![python](https://img.shields.io/badge/python-3.9%2B%20(3.12%20tested)-3776AB)
![license](https://img.shields.io/badge/license-MIT-8B6AEB)

</div>

> This is the English documentation. The application and installer currently use Simplified Chinese; multilingual UI support is planned.

## 0.4.20: current Windows release

Download [`MarkdownReader-0.4.20-core-win64.zip`](https://github.com/Jiko-Hyper/MarkdownReader/releases/tag/v0.4.20), extract it completely, then run `MDReader.exe` or `安装.exe` to choose the application and data folders. The core package includes the Windows runtime and common Word/PDF export plugins. Offline photo and scanned-PDF recognition components are separate assets in the same release and can be installed on demand through **More → Recognition component management**. Upgrades reuse verified components instead of downloading the large models again.

This release includes the changes tested locally in 0.4.12–0.4.19: shortcut and navigation fixes, searchable sidebar selectors, a clean installation layout, Markdown default-app registration, responsive zoom in formula-heavy documents, and inventory-based uninstall. Uninstall clears the app and its API credentials, settings, cache, and recognition components while retaining projects, attachments, and user-owned files. Reinstallation requires setting up API credentials again. See the [component guide](docs/MODULAR_INSTALL.md) and [user manual](使用手册.md).

Older installer assets have been removed from Releases. Source history, tags, and release notes remain available.

## 0.4.18: clean download and installation folders (local test build)

The outer folder contains only `MDReader.exe`, `安装.exe`, `卸载.exe`, the user manual and `resources`. Source, scripts, runtime and internal records live under resources. Keep that folder with the executables. Use the full core installer to upgrade from the old layout; projects and extra user files are retained.

## 0.4.17: searchable sidebar selectors (local test build)

Type a name or path in My Folders or Projects to filter the dropdown. Enter or a result click commits the selection; Escape restores it. Text fields, results and Chinese composition match the current theme and recent-file search. Delete edits text; Ctrl+Delete removes the current history record while keeping local files. Only the test application and local compact packages are updated.

## 0.4.12: local shortcut maintenance build

Fixes Ctrl+O editing the source before opening a file, Caps Lock failures for Save As/table insertion, project rename intercepting the formula shortcut, and web shortcuts modifying the document behind a modal dialog. Escape now closes heading navigation. Restart the desktop test application after saving your work to load the changes. This build is prepared locally; see [shortcut audit](docs/SHORTCUT_AUDIT.md).

## 0.4.11: bundled offline scan recognition

The full Windows ZIP includes a standalone Python runtime and local scan models. Scanned PDFs become editable Markdown and LaTeX, with page selection, cancellation, original-page links and formula review notes. Results require manual review; 98% recognition accuracy has not been established. Photo OCR, export styles/preflight, PDF preview, AI change review and request recovery are included.

Extract the complete ZIP and run `MDReader.exe`, or use `安装.exe` to choose installation folders. Save and close the previous version first. See [third-party scan components](docs/THIRD_PARTY_SCAN.md) for dependencies and notices.

## 0.4.2: taskbar identity fix

The Windows release now declares its own application identity, purple MD icon, and relaunch command. Pinning a running window launches `MDReader.exe` instead of the internal Python interpreter. Installer-created shortcuts use the same identity; source development uses a separate identity.

If an older release created a Python taskbar pin, save your work, close the old version, upgrade to 0.4.2, unpin the old Python item, and pin the new running app once. Existing pins are not modified automatically.

## Local test build 0.4.15

Use `Ctrl+Shift+E` to switch views at the selection, source caret or current preview position. Duplicate text inside hidden link destinations no longer steals the match; uppercase M remains normal input. The folder browser shows files and directory rows expand on a single click. A 57 KB native `卸载.exe` clears application files and API credentials after confirmation while retaining projects and user files. The compact core package reuses separate OCR components. This is a local test update only.

## Uninstall: clear app data and retain user files

Double-click `安装.exe` in the extracted download and choose two dedicated folders. The installer records the **application folder** and **configuration/data folder**. Uninstall removes program files, API profiles and keys, settings, history, caches, plugins and recognition components. User projects, attachments and personal files are retained. Reinstallation requires entering API credentials again, even when the key is identical.

Save your work and close the app, then double-click `卸载.exe` in the installed folder or the original extracted download. Check both displayed paths and choose Yes to clear program files and application settings while preserving user files, or No to cancel. The internal script is under `resources/setup`.

```powershell
# Install from the extracted download
.\安装.exe
# Full uninstall from the installed folder or original extracted download
.\卸载.exe
# Automation: explicitly confirm removal of the application AND data
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Apps\MDReader\resources\setup\卸载.ps1" -InstallDir "D:\Apps\MDReader" -Yes -DeleteData -NoPause
```

`resources/installation.json` in the application folder stores both absolute paths and the installation ID. `installed-target.json` in the extracted package resources folder points to the latest installation completed from that download. A matching association is stored in the data folder. Uninstall cross-checks these records instead of treating the script location as its deletion target. With `-DesktopOnly`, the extracted folder itself is registered as the application folder and its program files are removed while user files are preserved.

**For 0.4.3 and earlier:** first upgrade with the latest installer, selecting your existing application and data folders to create the complete records. Missing, damaged, mismatched, overlapping, or linked paths and running applications cause refusal without guessing a target. The old `-Yes` command alone is refused; unattended removal requires both `-Yes -DeleteData`. `-NoPause` only skips the final wait.

Application files and app-owned settings are removed by the verified inventory. Projects, attachments and user-owned files remain, even inside the selected folders. Shared data locations receive a warning. Matching shortcuts and file associations are removed; unpin old taskbar items manually.

## Download and install

1. Download `MarkdownReader-0.4.20-core-win64.zip` from [Releases](https://github.com/Jiko-Hyper/MarkdownReader/releases), **not** GitHub's “Source code” archive.
2. Extract the entire ZIP into a folder.
3. Double-click **`MDReader.exe`** to run, or **`安装.exe`** to install and create a desktop shortcut.

The Windows release includes Python and the offline export plugins and dependencies. No separate Python installation is required for normal use.

The installer offers two folder selectors:

| Installer label | Purpose |
| --- | --- |
| 程序安装目录 | Application installation folder |
| 配置与数据目录 | Settings, recent-file history, projects, and other workspace data |
| 浏览文件夹… | Browse for a folder |
| 安装 / 取消 | Install / Cancel |

You can browse or type either path. The two folders must be separate: neither may contain the other. The data location is saved in `installation.json` beside the application, so both the desktop shortcut and the EXE use it.

By default, workspace data is stored in `%USERPROFILE%\MDReader`. Choosing a new folder does **not** migrate old documents; choose the existing data folder to keep using its records. Save your work and close the application before upgrading. The installer backs up an existing installation to a neighboring `.backup-*` folder before copying the new files.

For command-line installation:

```powershell
.\安装.exe --install-dir D:\Apps\MDReader --workspace-dir D:\Documents\MDReader --no-ui
```

`--no-ui` skips the folder-selection window. To uninstall, run `卸载.exe` and confirm the recorded locations. Application files and API settings are removed; projects and personal files stay in place. Reinstallation requires configuring API credentials again.

### Installation troubleshooting

- Extract the complete package before running it; do not launch files inside the ZIP preview.
- Installation errors appear in a dialog.
- The installer sets the execution policy for its own PowerShell process only; it does not permanently change system policy. Organization-managed policies may still block execution.
- If Windows blocks downloaded files, verify the source first. If an **Unblock** option is available in the ZIP's Properties dialog, use it and extract again.
- The application is unsigned, so Windows may show a security warning. Do not disable antivirus or other system protections.

## What's new in 0.4.0 and how to use it

- **AI chat and document edits:** click **AI 接入** (AI connection) in the toolbar. Choose GPT, Claude, or DeepSeek, enter your provider API key, then click **保存并获取模型** (Save and fetch models) and **测试连接** (Test connection). Send with **发送** or `Ctrl+Enter`. Model availability depends on your provider account; custom model IDs and compatible endpoints are supported. Connection tests and chat require internet access and may incur provider API charges.
- **Apply an AI edit:** **附上当前文档** (Attach current document) is off by default. Enable it to send the current buffer, including unsaved text, along with your request. Review the proposed change and click **应用修改** (Apply changes); save separately with `Ctrl+S`. Desktop users can undo in the source editor with `Ctrl+Z`; the browser assistant has **撤销此次修改** (Undo this change). If the document changes while waiting, stale edits are rejected. Ordinary replies can be copied or inserted at the cursor. Keys are stored using Windows account encryption; chat history is only kept in the current assistant window. See [AI model guide (Chinese)](docs/AI_MODELS.md).
- **External AI control:** open **更多 → 外部 AI 控制接口（高级）** (More → External AI control interface), enable the local service, and copy its connection details into an assistant that supports local HTTP tools. Enable write access separately to create, save, or replace document text. This local key is separate from provider API keys. See [API guide (Chinese)](docs/AI_API.md).
- **Offline format conversion:** in **更多 → 插件管理** (More → Plugin manager), install and enable the bundled `mdreader.document-convert-1.0.0.zip`. Open **更多 → 文档格式转换** (More → Document conversion), choose PDF/DOCX → Markdown or Markdown ↔ HTML, select the source file and a new destination. Save edits first: conversion reads the disk file, preserves the source, and refuses to overwrite an existing output. Scanned PDFs have no OCR support; retain generated image folders alongside Markdown output. This feature currently has a desktop menu only and requires host version 0.4.0 or later. See [conversion guide (Chinese)](plugins/official/document-convert/README.md).
- **Find and navigation:** `Ctrl+F` opens a themed, draggable search panel. Typing counts and highlights all matches. First/previous/next/last controls navigate matches, with previous/next wrapping around. Press `Ctrl+F` again, Esc, or click outside to close. Select text in the desktop document and press `Ctrl+Shift+E` to switch between preview and source at the matching text. Without a selection, this shortcut locates the source caret or the current preview reading position. `Ctrl+E` restores each view’s independent position, remembered per document across normal restarts. Uppercase M remains normal text input. `Ctrl+Home` / `Ctrl+End` move to the beginning/end.

**Upgrade from 0.3.0 / 0.4.0:** save your documents, close the application, download `MarkdownReader-0.4.20-core-win64.zip` from this release, extract it fully, and run `安装.exe` using the existing application and data folders. Install and enable the new conversion plugin in Plugin manager. Updating only the plugin ZIP does not update an older host.

## 0.4.1 maintenance update

- **Desktop selection edits:** select text, open **AI 接入**, then choose polish, shorten, expand, or a custom instruction. By default only the selection is sent; choose paragraph/full-document context explicitly if needed. Review the difference and apply the suggestion. Only the selected range changes, one undo restores it, and saving remains manual. Stale suggestions are rejected. This selection workflow is currently desktop-only; browser editing still uses the whole-document workflow.
- **Direct export:** the desktop toolbar now offers **导出 Word/PDF**. Install or enable the chosen bundled plugin when prompted, then export and open the result or its folder. Bundled Word/PDF plugins are version 1.0.4, with improvements to formulas, tables, lists, and long code lines. If an older plugin is installed, install the new bundled ZIP in Plugin manager and enable it.
- **Navigation:** use `Ctrl+Shift+E` for desktop preview/source positioning. Project-tree renaming still uses `Ctrl+M`.

Automated window checks are available; first-time-user observation and manual verification at all display scaling levels remain incomplete.

## Features

- **Reading:** light, dark, and warm reading themes; adjustable font size; heading navigation; in-document search; tables and code blocks.
- **Editing:** multiple closable tabs, independent editing state, preview/source switching, and recovery snapshots after a short pause in typing.
- **Math:** inline `$…$` and display `$$…$$` formulas, rendered locally using Windows GDI. A supported TeX subset is available; a full LaTeX engine is not included.
- **Organization:** projects and registered local folders, recent-file search by name or path, and drag-and-drop Markdown files.
- **Saving:** UTF-8, BOM, UTF-16, and GB18030 support, with checks for external changes before overwriting a document.
- **Plugins:** offline Word (`.docx`) and PDF export, plus image insertion. Plugin sources, packages, and bundled dependencies are in `plugins/`.
- **Local operation:** document processing runs on your computer. The release does not require an online rendering service.

## Community plugins

Find new plugins and community plugin development on the main repository's [Plugins branch](https://github.com/Jiko-Hyper/MarkdownReader/tree/Plugins). Contributions are welcome: fork the repository, add or update a plugin, and open a pull request targeting `Plugins`. Maintainers review and merge changes; public access does not grant direct write access. See [Contributing](CONTRIBUTING.md).

Plugins on this branch may not yet be trusted or supported by the current release. Check compatibility and plugin documentation before installing; packages must pass the application's trusted-package checks.

## Common shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` | Create an untitled document; unsaved tabs have a `#` prefix |
| `Ctrl+O` | Open a local file |
| `Ctrl+S` | Save the current document |
| `Ctrl+Shift+S` | Save as (choose a new location) |
| `Ctrl+Z` / `Ctrl+Y` | Source editor: undo / redo (including applied AI edits); `Ctrl+Y` in the project tree shows the file path |
| `Ctrl+W` | Close the current tab |
| `Ctrl+Tab` | Switch tabs |
| `Ctrl+E` | Toggle preview/source mode |
| `Ctrl+Shift+E` (document) | Desktop: switch preview/source and locate the selected text |
| `Ctrl+Home` / `Ctrl+End` (document) | Desktop: move the caret to the start / end |
| `Ctrl+B` | Show or hide the sidebar |
| `Ctrl+F` | Find text in the document |
| `Enter` / `Shift+Enter` (find box) | Next / previous match |
| `Esc` | Close the current dialog; clear the recent-file search box |
| `Ctrl+L` | Open heading navigation |
| `F5` | Refresh the current document |
| `Ctrl+Mouse wheel` | Adjust text size (preview and source) |
| `Ctrl+Shift+T` | Insert a table |
| `Ctrl+Shift+M` | Insert an inline formula |
| `Ctrl+Shift+V` | Insert a clipboard image with the image-insertion plugin |
| `Ctrl+Enter` (AI prompt) | Send the request |
| `←` / `→` (PDF preview) | Previous / next page; `Esc` returns to editing |
| `Enter` / `Esc` (scan page box) | Start recognition / cancel |
| Drag into window | Read and edit the original `.md`; a dropped image is inserted at the caret |
| `Delete` (project tree) | Delete the selected file or folder (asks first) |
| `Delete` / `Enter` (recent list) | Remove the entry (keeps the file) / open the file |
| `Ctrl+M` (project tree) | Change the title of the selected item |
| Double-click title | Change the title and rename the local file |
| `Enter` (project tree) | Open the selected document |

Drag a `.md` file into the window to open and edit the original file. In the project tree, `Enter` opens a document, `Delete` requests deletion, `Ctrl+M` changes a title, and `Ctrl+Y` displays the file path.

## Run from source

Python 3.9+ is expected; Python 3.12 is the tested release runtime. The native desktop interface needs `tkinter`. The application can start with the standard library; it uses `markdown-it-py` when available and otherwise falls back to its built-in parser. Export plugins have additional dependencies.

```powershell
python main.py
python main.py --browser
python main.py --workspace D:\notes
python main.py --open "notes.md"
python main.py --selftest
```

The launcher selects an embedded WebView2 interface when available, otherwise the native interface, with a browser fallback. The release's `MDReader.exe` launches the bundled Python runtime and `main.py`.

## Build a Windows release

Use Windows, Python 3.12 with `tkinter`, and MinGW-w64 (`gcc`; `windres` is recommended for the icon and version resources). Preparing the runtime requires downloading build dependencies.

```powershell
# Only needed when regenerating the application icon:
python -m pip install --user pillow

# Prepare the runtime, compile the launcher, test, and package:
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1
```

Outputs:

```text
build\release\MarkdownReader\           Complete unpacked application
release\MarkdownReader-<version>-core-win64.zip
```

The build runs installer regression checks, folder-selection dialog tests, launcher argument tests, and release checks for startup, window creation, and bundled plugins. The full build requires `gcc`; without it, run the Python source instead. The alternative `启动 MDReader.bat` launcher still requires `MDReader.exe`.

To investigate a startup failure, run this from the extracted release folder:

```powershell
.\MDReader.exe --console
```

## Project layout and development

| Path | Contents |
| --- | --- |
| `main.py`, `mdreader/` | Python entry point and application modules |
| `webui/` | Embedded HTML, CSS, and JavaScript interface |
| `plugins/` | Official plugin sources, packages, and dependencies |
| `assets/` | Application icons |
| `release/app/` | Installer and launcher script templates |
| `tools/` | Build, validation, and benchmark tools |
| `tests/` | Automated tests |
| `docs/` | User guide, architecture, and development notes |

```powershell
powershell -ExecutionPolicy Bypass -File tools/scripts/check.ps1
python tools/scripts/acceptance.py
python tools/scripts/bench_interaction.py
```

Further documentation is currently in Chinese: [User guide](docs/USAGE.md), [Architecture](docs/ARCHITECTURE.md), [Development](docs/DEVELOPMENT.md), and [Roadmap](docs/ROADMAP.md).

## Known limitations

- Windows 10/11 x64 only; the UI is currently in Simplified Chinese.
- The fallback Markdown parser is not a full CommonMark implementation. Footnotes and Mermaid content have limited rendering support; preserving their text does not imply diagram rendering.
- Math rendering supports a fixed TeX subset, not arbitrary LaTeX packages.
- Binary files and files larger than 8 MB are rejected.
- Browser-mode export and local-file opening require the application's session authorization. Files outside the workspace must first be authorized through the local-file opening flow.

## License

MDReader is released under the [MIT License](LICENSE). The bundled Python runtime and third-party dependencies retain their respective licenses, including the Python Software Foundation license and the licenses for Pillow, markdown-it-py, python-docx, lxml, and ReportLab.

## A note from the developer

I'm a university student building MDReader as a personal, non-commercial hobby project. It is still evolving, and some areas need more polish. AI API integration is now available; multilingual support and further usability improvements are planned.

Bug reports, suggestions, and new plugins are welcome. Please use the repository's Issues to share feedback and help improve the project.
