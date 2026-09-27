# MDReader 0.4.2 — Taskbar identity fix

## 简体中文

修复正式版从运行中的窗口固定到任务栏后变成 Python 图标、固定入口缺少启动参数的问题。

- 启动时声明 MDReader 应用身份，桌面与内嵌浏览器窗口设置紫色图标、显示名称和指向 `MDReader.exe` 的重启命令。
- 安装器创建的快捷方式写入相同的应用身份；源码开发版使用独立身份，避免混组。
- 增加真实 Windows 窗口属性、进程身份、快捷方式持久化及中文/空格路径测试。

下载 `MarkdownReader-0.4.2-win64.zip`，保存并关闭旧版后安装。**已经固定的旧 Python 图标需要取消固定一次，再打开新版 MDReader 并重新固定**；程序不会自动改写你的任务栏布局。文档与数据目录沿用原位置。

## English

Fixes running MDReader windows being pinned as Python, with a missing application entry point.

- Declares a dedicated application identity and sets the top-level window's purple icon, display name, and relaunch command pointing to `MDReader.exe`.
- Installer-created shortcuts store the matching identity. Source development uses a separate identity.
- Adds native Windows property-store, process-identity, persisted-shortcut, and Unicode/space-path regression checks.

Download `MarkdownReader-0.4.2-win64.zip`, save your work, close the old application, and upgrade. **Unpin the old Python item, open the updated MDReader, and pin it again once.** Existing pins are not changed automatically. Keep your existing data directory.

Implementation reference: [Microsoft — AppUserModel relaunch command](https://learn.microsoft.com/en-us/windows/win32/properties/props-system-appusermodel-relaunchcommand).
