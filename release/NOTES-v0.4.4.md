# MDReader 0.4.4

修复卸载目标错误：现在依据安装时记录的程序安装目录和配置与数据目录，完整删除两个目录及全部内容。从原下载解压目录启动卸载也会定位真正的安装目标，下载包本身不受影响（除非使用 DesktopOnly 将它登记为程序目录）。

安装器保存两个绝对地址与安装标识，并核对数据目录关联。缺少或不一致的记录会停止删除。0.4.3 及更早版本请先用新版安装器选择原来的两个目录升级，再使用新版卸载。

卸载前请备份数据、保存并关闭软件，运行 `卸载.cmd`，核对两个地址，输入 `DELETE`。自动化需 `-Yes -DeleteData -NoPause`。数据目录中的文档、设置、项目和密钥均会删除；目标之外的原始文件和升级备份保留。

中英文 README 已更新。安装/升级与卸载隔离测试、正式包完整安装卸载流程均通过。

## English

Uninstall now removes both the application and data folders recorded during installation, including all their contents. Running the uninstaller from the original extracted download resolves the actual installed locations; the download remains unless it was registered as the application folder with DesktopOnly.

The installer records absolute paths and an installation ID, with a matching data-folder association. Missing or inconsistent records cause refusal. Users of 0.4.3 and earlier must first upgrade with the new installer using their existing two folders.

Back up data, save your work, close the app, run `卸载.cmd`, check both paths, and enter `DELETE`. Automation requires `-Yes -DeleteData -NoPause`. Workspace documents, settings, projects, and keys are deleted. Files and upgrade backups outside the two target folders are retained.

Both READMEs are updated. Isolated installation/upgrade/uninstall regressions and the packaged installation/uninstall lifecycle passed.
