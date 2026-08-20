#!/usr/bin/env python3
"""中文注释：生成仓库里用于示例与测试的最小 ONNX 模型。"""

from __future__ import annotations

from pathlib import Path

import onnx
from onnx import TensorProto, checker, helper


def make_classifier_model(output_path: Path) -> None:
    """中文注释：生成一个固定输出 logits 的最小文本分类模型。"""
    input_ids = helper.make_tensor_value_info(
        "input_ids", TensorProto.INT64, [1, "sequence_length"]
    )
    attention_mask = helper.make_tensor_value_info(
        "attention_mask", TensorProto.INT64, [1, "sequence_length"]
    )
    logits = helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 3])

    constant_logits = helper.make_tensor(
        "constant_logits",
        TensorProto.FLOAT,
        [1, 3],
        [0.96, 0.02, 0.02],
    )
    constant_node = helper.make_node(
        "Constant",
        inputs=[],
        outputs=["logits"],
        value=constant_logits,
    )

    graph = helper.make_graph(
        [constant_node],
        "algolib_constant_text_classifier",
        [input_ids, attention_mask],
        [logits],
    )
    model = helper.make_model(
        graph,
        producer_name="algolib",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    model.ir_version = 8
    checker.check_model(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, output_path)


def make_identity_model(output_path: Path) -> None:
    """中文注释：生成一个 int64 向量直通模型，供 no_op/tensor_from_json 测试使用。"""
    input_value = helper.make_tensor_value_info("input", TensorProto.INT64, [3])
    output_value = helper.make_tensor_value_info("output", TensorProto.INT64, [3])
    identity_node = helper.make_node("Identity", inputs=["input"], outputs=["output"])

    graph = helper.make_graph(
        [identity_node],
        "algolib_identity_vector",
        [input_value],
        [output_value],
    )
    model = helper.make_model(
        graph,
        producer_name="algolib",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    model.ir_version = 8
    checker.check_model(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, output_path)


def main() -> None:
    """中文注释：默认相对仓库根目录生成两个固定模型文件。"""
    repo_root = Path(__file__).resolve().parent.parent
    make_classifier_model(
        repo_root / "examples" / "onnx_text_classifier" / "1.0.0" / "model.onnx"
    )
    make_identity_model(
        repo_root / "tests" / "fixtures" / "onnx_identity_vector.onnx"
    )
    print("Generated ONNX fixtures.")


if __name__ == "__main__":
    main()
