# A01 固定交付样本与验收报告

日期：2026-09-27。基线：0.4.0（含未发布的导出插件 1.0.4）。状态：**A01 验收通过（固定样本范围内，包含已声明降级）**。不代表完整语法保真或安装包已发布。

复现入口：在 `MarkdownReader/` 运行 `python tools/verify_delivery.py`（失败返回非零退出码）。
最近一次完整证据：`.testtmp/delivery-lgzyvek2/`（`report.json`、`report.md`、`samples/`、`word-pages/`、`word-pages.json`）。逐页结论见 [A01 视觉复核记录](A01_VISUAL_REVIEW.md)。

## 1. 结论摘要

- 24 份自编样本 × Word/PDF 共 48 个检查结果，自动核验 **全部通过**（退出码 0，可重复）。其中成功生成 Word/PDF 各 21 份，共 42 份；其余 6 个结果是预期阻止或失败，不能计为成功导出。
- 本次全量 Python 回归：766 项，764 通过、2 项环境条件跳过，退出码 0；前端 68 项全部通过，语法与应用启动检查通过。日志：`.testtmp/a01-improve-diagnostic.log`、`.testtmp/a01-improve-js.log`。首次统一检查停在输入法测试清理阶段，未计通过；停止该测试进程后，重跑完整测试集及统一检查中的其余步骤均成功。
- 此前真实窗口验收 `python tools/scripts/acceptance.py --write`：**214 项通过、0 失败、11 跳过**，仅作历史记录，见 [acceptance-0.4.0-2026-09-27](acceptance-0.4.0-2026-09-27.md)。本次新取消流程另由真实 Tk 控件测试验证，并包含在上述全量回归中，不冒充真人点击。
- 本轮发现并修复 4 个会造成"内容/版式错误"或"整份导不出来"的缺陷，全部补了回归测试（第 4 节）。
- 除文本抽取外，新增了页边距越界、图片拉伸、文字重叠、空白页、跨页表头、Word 表格空单元格等**版式核验**；这些检查在既有 21 份样本上零误报。
- Codex 已使用图像查看工具逐页复核 PDF 24 页、Word 25 页，共 49 页；未发现截字、裁列、图片拉伸、内容遮挡或代码越界。机器报告仍默认“待验收”，实际复核结论独立保存在逐页记录中，不伪称真人试用。
- A01 出口条件"内容丢失或错误覆盖为零"：在自动核验覆盖范围内成立（见第 3、6 节）。

## 2. 样本清单

样本全部自行编写，不含用户文档或个人信息；预期字段与输入一起版本控制（`tests/fixtures/delivery/manifest.json`）。

| 编号 | 标题 | 主要检查点 |
| --- | --- | --- |
| 01-report | 普通中文报告 | 标题、正文、中文标点完整 |
| 02-bilingual | 中英混排 | 中英混排、粗体斜体、行内代码 |
| 03-headings | 六级标题 | 六级标题层级不丢（Word 样式计数） |
| 04-long | 长文连续段落 | 45 段连续文字不覆盖、跨页不截断 |
| 05-nested-list | 多级列表 | 三级嵌套列表层级 |
| 06-list-continuation | 列表续段和非一编号 | 起始编号 7/8，续段不重复编号（Word 与 PDF 各查一次） |
| 07-table | 可编辑表格 | Word 表格行数、单元格文字 |
| 08-long-table | 跨页表头 | 66 行跨页、每页重复表头（Word `tblHeader` + PDF 每页表头文字） |
| 09-wide-table | 十三列宽表 | 宽表不裁列，预检给出 `wide_table` 提示 |
| 10-image | 本地图片 | 图片嵌入、宽高比不变形 |
| 11-image-path | 含空格中文附件路径 | 路径解析、图片嵌入 |
| 12-table-image | 表格单元格内图片 | 单元格内图片不压文字 |
| 13-long-code | 长代码行 | 超长代码不越右边界、缩进保留 |
| 14-code-markup | 代码中的标记 | 代码里的 `![`、`#`、`$` 原样保留 |
| 15-formula | 支持的公式 | 行内/独立公式转图片、降级提示 |
| 16-unsupported-formula | 不支持的公式 | 不支持的公式保留原文表达式 |
| 17-footnote-mermaid | 脚注和 Mermaid 降级 | 脚注文字与 Mermaid 代码不消失，且有降级提示 |
| 18-symbols | 数学符号字形 | θ₂ − θ₁ = 10⁻⁶ 等字形回退不空白 |
| 19-missing | 缺失附件阻止导出 | 阻止项：不生成任何产物 |
| 20-broken-image | 损坏附件保留已有产物 | 失败分支：已有产物原样保留 |
| 21-buffer | 未保存编辑导出 | 缓冲区版本导出、磁盘版本不被覆盖 |
| 22-quote-links | 引用链接与重复文字 | 超链接目标、重复段落计数正确 |
| 23-empty | 空文档阻止导出 | 阻止项：不生成任何产物 |
| 24-wide-formula | 过宽公式缩放 | 公式等比缩小到版面内并提示，不越页边距 |

## 3. 每次运行都核验的内容

1. **内容不静默丢失**：正文、表格单元格文字逐一比对（重复段落按次数比对，换行等排版差异不算丢失）。
2. **产物真实生成**：`docx` 包可解析、`pdf` 逐页可读；阻止样本不生成产物。损坏图片样本在确认覆盖后失败，旧目标字节必须保持不变。
3. **源文件只读**：每次导出前后比对源 Markdown 字节。
4. **同名目标**：先写一个哨兵产物，导出必须要求确认且不得覆盖它。
5. **版式几何**（本轮新增）：
   - PDF 字符与图片必须落在页边距内（页脚页码除外）→ 截字、裁列、代码越界、公式越界；
   - 图片宽高比与源图一致（±3%）→ 拉伸变形；
   - 同页字符两两不重叠 → 叠字、压字；
   - 无空白页；
   - 跨页长表每页都要有完整表头，包括全部表头消失的情况；逐页报告关联实际几何问题页号；
   - Word 内联图片不超过正文宽度、表格无"既无文字也无图片"的空单元格。

## 4. 本轮发现的问题（按严重度）

| 级别 | 问题 | 复现 | 处理 |
| --- | --- | --- | --- |
| P0 整份导不出来 | 段落里的图片/公式比该行剩余宽度长时，ReportLab 的 CJK 折行对空文本片段执行 `ord("")`，PDF 导出直接抛错 | 中文段落 + 宽图片，或过宽公式 | 含图片/公式的段落改用通用折行（长中文仍逐字折行）；公式与图片宽度按版面收敛 |
| P0 整份导不出来 | 过宽公式（957px ≈ 718pt，A4 正文只有 499pt）在 PDF 里触发同一处报错 | 样本 24-wide-formula | 公式按版面等比缩小（`formula_image` 增加宽度/高度上限） |
| P1 内容未按预期呈现 | 公式里的 `\,`、`\{` 等被 CommonMark 反转义，公式源码与查找表失配，公式**静默留在正文**、没有变成图片，公式计数与提示也偏少 | `$\int_{0}^{1} f(t)\,dt = 1$` | 查找表补充反转义别名（`unescaped_markdown`）；样本 24 同时覆盖 |
| P1 版式越界 | 过宽公式在 Word 里压出正文宽度、在 PDF 里越过右边距 | 样本 24-wide-formula | 同上缩放；导出提示明确写出"等比缩小到版面内（内容不丢，字会更小）" |
| P1 验收入口不可用 | `tools/scripts/acceptance.py`、`tools/scripts/verify_official_plugins.py` 仍按旧目录层级（`Path(__file__).resolve().parents[1]`）定位仓库根；代码搬到 `tools/scripts/` 后一运行就 `ModuleNotFoundError: No module named 'mdreader'`，真实窗口验收和官方插件目视样本都进不去 | 直接运行这两个脚本 | 改为 `parents[2]`（与同目录其它脚本一致）；两个脚本恢复可运行 |
| P2 验收断言过期 | 验收矩阵"版本不兼容的插件只被禁用并写明原因"断言原因文本里含 `0.2.8`，本项目已是 0.4.0，这一行永远失败 | `python tools/scripts/acceptance.py` | 断言改为插件声明范围 `>=9.0.0` 与当前版本号，和真实原因文本一致 |

前四项是导出本身的问题，都补了回归测试：`tests/test_official_plugins.py` 中
`test_a_formula_with_markdown_escapes_still_becomes_an_image`、
`test_a_too_wide_formula_is_scaled_into_the_margins_instead_of_failing`、
`test_a_wide_image_at_the_end_of_a_chinese_paragraph_still_exports`，
以及样本 24 纳入 `tests/test_delivery.py` 的固定语料回归。
后两项是验收工具自身坏了，靠重跑 `tools/scripts/acceptance.py`（214 通过 / 0 失败）与
`tools/scripts/verify_official_plugins.py`（退出码 0）验证。

## 5. 异常分支记录

本次补强：此前同名文件测试在执行失败分支前删除旧目标，不能证明失败保护。现样本 20 保留哨兵目标，显式确认覆盖后执行真实插件失败，并比对旧目标全部字节。跨页表头全丢的漏检也已修复，并增加能捕获该缺陷的回归测试。

| 分支 | 记录方式 | 结果 |
| --- | --- | --- |
| 缺失附件阻止导出 | 样本 19（宿主预检 `missing_image`） | 通过：阻止且不产生产物 |
| 损坏附件失败 | 样本 20（插件抛 `UnidentifiedImageError`） | 通过：失败且已有产物原样保留 |
| 空文档阻止导出 | 样本 23（`empty`） | 通过：阻止且不产生产物 |
| 已有同名目标 | 每个样本先写哨兵产物再导出 | 通过：要求确认，哨兵未被覆盖 |
| 未保存编辑 | 样本 21（`source=buffer`） | 通过：导出缓冲区版本，磁盘文件不变 |
| 源文件不被修改 | 每个样本导出前后比对字节 | 通过 |
| 进程中途取消 | 自动测试 `tests/test_plugins.py`：取消后结果被拒绝、停用插件取消其任务；真实窗口：验收矩阵「插件 · 禁用会取消正在跑的任务，结果不会提交」 | 通过（任务层） |
| 导出进行中用户点“取消” | 新增居中主题弹窗；点击“取消导出”、Esc 或关闭弹窗取消后台任务；插件只生成临时产物，宿主最后提交 | 真实 Tk 按钮测试通过，取消后旧目标未变；官方 Word/PDF 插件取消测试也通过 |
| 生成结束但尚未提交时取消 | 后台任务生成成功后先不写目标；取消会撤销继续导出上下文 | 回归通过，取消后的继续提交请求被拒绝，不产生新目标 |

## 5.1 真实窗口验收覆盖（`tools/scripts/acceptance.py`）

本轮把选区修改（B01）纳入真实窗口验收：助手窗口入口、默认只发选区、选区外正文不进请求、按范围应用、不自动写盘、一次撤销、取消不改正文、正文变化后拒绝旧建议、显式选整篇才发全文、输入法组合中不发起请求、三种主题下仍可用 —— 共 16 项，全部通过。
换缩放（100%/150%/200%）下的观感仍需人眼，按 SKIP 记录，不计入通过。

## 6. 尚未完成 / 不计入通过

- **非阻断排版问题 P2**：Word 长表最后一页仅一行；部分标题层级外观差异较小。内容、表头与可编辑结构均保留，后续模板优化，详见逐页记录。
- Word 首次预览调用曾挂起。本轮改用 `OpenNoRepairDialog` 并在 PowerShell 7 下成功渲染当前版本全部 21 份 DOCX；已补 PNG 生成入口，使用当前证据，不借用旧版截图。
- **A02 五名首次用户试用**：未做，不以自动测试冒充；它是独立任务，不是 A01 验收门槛。
- **Word 重开结构不变、第二个 PDF 阅读器复验**：属于阶段 C（C01），本轮未做。
- 既有限制照旧对用户可见：Windows 10/11 x64、单篇 8 MB、有限 TeX 子集、脚注与 Mermaid 非完整渲染、PDF 转换无 OCR。

## 7. 本轮变更文件

A01 补强新增：`mdreader/core.py`（后台导出与取消后禁止提交）、`mdreader/media_ui.py`/`winui.py`（居中主题取消弹窗）、`tests/test_plugins.py`（真实按钮操作）、`tools/render_delivery_pages.py`（Word 逐页图）、`docs/A01_VISUAL_REVIEW.md`（49 页实际观察）。`tests/test_delivery.py` 新增表头全丢、逐页问题归属、官方插件取消与完成后取消回归；损坏附件覆盖失败保护纳入原固定语料回归。

- `tests/fixtures/delivery/24-wide-formula.md`、`tests/fixtures/delivery/manifest.json`（新增样本与 `image_ratio`、`header_labels` 预期）
- `tools/verify_delivery.py`（版式核验、逐页记录）
- `tests/test_delivery.py`（沿用固定语料回归，样本数下限 20 保持）、`tests/test_official_plugins.py`（3 条新回归）
- `plugins/official/common/exporting.py`（公式缩放、反转义查表、含媒体段落换行样式）
- `tools/scripts/build_official_plugins.py`（导出插件 1.0.4）、`plugins/packages/*-1.0.4.zip`、`mdreader/plugin_trust.json`
- `tools/scripts/acceptance.py`（恢复可运行、新增选区小节、修正过期断言）、`tools/scripts/verify_official_plugins.py`（恢复可运行）、`docs/acceptance-0.4.0-2026-09-27.md`（真实窗口验收记录）
