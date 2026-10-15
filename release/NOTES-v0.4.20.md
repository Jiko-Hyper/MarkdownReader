# MDReader 0.4.20

正式同步 0.4.12–0.4.19 本地测试更新。最新主程序包为 `MarkdownReader-0.4.20-core-win64.zip`，约 97 MB。完整解压后双击 `MDReader.exe`，或运行 `安装.exe` 选择原有程序与数据目录升级。包内提供 Windows 运行环境、Word/PDF 导出插件和使用手册。

照片识别、扫描 PDF 引擎及模型作为同一 Release 的三个独立附件。需要时从「更多 → 识别组件管理」下载安装，也可手动导入。大型模型不随主程序重复打包，升级复用已有且校验通过的组件。

本版涵盖快捷键与定位修复、侧栏搜索、默认 Markdown 打开器登记、长公式文档缩放期间的界面响应、原生安装与精简目录。卸载按程序文件清单清除应用、API 凭据、设置、缓存与识别组件，保留项目、附件和用户自有文件；重装后需重新配置 API。升级不会清除现有 API 配置与项目。

验证：Python 回归 937 项（2 项跳过），网页脚本 72 项通过；安装、卸载、目录选择、原生入口、窗口和随包插件自检通过。实际 ZIP 完整性与 SHA-256 校验通过；从 ZIP 安装、卸载、原路径重装以及从 0.4.18 升级通过。三个服务商在卸载重装后均需重新配置，两个项目和 12 个用户文件逐字节保留。

旧版安装包附件已撤下，历史源码、版本标签与更新说明保留。中英文 README、使用手册和组件说明已同步。

## English

This release publishes the changes tested locally in 0.4.12–0.4.19. Extract `MarkdownReader-0.4.20-core-win64.zip` and run `MDReader.exe`, or use `安装.exe` to install or upgrade in your existing application and data folders. The approximately 97 MB core package includes the Windows runtime, Word/PDF export plugins, and user manual.

Photo recognition, the scanned-PDF engine, and its models are three separate assets in this release. Install them on demand through Recognition component management or import the downloaded archives. Existing verified components are reused across upgrades.

The update includes shortcut/navigation fixes, searchable sidebar selectors, Markdown default-app registration, responsive zoom in formula-heavy documents, native installation, and a clean package layout. Inventory-based uninstall removes app files, API credentials, settings, caches, and recognition components while preserving projects, attachments, and user-owned files. API credentials must be configured again after reinstalling. An upgrade retains the existing configuration and projects.

Validation: 937 Python tests (2 skipped), 72 web tests, installer/uninstaller and packaged plugin checks, ZIP integrity and SHA-256, install/uninstall/reinstall, and upgrade from 0.4.18 passed. Three AI providers require reconfiguration after reinstall; two projects and 12 user-owned files were preserved byte-for-byte.

Older installer assets were removed. Source history, version tags, and release notes remain available.
