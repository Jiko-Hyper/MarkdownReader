# MDReader 0.4.3 — Install and uninstall commands

## 简体中文

- 新增双击卸载入口 `卸载.cmd`，安装器同步复制。保存并关闭软件后，在安装目录运行，输入 `YES` 确认。
- 支持明确指定安装路径及自动化 `-Yes -NoPause`；取消不修改文件，失败返回非零状态。
- 保留独立文档与配置目录，保留安装目录顶层额外个人文件；只清理属于本安装的快捷方式与文件关联。程序子目录仍属于卸载范围，个人资料请存放在数据目录。
- 检查运行中的进程、配置、安装标记、路径重叠及目录链接；不再支持清除用户数据的 `-Purge`。
- 补齐中英文说明，新增卸载回归测试并纳入发布构建门槛。

下载 `MarkdownReader-0.4.3-win64.zip`，完整解压后运行 `安装到桌面.cmd`。旧任务栏图标卸载后请手动取消固定。

## English

- Added `卸载.cmd` for double-click uninstallation, included by the installer. Save and close the app, run it from the installed folder, and enter `YES`.
- Supports an explicit installation path and automation with `-Yes -NoPause`; cancellation preserves files and failures return a nonzero status.
- Retains separate workspace documents/settings and unrelated top-level files; removes only matching shortcuts/file associations. Application subdirectories are removed, so keep personal documents in the workspace.
- Checks running processes, configuration, package markers, overlapping paths, and filesystem links. Destructive data removal with `-Purge` is no longer supported.
- Updated bilingual instructions and added uninstall regression checks to the release build.

Download `MarkdownReader-0.4.3-win64.zip`, extract fully, and run `安装到桌面.cmd`. Remove old taskbar pins manually after uninstalling.
