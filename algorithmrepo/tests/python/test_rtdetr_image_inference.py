"""Regression checks for actual image pixels and boxes, without loading weights."""
from types import SimpleNamespace

import numpy as np
import pytest

from agent.inference import vision


def configure(monkeypatch, image, model):
    monkeypatch.setattr(vision, 'get_detector', lambda _: model)
    monkeypatch.setattr(vision, 'get_device', lambda _: 'cpu')
    monkeypatch.setattr(vision, 'get_odconv_refiner', lambda _: None)
    monkeypatch.setattr(vision, 'decode_image_from_frame', lambda _: image)


def test_rgb_pixels_are_converted_to_ultralytics_bgr(monkeypatch):
    rgb = np.array([[[10, 20, 30]]], dtype=np.uint8)
    seen = []
    class Model:
        def predict(self, **kwargs):
            seen.append(kwargs['source'])
            return []
    configure(monkeypatch, rgb, Model())
    vision.detect_objects([{'modality': 'eo_ir'}], {})
    assert seen[0].tolist() == [[[30, 20, 10]]]
    assert rgb.tolist() == [[[10, 20, 30]]]


def test_unreadable_image_is_an_error_instead_of_zero_detections(monkeypatch):
    configure(monkeypatch, None, object())
    with pytest.raises(ValueError, match='Unable to decode'):
        vision.detect_objects([{'modality': 'eo_ir'}], {})


def test_duplicate_queries_keep_one_box_and_preserve_separate_targets(monkeypatch):
    class Vector:
        def __init__(self, value): self.value = value
        def tolist(self): return self.value
    def box(coords, label, confidence):
        return SimpleNamespace(xyxy=[Vector(coords)], cls=[label], conf=[confidence])
    boxes = [box([0, 0, 100, 100], 0, 0.9), box([0, 0, 100, 100], 1, 0.7), box([150, 0, 250, 100], 1, 0.8)]
    class Model:
        def predict(self, **kwargs):
            return [SimpleNamespace(boxes=boxes, names={0: 'destroyer', 1: 'boat'})]
    configure(monkeypatch, np.zeros((100, 250, 3), dtype=np.uint8), Model())
    result = vision.detect_objects([{'modality': 'eo_ir'}], {})
    assert [(item['class_name'], item['bbox']) for item in result] == [
        ('destroyer', [0, 0, 100, 100]), ('boat', [150, 0, 250, 100])]
