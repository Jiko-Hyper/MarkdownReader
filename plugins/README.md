# plugins — 官方离线插件

MDReader 的核心功能（读写、撤销、表格、主题、公式阅读、HTML 导出）不依赖任何插件。
**导出 Word/PDF**、**图片插入** 与 **文档格式转换** 由这里的四个官方插件提供，全部离线运行。

Word/PDF 导出插件 1.0.5 新增「简洁报告」「技术文档」两套样式。更新宿主源码后，从工具栏「导出 Word/PDF」选择；旧插件可在提示中更新并启用。已有预检、取消和覆盖确认继续生效，样式不修改源 Markdown。

`mdreader.document-convert-1.4.0.zip`：新增扫描 PDF 正文与 LaTeX 公式识别、页码选择和原页批注；需要宿主 0.4.11 及本地扫描运行环境。保留图片及纸质照片 OCR、普通 PDF / Word 转 Markdown 和 Markdown 与 HTML 互转。扫描识别不代表准确率已通过验收。
更新宿主后，在桌面「更多 → 文档格式转换」使用，[安装与边界说明](official/document-convert/README.md)。

| 目录 | 是什么 |
| --- | --- |
| `official/` | 四个插件的源码（`manifest.json` + `plugin.py`，导出插件共用 `common/exporting.py`） |
| `packages/` | 可直接安装的插件包 `*.zip`，SHA256 登记在 `mdreader/plugin_trust.json` |
| `dependencies/` | 插件运行需要的第三方库（Pillow / markdown-it-py / python-docx / lxml / reportlab），源码运行时用 |
| `samples/` | 插件开发样例，用来演示清单字段与能力接口 |

## 用户怎么装

1. 打开 MDReader → 「⋯ 更多 → 插件管理…」（网页端：「🧩 插件管理」）；
2. 点「安装本地插件包…」，选 `packages/` 里的 zip；
3. 装好后默认**不启用**，需要手动点「启用」，菜单里才会出现对应命令。

安装只接受**受信清单里登记过哈希**的包：id 对不上或内容被改过都会被拒绝，界面上没有
「忽略来源」的开关。插件代码在独立工作进程里执行，产物路径、类型和大小都由宿主重新校验；
但独立进程**不是安全沙箱**，请不要安装来源不明的第三方插件包。

## 开发者怎么打包

```powershell
python tools/scripts/build_plugin.py <插件源码目录> -o <输出目录>
python tools/scripts/build_official_plugins.py    # 重新生成四个官方插件包，转换依赖准备方式见上面的说明
```

打包是确定性的（成员按名字排序、时间戳固定），因此同一份源码总是得到同一个 SHA256；
把新哈希登记进 `mdreader/plugin_trust.json` 之后才能被安装。
