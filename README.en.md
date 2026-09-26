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

## Download and install

1. Download `MarkdownReader-<version>-win64.zip` from [Releases](https://github.com/Jiko-Hyper/MarkdownReader/releases), **not** GitHub's “Source code” archive.
2. Extract the entire ZIP into a folder.
3. Double-click **`MDReader.exe`** to run, or **`安装到桌面.cmd`** to install and create a desktop shortcut.

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
.\安装到桌面.ps1 -InstallDir D:\Apps\MDReader -WorkspaceDir D:\Documents\MDReader -NoPause
```

`-NoUI` skips the folder-selection window. You can also right-click `安装到桌面.ps1` and choose **Run with PowerShell**. To uninstall, run `卸载.ps1` from the installed application folder; workspace data is retained by default.

### Installation troubleshooting

- Extract the complete package before running it; do not launch files inside the ZIP preview.
- Installation errors remain visible in the console window.
- The `.cmd` installer sets the execution policy for its own PowerShell process only; it does not permanently change system policy. Organization-managed policies may still block execution.
- If Windows blocks downloaded files, verify the source first. If an **Unblock** option is available in the ZIP's Properties dialog, use it and extract again.
- The application is unsigned, so Windows may show a security warning. Do not disable antivirus or other system protections.

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
| `Ctrl+W` | Close the current tab |
| `Ctrl+Tab` | Switch tabs |
| `Ctrl+E` | Toggle preview/source mode |
| `Ctrl+B` | Show or hide the sidebar |
| `Ctrl+F` | Find text in the document |
| `Ctrl+L` | Open heading navigation |
| `F5` | Refresh the current document |
| `Ctrl+Mouse wheel` | Adjust text size |
| `Ctrl+Shift+T` | Insert a table |
| `Ctrl+Shift+M` | Insert an inline formula |
| `Ctrl+Shift+V` | Insert a clipboard image with the image-insertion plugin |

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
release\MarkdownReader-<version>-win64.zip
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

I'm a university student building MDReader as a personal, non-commercial hobby project. It is still evolving, and some areas need more polish. Multilingual support and API integration are among the ideas for future development.

Bug reports, suggestions, and new plugins are welcome. Please use the repository's Issues to share feedback and help improve the project.
