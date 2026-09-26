# MDReader 0.4.0

## 简体中文

下载 `MarkdownReader-0.4.0-win64.zip`，完整解压后双击 `MDReader.exe`。随包包含 Python 运行时、四个离线插件及依赖，无需另装 Python。

- 新增 GPT、Claude、DeepSeek 对话与模型配置；支持附上当前文档、查看修改差异、手动应用和撤销。AI 请求需要联网，可能产生服务商费用；不会自动保存修改。
- 新增外部 AI 控制接口：在「更多」开启本机服务，复制接入信息；读取与修改权限分开设置。
- 新增离线格式转换：PDF / DOCX → Markdown、Markdown ↔ HTML。在「更多 → 插件管理」安装并启用随包转换插件，再从「更多 → 文档格式转换」操作。读取磁盘文件、不覆盖已有目标；扫描 PDF 不支持 OCR。
- 改进 Ctrl+F：主题搜索框、匹配计数、全部高亮、首尾跳转与循环查找；修复快捷键拦截和拼音位置问题。
- 正文选中文字后按 Ctrl+M，可在预览与源码间定位；分别记忆每篇文档各视图的阅读位置。Ctrl+Home / Ctrl+End 跳到文章首尾。
- 更新中英文 README、使用说明、AI 接入和转换文档，并同步发布包中的源码与插件受信清单。

升级：先保存并关闭软件，运行新包中的 `安装到桌面.cmd`，选择原安装目录与原数据目录。安装程序备份旧程序并保留数据。转换插件要求宿主 0.4.0 及以上，不能仅安装插件 ZIP 给旧宿主升级。

## English

Download `MarkdownReader-0.4.0-win64.zip`, extract the complete archive, and run `MDReader.exe`. Python, four offline plugins, and their dependencies are bundled.

- Added GPT, Claude, and DeepSeek configuration and chat, with optional document context, edit review, manual apply, and undo. AI requests require internet access and may incur provider charges; edits are not saved automatically.
- Added a local external AI control interface with separate read and write permissions, available from the More menu.
- Added offline PDF/DOCX → Markdown and Markdown ↔ HTML conversion. Install and enable the bundled conversion plugin in Plugin manager, then use More → Document conversion. Conversion reads saved files, preserves existing targets, and does not perform OCR on scanned PDFs.
- Improved Ctrl+F with themed search, match counts, highlights, wrapping navigation, and first/last controls; fixed shortcut interception and IME positioning.
- Select document text and press Ctrl+M to navigate between preview and source. View positions are remembered separately per document; Ctrl+Home / Ctrl+End navigate to the beginning/end.
- Updated Chinese and English READMEs, usage instructions, AI and conversion guides, and the Windows package.

Upgrade: save your work, close the application, run `安装到桌面.cmd` from the new package, and choose your existing application and data folders. The installer backs up the previous application and retains data. The conversion plugin requires host version 0.4.0 or later.

The UI remains Simplified Chinese. AI protocol tests use local mock services; availability, balance, and connectivity for a real provider account should be checked through Test connection after entering your key.
