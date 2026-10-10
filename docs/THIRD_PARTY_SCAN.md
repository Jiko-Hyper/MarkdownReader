# 离线扫描组件

完整发行包的 `scan-runtime` 使用独立的 Python 3.12.9 嵌入式解释器，包含 MinerU 4.0.10、mineru-llama-cpp、ONNX Runtime、PDFium 及其依赖。组件版本和模型 SHA-256 记录在随包的 `scan-runtime/component-manifest.json`。

- MinerU 源码与许可：https://github.com/opendatalab/MinerU 。随包保留 [MinerU 许可](licenses/MinerU-LICENSE.md) 和 [Apache 2.0 全文](licenses/Apache-2.0.txt)。
- MinerU2.5-Pro-2605-1.2B（Q8_0 文本与视觉模型）：https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B ，Apache 2.0；GGUF 发布源：https://huggingface.co/jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF 。
- 布局与 OCR 模型来自 MinerU 官方模型集：https://huggingface.co/opendatalab/MinerU-4_models_onnx 。
- Python 许可见 `scan-runtime/LICENSE.txt`；其他依赖的原有许可证保留在各软件包及其 `.dist-info` 目录中。

MDReader 自有源码的 MIT 许可不替代这些第三方许可。扫描组件在本地运行，不发送原始文档。识别需要较多内存和计算资源；本次实机验证使用 Windows 11、32 GB 内存、RTX 5070 Laptop。不同硬件速度可能不同，建议先转换少量页面。

构建时使用 `tools/prepare_scan_runtime.py` 的 `--env`、`--models`、`--embed` 选择依赖来源，再以 `tools/build_release.ps1 -WithScanRuntime` 组装。未使用该开关的基础包不包含扫描组件。GitHub Actions 仅生成基础构建检查产物，正式完整包单独验证后上传，避免覆盖含模型的发行包。
