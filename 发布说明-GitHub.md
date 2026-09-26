# 上传 GitHub 前请先看这一页

这个文件夹就是可以直接推到 GitHub 的仓库根目录（也可以把它当成仓库里的一个子目录）。

## 已经准备好的东西

| 内容 | 位置 |
| --- | --- |
| 全部源代码 | `main.py`、`mdreader/`、`webui/` |
| 免安装发布包（含 exe） | `release/MarkdownReader-0.3.0-win64.zip`（约 67 MB，已压缩） |
| 应用图标 | `assets/icon.png`、`assets/icon.ico` |
| 官方离线插件 | `plugins/`（源码 + 可安装包 + 运行依赖） |
| 构建与验收脚本 | `tools/`（打包脚本在 `tools/build_release.ps1`） |
| 自动化测试 | `tests/`（677 项 Python + 63 项 JS） |
| 文档 | `README.md`、`CHANGELOG.md`、`docs/`、`tools/README.md` |
| 协议 | `LICENSE`（MIT） |
| CI | `.github/workflows/build.yml`（推 tag 时自动打包并发布 Release） |

## 上传步骤（命令行）

```powershell
cd MarkdownReader
git init
git add .
git commit -m "MDReader 0.3.0: 正式版，免安装 exe + 源码 + 插件"
git branch -M main
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

想走 CI 自动发布：打一个 `v0.3.0` 的 tag 再推上去，Actions 会在
windows-latest 上跑测试、打包并把 zip 挂到 Release 上。

```powershell
git tag v0.3.0
git push origin v0.3.0
```

## 体积说明

- 源码 + 插件包 + 依赖：约 126 MB（`plugins/packages` 与 `plugins/dependencies` 占大头）。
- 发布 zip：约 67 MB。GitHub 单文件上限 100 MB，这个包可以直接提交进仓库；
  如果你不想让仓库带二进制，把 `release/*.zip` 从提交里排除，只走 Release 附件。
- `build/` 是构建中间产物（已写进 `.gitignore`），不必上传；重跑
  `tools/build_release.ps1` 就能再生成一遍。

## 发布包长什么样

`release/MarkdownReader-0.3.0-win64.zip` 解压后：

```text
MarkdownReader/
├─ MDReader.exe            双击即用（自带运行时的外壳，约 100 KB）
├─ runtime/                Python 3.12 + tkinter + 插件依赖（约 61 MB）
├─ main.py  mdreader/  webui/    程序本体（就是源码，可直接阅读修改）
├─ plugins/                官方插件包与依赖
├─ assets/ docs/ tools/ tests/   图标、文档、脚本、测试
├─ 启动 MDReader.bat       备用入口（没有 exe 时也能启动）
├─ 安装到桌面.ps1          建桌面快捷方式（可选 .md 打开方式）
├─ 卸载.ps1                卸载（默认保留 %USERPROFILE%\MDReader）
└─ 使用说明.md  VERSION.txt
```

打包脚本在发布目录里跑过三项自检，全部通过才会生成 zip：
`main.py --version`、`MDReader.exe --selftest`（真实创建窗口并渲染）、
`tools/smoke_release.py`（安装并启用三个随包插件）。

安装包通过 GitHub Release 附件分发，`release/*.zip` 不加入 Git 历史。
每次重新打包后请重新计算 SHA256，不能沿用旧包的校验值。

社区新插件在主仓库的 `Plugins` 分支维护，欢迎 Fork 后提交目标为 `Plugins` 的 Pull Request。
