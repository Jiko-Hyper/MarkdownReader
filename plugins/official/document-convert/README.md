# 文档格式转换插件

一个离线插件，提供图片文字识别 → Markdown、PDF → Markdown、Word DOCX → Markdown、Markdown → HTML、HTML → Markdown。

## 安装和使用

1.3.0 需要本次更新后的 MarkdownReader 0.4.10 或更新宿主，运行环境为 Windows x64 / Python 3.12。旧版 exe 不能仅安装 ZIP 就获得此功能，需一并更新宿主。实拍照片增强识别还需要随桌面运行时提供的离线模型组件。

1. 在更新后的程序中打开「更多 → 插件管理」。
2. 安装 `plugins/packages/mdreader.document-convert-1.3.0.zip`，然后启用「文档格式转换」。
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
| 纸质照片 → MD（增强） | 用于手机拍摄的印刷体单栏纸张：本地 PP-OCRv6 模型、文字行透视矫正、纸张阴影补偿、二次文字复核、标题和跨行段落合并、编号列表。相比系统 OCR 更适合密集中文小字。需要附带的照片识别组件；严重模糊、手写、复杂表格、多栏阅读顺序不保证还原，低置信度或两次识别不一致的文字行会提示核对。 |
| 图片 → MD | PNG、JPEG、BMP、单页 TIFF 的印刷体文字，输出可编辑的 UTF-8 Markdown，按文字尺寸与行距恢复简单标题、段落、常见列表。Windows 10 / 11 自带 OCR，全程本地运行，不需要 API。识别语言依赖系统已安装的 OCR 语言；中文需中文 OCR 语言。支持透明背景、深色背景、EXIF 方向校正。手写、公式、复杂表格、多栏阅读顺序及图形内容不保证还原。 |
| PDF → MD | 按文字位置恢复标题层级、有序/无序列表、网格表格、双栏阅读顺序、代码缩进、链接和位图，保留页码分隔。不做 OCR；纯扫描件报错，混合文件提示无文字页。无框表格、三栏以上、复杂公式及复杂嵌套图片仍可能降级。密码 PDF 请先解密。 |
| Word → MD | `.docx` 常用标题、粗斜体、列表、链接、表格、位图图片、Code 样式代码段与 Quote/Intense Quote 样式引用。合并单元格展平为矩形 Markdown 表格。旧 `.doc` 请先另存为 `.docx`。分页、文本框、复杂公式和复杂样式可能降级。 |
| MD → HTML | 常用 Markdown、表格、代码、删除线；本地位图内嵌，生成可独立打开的 HTML。原始 HTML 作为文字显示；不执行脚本，不渲染 Mermaid 或数学公式。 |
| HTML → MD | 提取静态正文、标题、列表、表格、代码、链接和图片；不会运行 JavaScript，不会获取动态页面内容。支持 UTF-8、UTF-16 BOM、GB18030。 |

Word / PDF / HTML 导出的图片保存在结果文件旁边的 `conversion-assets-…` 目录，移动 Markdown 时请连同此目录移动。本地图片仅从源文档所在目录及子目录读取，单张上限 16 MB；支持 PNG、JPEG、GIF、WebP、BMP，不下载远程图片。缺失、越界或不支持的图片保留替代文字并提示。相对文档链接保持原写法，跨目录保存后需检查。

源文件最大 32 MB；PDF 最多 1000 页；图片最多 500 张；产物总大小最大 64 MB。输出原子发布使用同目录硬链接，需要支持硬链接的文件系统（如 Windows NTFS）；不支持时会报错并清理临时产物。

首版入口位于桌面「更多」菜单；浏览器模式暂未提供格式转换按钮。

图片文字识别：选择「更多 → 文档格式转换 → 图片转 Markdown（离线文字识别）」后选图片和新的 `.md` 保存位置。单张最多 32 MB、2000 万像素、单边 10000 像素；内部按识别上限缩放，超长截图建议分段裁剪以保留小字清晰度。多页图片会明确拒绝，避免只取第一页。空白或损坏图片不生成空文档。图片中的 Markdown / HTML 符号按文字处理，不执行图片中的指令。完成后请打开结果核对。

## 图片识别验收

### 实拍照片增强识别

在「更多 → 文档格式转换」选择「纸质照片转 Markdown（增强识别）」，选择照片和新的 Markdown 保存位置。普通截图仍可使用原有「图片转 Markdown（离线文字识别）」。照片应正向拍摄；支持 EXIF 方向信息，文本行的轻度倾斜由四点裁切处理。不是整页文档扫描器，不声称修复任意透视、反光或折痕。

增强识别全程离线，不读取 AI 密钥、不上传图片、不在识别时下载模型。运行时组件约 275 MB，独立于插件 ZIP；源码开发者用完整 Python 3.12 执行 `python tools/prepare_photo_ocr.py --install` 准备，已准备时去掉 `--install` 校验并复制到 `build/runtime/photo_ocr_runtime`。运行时重建脚本会一并复制已准备的组件。组件缺失、模型哈希异常会明确报错。

模型及库采用 RapidOCR 3.9.2、PP-OCRv6 small、ONNX Runtime 1.23.2，版本锁定在 `tools/photo_ocr_requirements.txt`，模型 SHA256 在加载时校验。依赖许可证保留在运行时各包的 `dist-info` 目录。模型来源和参数说明参见 [RapidOCR 官方文档](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)。

实拍验收：`python tools/qualify_photo_ocr.py --source 照片.jpg --expected 独立校对稿.md`。真实安装 ZIP 并调用独立工作进程，严格对比全文、标点、数字、标题与编号，只忽略空白；校对稿不传给识别程序。照片样本和之前的 20 张合成截图分开报告，不能把同一照片的变体当作不同来源实拍照片。

### 原有截图样本

运行 `python tools/qualify_image_ocr.py`，通过正式 ZIP、插件工作进程及 Windows OCR 转换 20 张固定合成图片。覆盖中英文、混合文本、六种扩展名、灰度、深色、透明背景、大小字体、标题、编号列表、段落间距、粗体、彩色背景、数字和宽边距。对比完整文字与顺序（仅归一化空白和 Markdown 转义），同时检查结构及源图片不变。任一检查失败即该样本失败，不替换失败样本。

每次保留图片、预期文字、实际 Markdown、哈希、耗时及报告到 `artifacts/image-ocr-qualification/run-*/`。此处的通过率只指这 20 张合成样本，不能代表任意真实图片准确率；另用 `python -m unittest tests.test_image_ocr tests.test_conversion -v` 验证失败处理和原有转换功能。

## 构建与验证

在项目根目录运行：

```powershell
python -m pip install -r plugins/official/document-convert/requirements.txt --target plugins/conversion-dependencies
python tools/scripts/build_conversion_plugin.py
python -m unittest discover -s tests -p test_conversion.py -v
python tools/scripts/qualify_conversion.py --generate
```

`--generate` 创建 20 份固定内容的源文件（10 DOCX + 10 文字层 PDF）与独立预期结果；日常回归去掉该参数，重复使用同一份样本。测试通过真实安装包和独立工作进程运行。逐份检查全文字符及顺序、标题层级、列表数量、表格全部单元格与行列顺序、链接、代码逐字与缩进、图片像素和源文件不变。

一份文档必须通过所有适用检查才计为通过，文档通过率和检查点通过率均须严格大于 99%；20 份中有一份失败就不合格。报告、输入哈希、输出哈希、告警与耗时保存在 `artifacts/conversion-qualification/`。此回归验收不代表对任意现实文档的总体准确率承诺；扫描件 OCR、旧 DOC 和原生公式对象未纳入这组成功样本。

安装包附带运行依赖及许可证，无需用户安装转换库。测试样本生成另外复用项目的 `plugins/dependencies`（python-docx、ReportLab、Pillow）。构建会更新 `mdreader/plugin_trust.json`，ZIP 和宿主受信清单须同时分发。

实现参考：[pdfplumber](https://github.com/jsvine/pdfplumber)、[pypdf](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)、[Mammoth](https://github.com/mwilliamson/python-mammoth)、[markdownify](https://github.com/matthewwithanm/python-markdownify)。
# 扫描 PDF 与公式（1.4.0）

测试版入口：文档格式转换 → 扫描 PDF 转 Markdown（公式与原页批注）。
支持输入 1 GB 以内 PDF，页码如 `1-20,25` 或 `all`；默认选择前 20 页，超过原件页数会提示重新选择。
正文和数学公式为可编辑 Markdown/LaTeX，示意图保留图片。每页附原文件名、SHA-256 和原页链接；公式附归一化区域坐标与原图。
公式会进行局部灰度重识别，差异仅作为待核对候选，不自动覆盖正文。所有结果均为待核对草稿，不承诺或自动显示 98% 正确率。
可在处理中取消，取消或失败不会发布最终 Markdown。大量页面建议分批；任务最长 4 小时，附件总量上限 900 MB，超过限制需缩小页码范围。

扫描引擎独立于普通 PDF 转换。`tools/prepare_scan_runtime.py` 从已验证的 MinerU 4.0.10 依赖和离线模型生成独立嵌入式 Python 组件 `build/scan-runtime-portable`。
完整发行包通过 `tools/build_release.ps1 -WithScanRuntime` 将其置于 `scan-runtime`，不依赖本机 Python。模型路径随安装位置解析；识别只使用本地模型，不上传文档。第三方声明见 `docs/THIRD_PARTY_SCAN.md`。
人工核对过的教材订正没有写成通用替换规则；软件不会把具体书中的答案套用到其他文档。
