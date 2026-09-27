# MDReader 0.4.1

## 简体中文

下载 `MarkdownReader-0.4.1-win64.zip`，完整解压后运行 `MDReader.exe`。随包包含 Python 运行时与离线插件，无需另装 Python。

- 桌面 AI 新增选区润色、精简、扩写和自定义修改。默认只发送选区；可显式增加上下文，查看差异后应用，仅替换所选范围，可一次撤销，不自动保存。文档变动后拒绝过期建议。
- 工具栏新增「导出 Word/PDF」，可按格式安装/启用随包插件；完成后可直接打开文件或所在文件夹。
- Word/PDF 插件更新至 1.0.4，改进公式、表格、列表和长代码排版，增加固定交付样本及回归检查。
- 正文预览/源码定位快捷键改为 `Shift+M`；项目树重命名仍为 `Ctrl+M`。
- 同步中英文 README、使用说明和插件受信清单。

升级前请保存并关闭旧程序。解压新包，运行 `安装到桌面.cmd`，选择原安装目录与原数据目录。已安装旧版 Word/PDF 插件的用户，请在插件管理中安装 `plugins/packages/` 下的 1.0.4 包并启用。

选区 AI 功能目前仅有桌面入口。AI 需要联网及服务商 API 凭据；人工首次使用观察和各显示缩放下的完整验收仍待完成。

## English

Download `MarkdownReader-0.4.1-win64.zip`, extract it fully, and run `MDReader.exe`. Python and the offline plugins are bundled.

- Added desktop AI selection editing: polish, shorten, expand, or use a custom instruction. Only the selection is sent by default; additional context is explicit. Review and apply changes to the selected range, undo in one step, and save manually. Stale suggestions are rejected.
- Added a direct Word/PDF export toolbar entry with guided bundled-plugin installation/enabling and result file/folder actions.
- Updated Word/PDF plugins to 1.0.4, improving formulas, tables, lists, and long code layout, with fixed delivery samples and regression coverage.
- Desktop preview/source positioning now uses `Shift+M`; project-tree renaming still uses `Ctrl+M`.
- Updated bilingual documentation and the trusted-plugin manifest.

Save and close the old application before upgrading. Extract the new package, run `安装到桌面.cmd`, and keep the existing application and data folders. Install and enable the bundled 1.0.4 Word/PDF ZIPs through Plugin manager if older plugins are installed.

Selection editing is desktop-only. AI requires network access and provider credentials. First-use observation and full manual testing across display scaling settings remain incomplete.
