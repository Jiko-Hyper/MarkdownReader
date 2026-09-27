# tools — 构建、打包与验收

所有脚本都在**仓库根目录**执行。发布包里的 `MDReader.exe` 就是这里产出的。

## 打包发布（一条命令）

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1
```

它依次做四件事，任何一步失败都会立刻停下：

1. `prepare_runtime.ps1` — 下载 Python 3.12 embeddable，补上 tkinter（从本机同一个
   3.12 完整安装里取 `tkinter/`、`_tkinter.pyd`、`tcl86t.dll`、`tk86t.dll`、`tcl\`），
   再补上插件依赖（Pillow / markdown-it-py / python-docx / lxml / reportlab）。
2. `build_launcher.ps1` — gcc + windres 编译 `launcher.c`，把图标、版本信息、
   DPI 感知清单写进 `MDReader.exe`（约 100 KB）。
3. 组装 `build\release\MarkdownReader\`：exe + 源码 + `runtime\` + `plugins\` + 启动脚本。
4. 在发布目录里跑三项自检：`main.py --version`、`MDReader.exe --selftest`
   （真实创建窗口并渲染）、`tools/smoke_release.py`（安装并启用随包插件），
   全部通过后压缩成 `release\MarkdownReader-<版本>-win64.zip`。

| 参数 | 作用 |
| --- | --- |
| `-NoZip` | 只生成目录，不压缩 |
| `-SkipRuntime` | 复用已存在的 `build\runtime`，省一次下载与复制 |

单独使用：

```powershell
powershell -ExecutionPolicy Bypass -File tools/prepare_runtime.ps1 -Force
powershell -ExecutionPolicy Bypass -File tools/build_launcher.ps1
python tools/make_icon.py          # 重新生成 assets/icon.png 与 icon.ico
python tools/smoke_release.py      # 只跑「装插件 + 启用插件」这一项
```

排错提示：`MDReader.exe` 正常双击走的是 `runtime\pythonw.exe`，它不产生控制台输出。
需要看程序打印的启动信息或异常时，用随包解释器直接跑入口：

```powershell
runtime\python.exe main.py --console
```

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File tools/scripts/check.ps1   # 单元测试 + JS 语法 + 启动检查
python tools/scripts/acceptance.py --root .                       # 真实窗口验收清单
python tools/scripts/acceptance.py --root build\release\MarkdownReader   # 对发布包再跑一遍
python tools/scripts/bench.py                                     # 渲染基准
python tools/scripts/bench_interaction.py                         # 交互性能基准
```

`acceptance.py` 会弹出真实窗口逐项判定，需要人在旁边看着。

## 插件工具

```powershell
python tools/scripts/build_official_plugins.py        # 重新打包四个官方离线插件
python tools/scripts/build_plugin.py <插件目录> -o <输出目录>
python tools/scripts/install_official_plugins.py      # 把插件安装进默认工作区
python tools/scripts/verify_official_plugins.py       # 校验插件包哈希与清单
```

## 原生桥

`mdreader/native_bridge.c` 是输入法组合与拖放的窗口消息钩子，仓库里已经带了编译好的
`native_bridge.dll`。需要重新编译时：

```powershell
gcc -shared -O2 -Wall -Wextra -static-libgcc mdreader/native_bridge.c `
    -o mdreader/native_bridge.dll -lcomctl32 -limm32 -lshell32
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `launcher.c` | 免安装版 exe 外壳（唯一需要编译的 C 代码） |
| `build_launcher.ps1` | 编译外壳并写入图标 / 版本资源 |
| `prepare_runtime.ps1` | 准备 `runtime\`（Python + tkinter + 插件依赖） |
| `build_release.ps1` | 一键打包发布 |
| `make_icon.py` | 生成应用图标（需要 Pillow） |
| `MDReader.rc` | windres 生成的资源脚本（构建产物，可安全删除） |
| `scripts/` | 验收、基准、插件打包与安装脚本 |

格式转换插件首次构建前，执行 `python -m pip install -r plugins/official/document-convert/requirements.txt --target plugins/conversion-dependencies`。仅重建转换插件可运行 `python tools/scripts/build_conversion_plugin.py`；它同时更新宿主受信清单。


安装/卸载脚本验证：`powershell -NoProfile -ExecutionPolicy Bypass -File tools/test_install.ps1` 与 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/test_uninstall.ps1`。测试只使用隔离的临时安装与模拟文档，卸载测试同时覆盖双击入口、取消、路径保护、运行中拒绝卸载、按记录删除程序与数据两个目录，以及下载目录和目标外文件保留。完整发布构建会强制执行这两项。
