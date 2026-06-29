# ONNX Text Classifier 示例

这个目录提供一个最小可运行的 ONNX 算法包示例，用于演示和验证：

- `register / validate / activate / run` 主流程
- `text_tokenization -> ONNX Runtime -> classification_postprocess` 链路
- golden case 校验

说明：

- `model.onnx` 现在是真实的最小 ONNX 模型，不再是占位文本文件。
- 模型固定输出 3 类概率，对应 `label_map.json` 里的 `task / report / other`。
- 当前 ONNX 路径仍然依赖现有 preprocess/postprocess 契约，不是任意模型都能零改动接入。

如果需要重新生成示例模型和测试夹具，可以运行：

```bash
python tools/generate_example_onnx_models.py
```

更完整的后端文件格式说明见仓库根目录：

- [backend_file_formats.md](</c:/Users/liu/Desktop/algorithm repo1/backend_file_formats.md>)
