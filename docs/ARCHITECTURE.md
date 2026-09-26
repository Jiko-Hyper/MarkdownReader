# 架构与模块边界

## 当前可运行结构

```text
build/main.py                       程序入口
  mdreader/launcher.py              启动参数、窗口选择、服务生命周期
    winui.py                       tkinter 原生界面（FileDrop 接收系统拖放）
    document_ui.py                 原生端的保存安全、冲突处理、查找/目录/阅读位置
    webui/index.html + app.js/css   WebView2 / 浏览器界面
      core.py: Api / Handler       本地 HTTP 适配（会话凭据、Host/Origin 校验）
      core.py: Workspace           项目、文档、搜索、导入导出用例
        documents.py               内容指纹、编码/换行识别、带冲突检测的写入
        recovery.py                有界恢复快照
        content_policy.py          阅读/导出/网页共用的内容白名单
        core.py: LooseDocs         临时查看：直接读写工作区之外的文件（需授权）
        folders.py                 用户原文件夹：登记、扫描、后台增量轮询、独占创建
        plugins.py                 插件机制：清单校验、受信清单、安装/启停/回退、命令注册
        plugin_tasks.py            插件任务：独立工作进程、进度/取消/超时、结果复核与提交
        storage.py                 路径边界、原子写入
        render.py                  Markdown 排版和离线 HTML 样式
    plugin_ui.py                   原生端的插件管理窗口（启用/禁用/卸载/导出格式选择）
    plugin_worker.py               插件工作进程入口（只回 JSON Lines 协议消息）
    plugin_trust.json              随程序分发的受信插件清单（id + 版本 + SHA256）
    ime.py                         输入法组合显示（组合串由 native_bridge 采集）
    native_bridge.c / .dll         C 侧窗口子类，避免 Python 回调重入
tests/                             Python、界面接线与编辑器 JavaScript 回归测试
scripts/check.ps1                  统一验证入口
docs/                              架构、开发流程与迭代清单
```

`native_bridge.dll` 是随程序分发的本机组件（源码同目录 `native_bridge.c`，用
`scripts/build-native.ps1` 重新编译）；缺少它时输入法组合显示退回到系统默认外观，
其余功能不受影响。

正文仍是磁盘上的普通 Markdown。`workspace.json`、`project.json` 与
`recent.json`（最近打开的临时文档）保存辅助信息。原有工作区结构、API 路径与
`mdreader` 对外导出保持兼容。

## 模块约定

1. 界面负责展示和用户交互，文档写入交给 Workspace 或 LooseDocs。网页通过 API
   调用，原生界面沿用现有直接调用方式。
2. Workspace 负责业务用例；文件写入统一走 `storage.atomic_write`，限定目录的路径
   统一走 `storage.safe_join`。
3. storage 不引用界面或 HTTP；临时文件与目标同目录、使用唯一名称，写入完成后
   替换原文件，失败清理临时文件并抛出异常。**不使用 `tempfile.mkstemp`**：
   它在创建失败时会无限重试，会把「目录不可写」变成看似卡死。
4. render 只负责文本与 HTML，不应修改工作区。当前仍有原始 HTML 支持，正式处理
   不可信文档前需要统一消毒。
5. 每个 HTTP 服务实例拥有自己的 API 与工作区：`serve()` 为每个服务器生成独立的
   Handler 子类，外部通过 `core.api_of(httpd)` 取回，不共享可变的类级绑定。

## 临时查看（工作区之外的文件）

- `LooseDocs` 的文档 id 就是**规范化后的绝对路径**（草稿用 `draft:N`），
  保存即写回原文件，不复制进工作区；`recent.json` 只记录路径与名称。
- 本地 HTTP 服务只监听回环地址，读写这些路径等同于用户在本地文件对话框里
  亲自选择文件。相邻图片通过 `/api/localfile` 读取，且只允许该文档所在目录内。
- 不存在的路径、目录、非 Markdown 扩展名、超过 8 MB 的文件一律拒绝并给出原因。
- 移除最近记录**只移除条目**，永远不删除用户的文件。

本轮是渐进拆分。`core.py` 仍包含业务与传输代码；后续按功能迁往
`workspace.py`、`http_api.py`、`exports.py`，以兼容导出保留已有调用，
避免一次大搬迁影响桌面启动。

## 保存、冲突与恢复

写入统一走 `documents.write_checked(path, text, expected, overwrite, backup_dir)`：先按内容
哈希（不是修改时间）核对调用方持有的版本，不一致就抛 `ConflictError` 并且**不碰磁盘**；
一致时按原文件的编码与换行写回，覆盖分支会先把磁盘版本原子写入
`.recovery/conflicts`。工作区文档和临时查看共用这一条路径，两端的界面只负责把
「重新加载 / 另存为 / 备份后覆盖」的选择交回业务层。

`recovery.RecoveryStore` 按身份（原路径或草稿 id）保存未保存内容的快照，单条、条数与
总量都有上限；快照损坏只报告并保留文件，不阻止启动。

## 内容安全边界

`content_policy.sanitize` 是阅读、导出和网页共用的白名单：只放行文档需要的标签，
`href`/`src` 仅接受 http/https/mailto/相对路径与 `data:image`，远程图片不自动加载，
样式只允许 `text-align`。本地 HTTP 服务另外要求会话凭据并校验 Host/Origin；
工作区之外的文件必须先经「打开本地文件」授权（`selected_paths`/`recent.json`）才能读取。

## 保存与删除语义

- 原子替换防止半文件和临时文件重名；内容指纹与冲突检测见上一节，但仍不宣称支持外部程序不配合锁的严格并发编辑。
- 网页保存失败时，“保存并切换”停留当前文档或项目；保存期间继续输入时保留新输入。
- 默认删除仅请求回收站。回收站不可用则报错并保留文件，不自动改为永久删除。显式 `recycle=false` 仍保留原有永久删除接口。
- 路径工具解析符号链接及 Windows junction 后检查边界；这不是对其他进程并发替换目录的完整隔离措施。

## 标签、编辑控件与视图（0.2.8 / E01）

原生窗口的正文区是**同一网格单元里叠放的一排控件**：一份只读预览控件，加上**每份打开
文档各一个源码编辑控件**（`MarkdownWindow.editor_for(tab)` 按需创建，`tab["editor"]` 持有，
关闭标签时 `_release_editor` 销毁并注销输入法适配项）。切换标签或切换「预览 / 源码」只做
「显示哪一个 + 聚焦」，不重写任何控件的内容——这正是 Tk 撤销栈能跨视图、跨标签存活的原因：
整段 delete/insert 或 `edit_reset()` 会清空历史，所以它们只用于**真正重新加载一份文档**
（`_load_editor`）。

`self.text` 始终指向**屏幕上**那个控件（预览或当前编辑控件），`self.editor` 指向当前文档的
编辑控件；`_sync_view()` 负责让屏幕、`self.text` 与 `self.mode` 三者一致（没有文档时强制回到
只读预览空状态）。`stash_tab()` 记录屏幕上的滚动位置和编辑控件的光标，`activate_tab()` 还原它们。
保存基线与撤销栈分开：`mark_baseline`/`at_baseline` 用内容哈希记录「已保存」参照，`on_modified`
按「是否回到基线」决定未保存标记，于是撤销回保存内容会去掉标记、重做会恢复标记。

## Windows 输入法组合显示

`mdreader/ime.py` 用 `native_bridge.c` 编译出的 `native_bridge.dll` 在 C 侧为每个顶层窗口安装窗口子类并采集组合串；窗口内所有可编辑控件（每个文档的编辑控件、预览控件与侧栏搜索框）注册到同一个钩子，组合内容只交给当前拥有 Tk 焦点的那个控件显示。用 Python 回调做子类会在 `DefSubclassProc` 里重入 Python 并破坏解释器状态，所以这里不装任何 Python 回调。

**每个顶层窗口只有一个轮询循环**（`WindowHook._loop`，16 ms）：它调用一次 `poll()` 读取组合串，再逐个调用已注册控件的 `_tick()` 完成绘制与候选框定位。标签多时若每个控件各跑一个定时器，原生桥调用次数会随标签数成倍增长，而只有拥有焦点的那个控件才可能有组合串，因此定时器收归钩子所有。控件被切走（切标签、切到预览）时 `_show()` 会调用其 `_focus_out()` 清掉组合状态，避免在隐藏控件上留下拼音浮层。

**谁在真正处理 Windows 消息**：只有 `native_bridge.c` 里的子类过程。Python 侧不安装任何回调，`InlineIME.handle_message` / `WindowHook.handle_message` **已不在生产路径上**（保留为平台无关的协议表述，由 `tests/test_ime.py::ImeProtocolTests` 覆盖；其 docstring 已标注，请勿把它当作运行中程序的行为证据）。C 侧只在 `s->active` 为真时剥掉 `ISC_SHOWUICOMPOSITIONWINDOW`，而 `s->active` 由 Python 每帧的 `native.active(wanted)` 驱动，`wanted` 取决于"屏幕上那个控件的适配项是否 `editable`"。因此**可编辑性错误 = 系统白色组合框回来**：这正是 0.2.8 的一次真实回归（E01 重构后没人再设置该标志），修复方式是把它收敛到 `winui.py::_show()`，回归用例见 `tests/test_native_tabs.py::test_every_tab_editor_is_composition_ready_without_manual_setup`（断言真实桥被要求采集/停止采集）。

只隐藏默认组合框，不隐藏候选词栏；读取 IMM 组合串后交给同主题、同字体的 Canvas 显示。Canvas 是编辑控件的兄弟，不是正文中的嵌入控件，未确认文字不参与保存、统计或撤销。`GCS_RESULTSTR` 仅转发一次给 Tk，不自行插入；UTF-16 光标位置转换成 Python 字符索引。

候选位置由 Tk 定时器负责：把组合矩形（`CFS_RECT`）和四个候选列表的左上角（`CFS_CANDIDATEPOS`，取拼音框底边加 2 像素）写给输入法，候选词栏因此排在拼音下方而不是压在上面。坐标一律是顶层窗口客户区像素——Tk 的 `Tk_SetCaretPos` 就是按这个空间关联 IMM 的。

浮层摆放**不能**用 `place(in_=编辑器)`：Tk 的 `-in` 是在控件内边距之内计算的，而 `bbox()`/`dlineinfo()` 用的是控件自身坐标，两者相差 `padx`/`pady`，会让拼音整体低一行、右一列。现在改为在父容器坐标里摆放（编辑器 `winfo_x/y` 加 `bbox` 偏移），并用 `tests/test_ime.py` 的几何用例锁住。

两条硬性约束写进结构里：Win32 回调内既不能调用 Tcl/Tk，也不能调用 imm32 的设置函数（两者都会同步重入已入栈的代码），回调只记录待显示内容与「位置已过期」标记，绘制和定位交给 Tk 定时器；控件销毁或钩子失败时移除子类回调，并恢复原生输入外观。文本输入法候选栏的颜色与具体行为由输入法提供，程序不修改系统输入法设置。

参考：[组合窗口控制](https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-setcontext)、[组合与确认消息](https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-composition)、[CANDIDATEFORM](https://learn.microsoft.com/en-us/windows/win32/api/imm/ns-imm-candidateform)、[窗口子类](https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-setwindowsubclass)。


## 插件机制（P01）

插件机制只覆盖本阶段真正需要的两类能力：`editor.image_insert` 与 `export.format`。它不是
通用 SDK，也没有事件总线或任意界面代码扩展；插件提供的 HTML/界面字段在清单校验阶段就会被
拒绝（`plugins.MANIFEST_KEYS` 是白名单，未知键直接判清单损坏）。

```text
mdreader/plugin_trust.json          随程序分发的受信清单：id → 版本 → 包 SHA256
mdreader/plugins.py                 PluginStore：安装、启停、升级回退、卸载、命令注册、结果复核
  plugins/installed/<id>/<ver>/     解包后的插件（根目录必须有 manifest.json）
  plugins/cache/<id>/               插件专属缓存（卸载时只删这里和 installed）
  plugins/tasks/<task id>/          每个任务的独立工作目录（插件唯一可写的地方）
  plugins/staging/                  暂存区：全部校验通过后才改名进 installed
mdreader/plugin_tasks.py            TaskManager：起进程、读协议、取消/超时、提交产物
mdreader/plugin_worker.py           工作进程入口：加载插件模块，只回 JSON Lines
<workspace>/plugins.json            两端共用的启用状态（宿主统一保存）
plugins/official/<kind>/            三个正式插件的源码：image-insert、export-pdf、export-docx
plugins/official/common/exporting.py 两个导出插件共用的 Markdown → 文档结构转换与字形覆盖
plugins/packages/*.zip              确定性打包的安装包（哈希由 scripts/build_official_plugins.py 登记进受信清单）
plugins/dependencies/               随包分发的离线依赖：reportlab、python-docx、Pillow、markdown-it、lxml
```

**导出时的字形覆盖（2026-09-25 修）**：中文正文字体（SimSun / 黑体 / 微软雅黑）都没有数学减号
`U+2212` 与上下标 `U+2076 ⁻`、`U+2081 ₁` 这类字形，而 ReportLab 对没有字形的字符仍会照画一个空的
`.notdef`——导出稿上就成了一块空白（维护者试用正式插件时反馈的「转换后出现乱码」）。`exporting.py`
现在自己读字体文件的 `cmap`（只认 Unicode 子表，支持 format 0/4/6/12），逐字符判断谁有字形：基础
字体画不出来的改用 Times New Roman / Cambria / Segoe UI Symbol；Word 侧用同一套判断拆 run 并显式
写出回退字体，PDF 侧用 `<font>` 包住那几段。两边都没有字形时导出照常完成，但结果里带一条提示，列出
受影响的字符，不假装成功。之所以不用 ReportLab 自带的 `charToGlyph`：DOCX 插件不装 ReportLab，两个
插件必须共用同一套判断；`tests/test_official_plugins.py` 用 fontTools 作为独立实现逐码点核对这套读取。

**发现与执行分开**：`list_plugins`/`commands` 只读磁盘上的清单，不导入任何插件代码；只有
`TaskManager.submit` 才会起 `python -m mdreader.plugin_worker`，插件模块只存在于那个进程里
（用例断言宿主 `sys.modules` 中不会出现 `mdreader_plugin_*`）。任务绑定文档身份、缓冲修订与
调用入口；产物必须落在任务工作目录内，导出还要核对清单声明的扩展名、文件头与大小上限，然后
由核心（`Api._plugin_insert` / `Api._plugin_export`）负责写附件或原子替换目标。桌面窗口不另写
一套：`MarkdownWindow.plugin_bridge()` 直接复用一个 `Api` 实例，所以两端规则同源。

**失败与状态**：安装先做归档检查（越界、链接、加密、大小、重复路径、清单）与受信哈希比对，
再解包到暂存区；升级前做兼容检查，失败保留上一版本，启用握手失败时自动回退且不复活用户禁用
过的插件。禁用会取消该插件正在跑的任务，服务端随后拒绝它的命令。卸载只删 `installed/<id>`
与 `cache/<id>`，动手前用 `is_within(plugins_dir, …)` 复核。

## 表格与格式规则（F04）

表格与常用格式的**内容规则**放在核心，界面只负责收发：桌面端 `import` 这两个模块，
网页端走 `POST /api/edit/table` 与 `POST /api/edit/format`，把缓冲区与选区交给服务端
算完再拿回新正文与新选区。两个接口是**纯计算**：不读工作区、不写磁盘（用例断言调用
前后工作区目录列表不变）。

```text
mdreader/tables.py        管道表格的识别（跳过围栏代码块）、可修改性核对、结构操作与序列化；
                          TSV 的矩形解析与粘贴（列数不一致补空并警告）
mdreader/formatting.py    包裹类（粗体/斜体/行内代码/链接，再点一次取消）与行级类
                          （标题、无序/有序列表、引用、代码块）的文本规则
mdreader/core.py          POST /api/edit/table、POST /api/edit/format（正文上限 8 MB）
mdreader/winui.py         格式工具栏、标题下拉、TableDialog、TablePasteDialog、粘贴接管
webui/{index.html,app.js} 源码模式下的格式栏、表格弹层与粘贴预览（同一批规则的服务端调用）
```

**为什么放在核心**：只要两端各写一份“什么算表格、`|` 怎么转义、列表怎么加前缀”，
就一定会漂移。现在两端的差别只剩“怎么把结果写回控件”（Tk 用一次 `replace` 记成
一个撤销单元，网页端写 `value` 并派发一次 `input`）。识别不了的结构（列数对不上、
各行首尾竖线不一致、围栏代码块里的伪表格）一律**拒绝修改并给出原因**，界面保留源码
——`review()` 是这条规则的唯一出口，反向验证的第一项就是它。

## 公式渲染（F05）

公式**不引入第三方渲染器**：`mdreader/formula.py` 把 TeX 子集排成版面盒子，再用 Windows
GDI 画进位图、自己编码 PNG；桌面预览用 Tk 的 `PhotoImage` 读它（不需要 PIL），网页与导出
用同一张图。四条出口（桌面、网页、自包含 HTML、PDF/Word 用的图片）因此只有一套结果要验证。

```text
mdreader/formula.py       识别（scan）、语法分析、版面、渲染（to_png）、缓存（cache_key/cached）
mdreader/render.py        render_markdown(src, formula=…)：先换哨兵、渲染完再替换成 <img>
mdreader/core.py          formula_cache_dir()、formula_resolver()、GET /api/formula
mdreader/winui.py         $$…$$ 块解析、行内 $…$ 匹配、insert_preview_formula()、缩放/换主题重画
<workspace>/formula-cache/  按内容寻址的 PNG（键含表达式/字号/缩放/主题/粗细/渲染器版本）
```

**为什么先换哨兵**：公式必须在 markdown 规则下与代码块、行内代码、`\$` 区分开，所以先按
源文扫描、把公式替换成哨兵，正文照旧过内容白名单，渲染完再把哨兵换成图片标签。公式 HTML
因此来自**只由我们自己生成**的通道：既不会被正文里的 `$` 误伤，也不会因为“支持公式”
就放行文档里的脚本。渲染失败或不支持的写法保留原始表达式并给出原因，不猜、不清空正文。

## 导出服务（F06）

导出的**公共部分在核心**，插件只负责“把快照转成某个格式的临时产物”：

```text
mdreader/exporting.py     来源选择、扫描与预检、快照、打印模板、资源收集、目标判定
mdreader/core.py          _export_plan()（身份/授权 → 来源 → 快照 → 预检 → 目标 → 冲突）
                          GET/POST /api/export/check（只算不写）
                          POST /api/plugins/export（三道拦截后才执行）
mdreader/winui.py         ExportSourceDialog（当前编辑内容 / 磁盘版本）、ExportReportDialog
webui/app.js              导出前先 check，再按需要弹来源选择与预检报告
```

三道拦截的顺序是固定的：**严重问题**（缺图、越界图、空文档）→ **来源未选**（有未保存
修改而调用方没选）→ **可降级项未确认**（写不出来的公式、过宽表格、死链、字体替代）。
全部通过才起插件任务；产物照旧先落在任务工作目录，由核心校验后在目标同目录写临时文件、
原子替换，失败或取消保留原目标。预检与来源选择**不写任何东西**，两端调的是同一份实现。

## 图片插入的三条入口（F03）

图片进入文档的路径有**三条**（选择文件、拖放、剪贴板截图），但它们共用同一条通道：核心
落附件、宿主提交正文，插件只做转换。所以新增入口不会带来第二套撤销/修订/清理规则。

```text
mdreader/media.py          clipboard_image()（只在用户动作时读）
                           _clipboard_dib() + _clipboard_png_from_dib()（CF_DIB → PNG，仅标准库）
                           owns_clipboard_file()（能不能删这份文件）、is_image_path()
mdreader/winui.py          insert_clipboard_image() / insert_dropped_images() / insert_image_with_plugin()
                             └─ insert_image_file(path, command, title)   ← 三条入口共用
                           on_files_dropped()：图片→插入，.md→临时打开，文件夹→导入
webui/app.js               insertImageBlob(file)   ← 选择文件 / 粘贴 / 拖入共用
                           clipboardImageFile(data)：先认 MIME，再认扩展名
core.py                    POST /api/plugins/insert：落 assets/、校验修订、返回 Markdown 片段
plugins/official/image-insert/  校验 + 转 PNG（独立进程，1.0.2）
```

**剪贴板的所有权**：`clipboard_image()` 可能返回两种路径——我们自己 `mkstemp` 的临时文件
（系统临时目录、`clipboard-` 前缀），或用户在资源管理器里“复制”的**原件**。只有
`owns_clipboard_file()` 为真时调用方才 `os.remove`；用户原件不复制、不删除。程序**不监视
剪贴板**、不保留历史，读操作只发生在菜单项或 `Ctrl+Shift+V` 被按下的那一刻。

**没有 Pillow 也能贴图**：`CF_DIB` 用标准库解析（`BITMAPINFOHEADER` + 24/32 位像素）并自写
zlib PNG 编码器。注意 `CF_DIB` **没有** 14 字节 `BITMAPFILEHEADER`，像素起点是信息头长度。
Pillow 存在时走它的快路径（能处理更多格式与“复制的图片文件”），它只是可选快路。

## 后续架构决策

继续优先完善当前栈。只有在双界面维护成本和打包实测明确后，再评估是否迁移 Tauri。项目全文搜索目前遍历文件，小规模足够；先收集大量文档下的性能数据，再决定引入 SQLite 索引。
