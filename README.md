<div align="center">

<img src="assets/icon.png" width="112" alt="MDReader">

# MDReader

**简体中文** | [English](README.en.md)

**Windows 上的本地 Markdown 阅读器 + 项目整理工具。**
阅读排版、多标签编辑、公式、表格、导出 Word/PDF，阅读、编辑和格式转换均可离线运行；可选 AI 对话需要联网。

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-3A4254)
![python](https://img.shields.io/badge/python-3.9%2B%20(3.12%20tested)-3776AB)
![license](https://img.shields.io/badge/license-MIT-8B6AEB)

</div>

---

## 0.4.2：任务栏图标修复

正式版现在使用独立 Windows 应用身份。无论从安装快捷方式启动还是直接运行 `MDReader.exe`，运行中的窗口固定到任务栏后均使用紫色 MD 图标，并通过 `MDReader.exe` 重新启动。

**已有 Python 图标的用户**：保存并关闭旧版，安装 0.4.2，取消固定旧的 Python 图标，打开新版后重新固定。旧固定项不会被程序自动改写。源码开发版使用独立身份，避免与正式版混组。

## 一、下载就能用（推荐）

到 [Releases](../../releases) 下载 `MarkdownReader-<版本>-win64.zip`，解压后双击 **`MDReader.exe`** 即可。
**不需要安装 Python，也不需要联网**（导出 Word/PDF 用的插件与依赖都在包里）。
完整安装包作为 Release 附件提供（大小以附件显示为准），不存放在 Git 源码历史中。

**AI 接入**：点击顶部「AI 接入」（位于「浏览器视图」与「更多」之间），选择 **GPT / Claude / DeepSeek Flash**，粘贴 API Key，点击「测试连接」即可在软件里对话。密钥在本机加密保存；可选择附上当前文档、复制回复或插入编辑器。详见 [模型接入说明](docs/AI_MODELS.md)。更新正在运行的测试版后，保存文档并重新打开程序即可看到新界面。

请以对应 Release 的附件为准，避免使用旧版本安装包。

| 你要做的事 | 怎么做 |
| --- | --- |
| 打开软件 | 双击 `MDReader.exe` |
| 打开一篇 .md | 把文件拖到 exe 或窗口里；或 `Ctrl+O` |
| 在桌面留个快捷方式 | 双击 `安装到桌面.cmd`（也可右键 `安装到桌面.ps1` → 使用 PowerShell 运行） |
| 卸载 | 右键 `卸载.ps1` → 使用 PowerShell 运行（默认保留你的文档） |

你的文档默认放在 `%USERPROFILE%\MDReader`，卸载不会删除。

安装无需管理员权限或预装 Python。请下载 **Releases 中的 win64.zip**，不是 GitHub 的 Source code 压缩包；完整解压后再运行。
安装窗口提供两个“浏览文件夹”：**程序安装目录**和**配置与数据目录**（主题设置、历史记录、项目等）。可浏览或手动输入路径，点击“安装”生效；取消不会安装。
两个目录不能相同或互相包含。所选数据位置保存在程序目录的 `installation.json` 中，桌面入口和直接运行 EXE 都会使用它。选择新的数据目录不会自动迁移旧文档；选择原目录可继续使用原记录。卸载默认保留数据。
自动化安装可使用 `安装到桌面.ps1 -InstallDir D:\Apps\MDReader -WorkspaceDir D:\Documents\MDReader -NoPause`，`-NoUI` 可跳过目录选择窗口。
安装失败时窗口会保留错误信息。升级前先保存并关闭软件；旧安装会备份到旁边的 `.backup-*` 目录，项目和历史记录继续保留。
`安装到桌面.cmd` 只为本次进程设置脚本执行策略，不修改系统长期设置；企业策略仍可能阻止执行。
如果系统提示下载文件被阻止，请先核实下载来源，再在压缩包属性中选择“解除锁定”（若有），重新解压。
未签名程序可能出现 Windows 安全提示；不要关闭杀毒软件或系统防护。

## 0.4.0 更新与新增操作

- **AI 对话与改文档**：点击顶部「AI 接入」，选择服务商、填写 API Key，点击「保存并获取模型」并「测试连接」。输入问题后点击「发送」或按 `Ctrl+Enter`。需要修改文章时勾选「附上当前文档」，查看结果后点击「应用修改」，再按 `Ctrl+S` 保存。桌面源码中可按 `Ctrl+Z` 撤销；浏览器助手提供「撤销此次修改」。生成期间切换文档或继续编辑会阻止过期结果覆盖新内容。测试和对话需要联网，可能产生服务商 API 费用。详见 [模型接入说明](docs/AI_MODELS.md)。
- **外部 AI 控制**：在「更多 → 外部 AI 控制接口（高级）」开启本机服务，复制接入信息给支持本机 HTTP 工具的助手；需要编辑时另行开启「允许 AI 修改文档」。此密钥与模型 API Key 不通用。详见 [控制接口说明](docs/AI_API.md)。
- **离线格式转换**：在「更多 → 插件管理」安装并启用随包的 `mdreader.document-convert-1.0.0.zip`，再到「更多 → 文档格式转换」选择 PDF / DOCX → Markdown 或 Markdown ↔ HTML、源文件及新输出路径。先保存当前编辑；转换读取磁盘文件，不修改原文件，不覆盖已有目标。PDF 扫描件不支持 OCR；图片附件目录需与结果一起保留。此功能目前仅有桌面入口，要求宿主 0.4.0 及以上。详见 [转换说明](plugins/official/document-convert/README.md)。
- **查找与定位**：`Ctrl+F` 输入即统计并高亮全部匹配，可循环前后查找或直达首尾；正文选中文字后按 `Shift+M` 可在预览与源码之间定位。无选区时恢复各视图的阅读位置，每篇文档分别记忆。

**0.4.1 维护更新**

- 工具栏新增「导出 Word/PDF」，可按提示安装或启用所选格式插件，导出后直接打开产物或文件夹。随包 Word/PDF 插件为 1.0.4，改进公式、表格、列表与长代码排版；已有旧插件时请在插件管理中安装随包新版并启用。
- 正文预览/源码定位快捷键改为 `Shift+M`，项目树重命名仍为 `Ctrl+M`。

- **AI 选区修改（B01）**：在桌面窗口里选中文字，打开「AI 接入」后点「润色 / 精简 / 扩写 / 自定义要求…」，看完「原文 → 建议」差异再点「应用建议」——只替换选中的范围，范围外一字不动，一次应用对应一次撤销，且不会自动保存；点「取消建议」正文不变。默认只把选中的文字发给 AI，需要更多上下文时在「发送范围」里显式选择「选区所在段落 / 整篇文档」，界面会显示实际发送的字数与字符区间。请求发出后若文档被切换或改动，旧建议会被拒绝应用。首版只有桌面入口，网页端仍是整篇修改。详见 [选区修改说明](docs/B01_SELECTION_EDIT.md)。状态：自动窗口测试通过，人工真实窗口验收待做。

**从 0.3.0 / 0.4.0 升级**：保存文档并关闭软件，下载本次 Release 的 `MarkdownReader-0.4.2-win64.zip`，完整解压后运行安装脚本，选择原安装目录及原数据目录。新增转换插件需在插件管理中安装并启用。仅替换旧版插件 ZIP 不会更新宿主功能。

## 二、它是什么

MDReader 把「读」放在第一位：Markdown 会排版成适合长时间阅读的样子（三种主题、任意字号、目录导航、查找），
同时把散落各处的 `.md` 收进「项目 / 我的文件夹」里管理。阅读、编辑、整理和转换在本机完成。可选 AI 对话会将问题发送给所选服务商；只有勾选「附上当前文档」时才附带当前文档。

**阅读**
- 三种配色（明亮 / 夜间 / 护眼），字号随 `Ctrl+滚轮` 调整，网页版与桌面版共用同一份设置
- 标题导航（`Ctrl+L`）、文档内查找（`Ctrl+F`）、双击标题重命名、`Ctrl+Y` 查看完整路径
- 表格自动换行、代码块高亮、脚注与 Mermaid 至少保证文字不丢失（见「已知限制」）
- 行内 `$…$` 与独立 `$$…$$` 公式由程序用 GDI 自己画成图片，不依赖 KaTeX、不联网

**编辑**
- 原生窗口多标签：每份文档独立保留编辑内容、撤销历史、光标与阅读位置
- UTF-8 / BOM / UTF-16 / GB18030 读得进来也写得回去；保存前校验磁盘内容指纹，冲突时要求你选「另存为 / 备份后覆盖」
- 停止输入约 2 秒写恢复快照，异常退出后重启可选择恢复
- 中文输入法：内嵌拼音组合浮层与主题同色，候选词栏排在拼音下方，不压字

**整理**
- 项目与「我的文件夹」两种组织方式；新建 / 导入 / 重命名 / 移动 / 删除（进回收站）
- 最近打开支持按文件名与路径即时搜索
- 临时查看：把 `.md` 拖进窗口即可编辑原文件，`Ctrl+S` 直接写回原路径

**导出（离线插件）**

- 桌面工具栏「导出 Word/PDF」可直接选择格式；缺少或停用插件时，可按提示安装随包插件或启用。无需配置 AI。
- 完成后可直接打开产物或所在文件夹，并查看本次导出的文档名称与降级提示。
- Markdown → Word（.docx）、Markdown → PDF、图片插入与等比例缩放
- 导出在独立工作进程里跑，主界面不卡；目标已存在时必须确认，不静默覆盖
- 插件源码与安装包位于 `plugins/` 文件夹中。

## 社区插件

新插件与社区插件开发请查看主仓库的 [Plugins 分支](https://github.com/Jiko-Hyper/MarkdownReader/tree/Plugins)。欢迎 Fork 仓库，在自己的副本中修改或添加插件，再提交以 `Plugins` 为目标分支的 Pull Request。维护者审核后合并；公开仓库不允许陌生人直接覆盖代码。详见 [贡献指南](CONTRIBUTING.md)。

注意：分支中的插件不一定已被当前发行版信任或支持。请查看插件说明与兼容版本，只有通过受信清单校验的插件包才能安装。

## 三、快捷键

在软件里点「⋯ 更多 → 快捷键说明」也能看到同一张表。

| 快捷键 | 作用 |
| --- | --- |
| `Ctrl+N` | 新建临时文档（未保存前标题带 `#`） |
| `Ctrl+O` | 打开本地文件 |
| `Ctrl+S` | 保存；临时文档写回原文件 |
| `Ctrl+W` | 关闭当前标签 |
| `Ctrl+Tab` | 切换标签 |
| `Ctrl+E` | 预览 / 源码 切换 |
| `Shift+M`（正文） | 桌面版：选中文字后切换预览 / 源码并定位对应文字 |
| `Ctrl+Home` / `Ctrl+End`（正文） | 桌面版：光标移到文章开头 / 末尾 |
| `Ctrl+B` | 显示 / 隐藏侧栏 |
| `Ctrl+F` | 文档内搜索：自动统计并高亮全部匹配，支持循环前后查找与首尾直达 |
| `Ctrl+L` | 标题导航（目录） |
| `F5` | 刷新当前文档 |
| `Ctrl+滚轮` | 调整正文字号 |
| `Ctrl+Shift+T` | 在光标处插入表格 |
| `Ctrl+Shift+M` | 插入行内数学公式 |
| `Ctrl+Shift+V` | 把剪贴板截图插到光标处（需图片插入插件） |
| `拖入窗口` | 阅读并修改 `.md` 原文件；拖入图片则插到光标处 |
| `Delete` | 项目树：删除选中的文件或文件夹（需确认） |
| `Ctrl+M` | 项目树：更改选中目标的标题 |
| `双击标题` | 项目树：更改标题并同步本地名称 |
| `Ctrl+Y` | 项目树：查看选中目标的文件地址 |
| `Enter` | 项目树：打开选中的文档 |

桌面版中，双击或拖选正文后按 `Shift+M` 可双向定位。没有选区时，返回预览或源码会恢复该视图上次的阅读位置；每篇文档分别记忆，正常退出后也会保存。

`Ctrl+F` 搜索框与当前主题一致，居中打开，可拖动标题栏移动，暂不支持缩放。输入即搜索并跳到首个匹配；下方按 `goto the First`、`back`、`next`、`goto the Last` 排列，前后按钮到达边界时循环跳转。再次按 `Ctrl+F`、点击右上角关闭、按 Esc 或点击搜索框外即可退出；正文不会被搜索操作修改。刚启动且未打开文档时也能弹出搜索框，并提示先打开文章。

## 四、从源码运行

需要 Python 3.9+（本轮验证 3.12），原生窗口需要 tkinter。标准库即可启动；若装了
`markdown-it-py` 会用它渲染，否则回退到内置解析器。

```powershell
python main.py                        # 桌面窗口（WebView2 可用时用内嵌视图）
python main.py --browser              # 只用系统浏览器
python main.py --workspace D:\notes   # 指定工作区
python main.py --open 说明.md          # 启动时打开一个文件
python main.py --selftest             # 自检：窗口能否创建并渲染
```

`MDReader.exe` 就是 `runtime\pythonw.exe main.py` 的外壳，所以源码目录和发布目录的行为完全一致。

## 五、目录结构

```text
MarkdownReader/
├─ main.py                 程序入口（MDReader.exe 就是它的外壳）
├─ mdreader/               全部 Python 源码
│  ├─ core.py              工作区、项目、HTTP 接口
│  ├─ winui.py             原生 tkinter 窗口（阅读 / 编辑 / 项目树）
│  ├─ launcher.py          启动选择：WebView2 → 内置窗口 → 浏览器
│  ├─ render.py            两套 Markdown 渲染引擎
│  ├─ formula.py           公式离线绘制（GDI）
│  ├─ tables.py            表格识别与插入
│  ├─ exporting.py         统一导出服务（预检、快照、原子写）
│  ├─ plugins*.py          插件状态机、工作进程、界面
│  ├─ appicon.py           运行时自绘应用图标（GDI+）
│  ├─ ime.py / native_bridge.c   中文输入法与原生消息桥
│  └─ ...
├─ webui/                  内置网页界面（HTML/CSS/JS）
├─ plugins/                官方离线插件：源码、插件包、随包依赖
├─ assets/                 应用图标（紫色 MD 标识）
├─ release/app/            发布包里的启动器与安装脚本源
├─ release/                GitHub Release 用的 zip（打包产物）
├─ tools/                  构建、验收、基准脚本
├─ tests/                  自动化测试
└─ docs/                   架构、开发、验收记录与使用说明
```

发布包（解压后的目录）在源码之外还多两样东西：`MDReader.exe` 外壳和 `runtime\`
（Python 运行时 + tkinter + 插件依赖）。

## 六、自己打包发布

```powershell
# 1) 一次性准备：Python 3.12（含 tkinter）；MinGW-w64（gcc，建议同时提供 windres）
python -m pip install --user pillow     # 只在重新生成图标时需要

# 2) 打包：准备运行时 → 编译 exe 外壳 → 组装目录 → 自检 → 压缩
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1
```

产物：

```text
build\release\MarkdownReader\            解压即用的完整程序
release\MarkdownReader-<版本>-win64.zip   上传 GitHub Release 的包
```

脚本会依次执行 `tools/prepare_runtime.ps1`（下载 Python embeddable、补 tkinter、
补插件依赖）与 `tools/build_launcher.ps1`（gcc + windres 生成带图标和版本信息的 exe），
最后在发布目录里跑三项自检：`main.py --version`、`MDReader.exe --selftest`
（真实创建窗口并渲染）、`tools/smoke_release.py`（装好并启用随包插件）。

当前完整打包脚本需要 gcc 来生成 EXE；没有编译器时可以直接从 Python 源码运行。`启动 MDReader.bat` 仍依赖发布包中的 `MDReader.exe`。

打不开、闪退时，在发布目录里执行下面这条能看到完整报错：

```powershell
MDReader.exe --console
```

## 七、开发与验证

```powershell
powershell -ExecutionPolicy Bypass -File tools/scripts/check.ps1   # 单元测试 + JS 语法 + 启动检查
python tools/scripts/acceptance.py                                 # 真实窗口验收（会弹窗，需人工看）
python tools/scripts/bench_interaction.py                          # 交互性能基准
```

- 架构与模块边界：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 开发、测试与发布流程：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- 版本计划与验收记录：[docs/ROADMAP.md](docs/ROADMAP.md)
- 后续产品定位、开发优先级与验收标准：[优势导向开发指南](docs/PRODUCT_DEVELOPMENT_GUIDE.md)
- 交付样本与导出验收结论：[A01 交付样本与验收报告](docs/A01_DELIVERY_REPORT.md)
- 用户说明（含已知限制）：[docs/USAGE.md](docs/USAGE.md)

## 八、已知限制

- 只支持 Windows 10/11 x64；界面语言为简体中文。
- 内置解析器是回退实现，不等同于完整 CommonMark；脚注与 Mermaid 只保证文字不消失，
  公式支持固定的 TeX 子集（22 条样本，见 `docs/USAGE.md`）。
- 大于 8 MB 的文件与二进制文件拒绝打开。
- 网页端「导出」与「打开本地文件」需要会话凭据；工作区之外的文件必须先经「打开本地文件」授权。

## 九、开源协议

[MIT](LICENSE)。随包分发的 Python 运行时来自 python.org（PSF 协议），
插件依赖（Pillow / markdown-it-py / python-docx / lxml / reportlab）各自遵循其原始协议。

---

# 开发者的话

- 本人目前是大学生，开发这个软件纯属兴趣爱好，为爱发电，不为任何商业活动。
- 目前这个软件在很多地方都不成熟，但日常使用是没有问题的。
- 已接入 AI API，之后会继续完善多语言与日常体验。
- 大家在日常使用中遇见任何问题都可以在下方留言，当然你有新颖的点子欢迎留言。
- 现有插件包括 Word/PDF 导出、图片插入及文档格式转换，欢迎贡献新插件。
