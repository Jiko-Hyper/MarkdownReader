# 文档格式转换插件

一个离线插件，提供 PDF → Markdown、Word DOCX → Markdown、Markdown → HTML、HTML → Markdown。

## 安装和使用

必须使用 MarkdownReader 0.4.0 或更新的宿主（新增 `file.convert` 能力和桌面菜单）。旧版 exe 不能仅安装 ZIP 就获得此功能，需一并更新宿主。

1. 在更新后的程序中打开「更多 → 插件管理」。
2. 安装 `plugins/packages/mdreader.document-convert-1.0.0.zip`，然后启用「文档格式转换」。
3. 打开「更多 → 文档格式转换」，选择转换方向、源文件和新文件保存位置。
4. 成功后弹窗显示结果路径和转换提示。

选择的是磁盘文件；编辑器里尚未保存的修改请先保存。源文件不改动，目标已存在时拒绝覆盖，请换一个文件名。转换在独立工作进程完成，最长 120 秒；停用插件会取消正在进行的转换。

也可以在项目根目录用 Python 3.12 执行：

```powershell
python tools/scripts/install_official_plugins.py
python main.py
```

安装脚本会安装并启用本发行版全部正式插件。仅安装此插件请使用插件管理界面。

## 格式与边界

| 方向 | 支持与限制 |
| --- | --- |
| PDF → MD | 提取文字层，保留页码分隔。扫描件不做 OCR；纯扫描件报错，混合文件提示无文字页。多栏、表格、公式、图片不能保证还原。密码 PDF 请先解密。 |
| Word → MD | `.docx` 常用标题、粗斜体、列表、链接、表格和位图图片。旧 `.doc` 请先另存为 `.docx`。分页、文本框、复杂公式和复杂样式可能降级。 |
| MD → HTML | 常用 Markdown、表格、代码、删除线；本地位图内嵌，生成可独立打开的 HTML。原始 HTML 作为文字显示；不执行脚本，不渲染 Mermaid 或数学公式。 |
| HTML → MD | 提取静态正文、标题、列表、表格、代码、链接和图片；不会运行 JavaScript，不会获取动态页面内容。支持 UTF-8、UTF-16 BOM、GB18030。 |

Word / HTML 导出的图片保存在结果文件旁边的 `conversion-assets-…` 目录，移动 Markdown 时请连同此目录移动。本地图片仅从源文档所在目录及子目录读取，单张上限 16 MB；支持 PNG、JPEG、GIF、WebP、BMP，不下载远程图片。缺失、越界或不支持的图片保留替代文字并提示。相对文档链接保持原写法，跨目录保存后需检查。

源文件最大 32 MB；PDF 最多 1000 页；图片最多 500 张；产物总大小最大 64 MB。输出原子发布使用同目录硬链接，需要支持硬链接的文件系统（如 Windows NTFS）；不支持时会报错并清理临时产物。

首版入口位于桌面「更多」菜单；浏览器模式暂未提供格式转换按钮。

## 构建与验证

在项目根目录运行：

```powershell
python -m pip install -r plugins/official/document-convert/requirements.txt --target plugins/conversion-dependencies
python tools/scripts/build_conversion_plugin.py
python -m unittest discover -s tests -p test_conversion.py -v
```

安装包附带运行依赖及许可证，无需用户安装转换库。测试样本生成另外复用项目的 `plugins/dependencies`（python-docx、ReportLab、Pillow）。构建会更新 `mdreader/plugin_trust.json`，ZIP 和宿主受信清单须同时分发。

实现参考：[pypdf](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)、[Mammoth](https://github.com/mwilliamson/python-mammoth)、[markdownify](https://github.com/matthewwithanm/python-markdownify)。
