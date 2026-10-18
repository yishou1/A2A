"""RT-DETR 检测 + ODConv 精炼网络。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_detector, get_device, get_odconv_refiner
from agent.inference.utils import bbox_iou, decode_image_from_frame


def detect_objects(frames: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    model = get_detector(config)
    odconv = get_odconv_refiner(config)
    device = get_device(config)
    conf_thr = float(config.get("confidence_threshold", 0.25))
    imgsz = int(config.get("detection_imgsz", 640))
    crop_size = int(config.get("odconv_crop_size", 128))
    half = bool(config.get("detection_half", False)) and device.startswith("cuda")
    max_det = max(1, int(config.get("detection_max_det", 300)))
    dev_arg = None if config.get("device", "auto") == "auto" else device
    out: list[dict[str, Any]] = []

    for frame in frames:
        if frame.get("modality") not in ("eo_ir", "sar"):
            continue
        img = decode_image_from_frame(frame)
        if img is None:
            raise ValueError("Unable to decode the input image; check its data or reachable URL")

        results = model.predict(
            source=img[..., ::-1].copy(), conf=conf_thr, verbose=False, device=dev_arg, imgsz=imgsz,
            half=half, max_det=max_det,
        )
        if not results:
            continue
        r0 = results[0]
        if r0.boxes is None or len(r0.boxes) == 0:
            continue

        names = r0.names or {}
        dets: list[dict[str, Any]] = []
        for box in r0.boxes:
            xyxy = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            dets.append(
                {
                    "sensor_id": frame.get("sensor_id", "unknown"),
                    "class_name": names.get(cls_id, str(cls_id)),
                    "confidence": round(conf, 4),
                    "bbox": [round(x, 2) for x in xyxy],
                }
            )
        if odconv is None:
            dets = [{**det, "odconv_refined": False} for det in dets]
        else:
            dets = odconv.refine_detections(img, dets, device=device, crop_size=crop_size)
        # RT-DETR queries can assign different labels to the same physical box.
        kept: list[dict[str, Any]] = []
        iou_threshold = float(config.get("detection_nms_iou", 0.7))
        for detection in sorted(dets, key=lambda item: item["confidence"], reverse=True):
            if not any(bbox_iou(detection["bbox"], prior["bbox"]) > iou_threshold for prior in kept):
                kept.append(detection)
        out.extend(kept[:max_det])
    return out
