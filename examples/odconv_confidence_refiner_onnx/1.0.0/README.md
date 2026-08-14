# ODConv Confidence Refiner ONNX

Native ONNX package for the ODConv detection-confidence refiner.

Inputs are already-cropped RGB tensors plus RT-DETR base confidence.
Frame loading / bbox crop logic stays in `battlefield_rtdetr_detector`.
