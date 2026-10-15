# 安装包体积分析与压缩记录（0.4.12）

日期：2026-10-10。只更新本地安装包，不发布 GitHub。

## 1. 结论

| | 旧包 | 新包 | 变化 |
| --- | --- | --- | --- |
| 安装包（zip） | 1844.2 MB | **1764.4 MB** | −79.8 MB（−4.3%） |
| 解压后占用 | 2450 MB | **2247 MB** | −203 MB |
| 文件数 | 13858 | 13858 | 不变 |

包体的 **75% 是两个离线识别引擎的模型权重**（压缩后约 1332 MB）。这部分在不减少功能的前提下无法再压。已做的压缩全部来自"删掉桌面流程从不加载的依赖"，功能经实测未变（见第 4 节）。

## 2. 包体构成（新包）

| 目录 | 解压 | 压缩 | 内容 |
| --- | --- | --- | --- |
| `scan-runtime` | 1824.9 MB | 1478.7 MB | 离线扫描识别：独立 Python 3.12 + MinerU 依赖 + 模型 |
| `runtime` | 340.8 MB | 139.4 MB | 主运行时（tkinter、插件依赖、照片 OCR 运行时） |
| `plugins` | 77.6 MB | 60.7 MB | 官方插件 ZIP（47.9）+ 运行时共享依赖（29.6） |
| 其余 | 2.5 MB | 0.9 MB | 源码、网页界面、文档、测试、图标 |

模型明细（`scan-runtime/models-home`，解压后 1409 MB / 压缩后约 1332 MB）：

| 模型 | 解压 | 用途 |
| --- | --- | --- |
| `MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` | 506.5 MB | 视觉语言模型的文本侧 |
| `mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` | 676.5 MB | 同一模型的视觉投影 |
| `PP-DocLayoutV2/inference.onnx` | 204.1 MB | 版面分析 |
| `ch_PP-OCRv6_small_rec_infer.onnx` | 20.2 MB | 文字识别 |
| `ch_PP-OCRv6_tiny_det_infer.onnx` | 1.7 MB | 文字检测 |

清单里没有重复模型、没有多余的量化版本；三个 ONNX 模型与一组 VLM 权重各自对应一条必需的识别路径。

## 3. 已做的压缩：删掉扫描运行时里用不到的依赖

44 个包、197.6 MB（解压后）→ 79.8 MB（压缩后）。复现：

```powershell
python tools/prune_scan_runtime.py --runtime build/scan-runtime-portable --apply
```

该步骤已接入 `tools/prepare_scan_runtime.py`，以后重新准备扫描组件会自动裁剪；被删清单（含版本与体积）写进 `scan-runtime/removed-packages.json`。

主要项：`gradio` 72.6（MinerU 的网页演示界面，桌面端从不启动，连带 gradio_client/hf_gradio/fastapi/starlette/uvicorn/watchfiles/jinja2/nh3 等）、`jieba` 36.5、`pandas` 32.6、`modelscope` 21.2、`hf_xet` 8.9、`pip`+`setuptools` 7.9、`reportlab` 4.4（导出插件自带一份）、Office 读写 `docx`/`pptx`/`openpyxl`/`xlsxwriter`/`olefile`/`et_xmlfile` 约 5.0。

证据（三条独立依据）：

1. **真实推理导入日志**：在扫描运行时里跑一次真正的 `mineru.parser.parse`（含 docvortex 起的渲染子进程），用 `Lib/site-packages/sitecustomize.py` 记录每个进程导入的顶层模块；上述包一个都没有被导入。
2. **设计上不可达**：扫描命令只接受 PDF；宿主强制 `MINERU_MODEL_SOURCE=local`、`HF_HUB_OFFLINE=1`、`MODELSCOPE_OFFLINE=1`；DOCX 转换走插件自带的 mammoth（主运行时），不经过扫描运行时。
3. **删除后实测不变**：同一页 PDF 直接解析，裁剪前后都是 29 个块且逐块内容一致；打包目录里端到端跑 `scan-md` 得到 2853 字符 + 原页附件 + 待核对批注，与裁剪前完全一致。

明确**保留**的包（宁可留着）：`cv2`/`onnxruntime`/`numpy`/`PIL`/`mineru`/`mineru_llama_cpp`/`docvortex`/`shapely`/`cryptography`/`huggingface_hub`/`fontTools`/`lxml`/`openai`，以及数学与图元渲染（`latex2mathml`/`mathml2omml`/`pylatexenc`/`ziamath`/`ziafont`/`metafile_render`/`resvg_py`，公式路径未被本次样本覆盖）、`magika`（格式检测）、`wcwidth`（终端宽度）、`fsspec`/`dateutil`/`cffi`/`google`（依赖链）。

## 4. 功能复验（在打包后的目录里跑）

| 命令 | 结果 |
| --- | --- |
| `scan-md`（扫描 PDF） | 通过：2853 字符、原页附件、待核对批注、公式原图说明 |
| `docx-md` | 通过：83 字符（证明删掉扫描运行时的 python-docx 不影响 DOCX 转换） |
| `pdf-md`（文字层 PDF） | 通过：1940 字符 |
| `image-md`（离线图片识别） | 通过：87 字符（照片 OCR 运行时本轮未改动） |
| `md-html` | 通过：16065 字符 |

`photo-md` 与 `image-md` 共用同一个离线 OCR 运行时，本轮没有改动它，未单独复验。

## 5. 试过但收益不划算的手段

| 手段 | 实测/估算 | 结论 |
| --- | --- | --- |
| deflate 级别 6 → 9 | 采样 0.16% | 模型与二进制已接近不可压，不做 |
| 整包改用 LZMA（.7z 等） | 模型 1–3%，DLL 最高 35%，全包约省 120 MB | Windows 资源管理器不能解 LZMA，需改用随包 Python 解压，安装流程变化大，暂不做 |
| 包内完全重复文件去重 | 6890 个文件、约 242 MB，但重复发生在不同运行时目录之间 | ZIP 内无法共享；改成安装后硬链接只减磁盘不减包体 |
| 删照片 OCR 运行时的 `sympy` | 25.4 MB | 它是 `tools/photo_ocr_requirements.txt` 里明确固定的依赖，为 8 MB 冒识别风险不值得 |
| 插件 ZIP 与 `plugins/dependencies` 二选一 | 47.9 / 29.6 MB | 前者用于离线安装插件，后者是插件运行的共享依赖，都需要 |

## 6. 剩下的 75%：需要决定的方向

模型权重压缩后约 1332 MB，是包体的大头。三个方向各有取舍，都不做则维持现状：

| 方案 | 安装包 | 功能影响 |
| --- | --- | --- |
| A 维持现状（已完成本轮裁剪） | 1.64 GiB | 无 |
| B 模型改为独立组件（随包第二个压缩包，首次使用时解压/下载） | ≈0.43 GiB | 功能不变；但离线扫描识别需要先把模型组件放到位，"解压即用"变成两步 |
| C 换成更小的 VLM 量化（如 Q4_K_M，约 0.6–0.7 GB） | ≈1.05 GiB | 扫描与公式识别质量可能下降，必须用固定语料重新验收后才能发布 |
| D 另出"不含扫描识别"的轻量包 | ≈0.43 GiB，安装后 0.4 GB | 少一个功能（与"不减少原有功能"冲突，仅列出） |

B 是唯一在**不减少功能**前提下把安装包缩到 500 MB 以内的方法；C 需要你先接受质量可能下降并重新做扫描验收；A 就是现在的状态。
