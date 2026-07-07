"""RT-DETR 检测 + ODConv 精炼网络。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_detector, get_device, get_odconv_refiner
from agent.inference.utils import decode_image_from_frame


def detect_objects(frames: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    model = get_detector(config)
    odconv = get_odconv_refiner(config)
    device = get_device(config)
    conf_thr = float(config.get("confidence_threshold", 0.25))
    imgsz = int(config.get("detection_imgsz", 640))
    crop_size = int(config.get("odconv_crop_size", 128))
    dev_arg = None if config.get("device", "auto") == "auto" else device
    out: list[dict[str, Any]] = []

    for frame in frames:
        if frame.get("modality") not in ("eo_ir", "sar"):
            continue
        img = decode_image_from_frame(frame)
        if img is None:
            continue

        results = model.predict(
            source=img, conf=conf_thr, verbose=False, device=dev_arg, imgsz=imgsz
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
        dets = odconv.refine_detections(img, dets, device=device, crop_size=crop_size)
        out.extend(dets)
    return out
