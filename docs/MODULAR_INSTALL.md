# 主程序、可选识别组件与小体积更新（0.4.20）

## 下载与安装

- `MarkdownReader-0.4.20-core-win64.zip`：最新主程序安装包。完整解压后运行 `安装.exe` 选择程序与数据目录，或运行 `MDReader.exe` 直接使用。阅读、编辑、管理 Markdown 及随包 Word/PDF 导出无需识别组件。
- `MDReader-photo-ocr-…-win64.zip`：照片增强识别组件。
- `MDReader-scan-engine-…-win64.zip` 与 `MDReader-scan-models-…-win64.zip`：扫描 PDF 离线识别所需的引擎和模型，需同时安装。

三个组件均在 [v0.4.20 Release](https://github.com/Jiko-Hyper/MarkdownReader/releases/tag/v0.4.20) 单独提供。主程序清单中的下载地址、大小和 SHA-256 对应这些附件；旧版 0.4.13 地址不再使用。

识别组件通过“更多 → 识别组件管理”下载或导入。首次使用缺少组件的识别功能时，也会打开组件管理窗口。下载与安装显示进度，支持取消；未下载完的内容可在重试时续传。组件包及解压文件均按随主程序提供的 SHA256 清单校验，通过后才启用。

组件保存在所选数据目录的 `components` 下，独立于程序目录。主程序升级继续复用相同内容的组件；模型内容未变时不需要重新下载。完整离线版和旧版已包含的组件也会被直接复用。下载完成并安装成功后，临时压缩包会删除，避免重复占用磁盘。

## 更新

历史上的 `MarkdownReader-0.4.12-to-0.4.13-update.zip` 曾只包含相对 0.4.12 安装包变化的文件；当前发布请使用 0.4.20 主程序包。解压到独立文件夹，保存文档并关闭旧软件，运行包内的 `安装.exe`，选择旧安装目录和原始更新 ZIP。工具检查原版本及文件校验值，只替换清单中的程序文件，并在原安装目录旁备份被替换的文件。用户文档、设置、运行时和识别组件均不参与更新。

原版本文件不匹配（包括修改过源码或使用不同构建的同版本包）时会停止更新，应使用主程序安装包升级。Python 运行时发生变化的版本也会要求使用主程序安装包，不生成误导性的小更新包。

## 构建与发布

先保留上一版本的程序清单：

```powershell
python tools/build_split_release.py --source build/release/MarkdownReader --out release --snapshot previous-program.json
```

准备已验证的照片和扫描组件后，构建完整离线包、主程序包、组件包，以及可选的更新包：

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -SkipRuntime -SplitPackages -BaseManifest previous-program.json
```

`components.json` 随主程序提供，固定组件 URL、大小、内容标识和校验值；三个组件的文件名由内容决定，与主程序版本独立。首次发布须上传清单引用的组件包。后续内容未变的组件保留原发布地址，避免重复上传和下载。当前本地构建不会自动上传；组件尚未发布时，在线下载会提示失败，仍可导入本地组件包。

本地验证结果：精简主程序包约 97.6 MB（相比原 1764.4 MB 缩小约 94.5%），0.4.12 → 0.4.13 更新包约 0.81 MB；照片组件约 115 MB、扫描引擎约 155 MB、扫描模型约 1397 MB。精简包导入三个组件后，实际照片和扫描 PDF 识别均通过；实际 Windows 更新入口验证了程序升级及用户文档、运行环境和模型保留。
