"""attachment_fetcher 与 URI 帧解码测试。"""

from __future__ import annotations

import base64
import io
import unittest
from unittest.mock import patch

import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

from attachment_fetcher import fetch_bytes_from_uri, resolve_image_uri_from_frame
from agent.inference.utils import decode_image_from_frame


def _tiny_jpeg_bytes() -> bytes:
    if Image is None:
        return b"\xff\xd8\xff\xd9"
    img = Image.new("RGB", (8, 8), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class AttachmentFetcherTest(unittest.TestCase):
    def test_resolve_image_uri_from_attachment_ref(self):
        frame = {
            "sensor_id": "EO-1",
            "modality": "eo_ir",
            "payload": {
                "attachment_ref": {"uri": "https://storage.example.com/recon/frame.jpg"},
            },
            "metadata": {},
        }
        self.assertEqual(
            resolve_image_uri_from_frame(frame),
            "https://storage.example.com/recon/frame.jpg",
        )

    def test_fetch_bytes_from_https(self):
        payload = _tiny_jpeg_bytes()

        class FakeResponse:
            content = payload

            def raise_for_status(self):
                return None

        with patch("attachment_fetcher.requests.get", return_value=FakeResponse()):
            data = fetch_bytes_from_uri("https://storage.example.com/a.jpg")
        self.assertEqual(data, payload)

    def test_decode_image_from_uri(self):
        if Image is None:
            self.skipTest("Pillow not installed")
        payload = _tiny_jpeg_bytes()
        frame = {
            "sensor_id": "EO-1",
            "modality": "eo_ir",
            "payload": {"image_uri": "https://storage.example.com/a.jpg"},
            "metadata": {},
        }

        class FakeResponse:
            content = payload

            def raise_for_status(self):
                return None

        with patch("attachment_fetcher.requests.get", return_value=FakeResponse()):
            rgb = decode_image_from_frame(frame)
        self.assertIsNotNone(rgb)
        assert rgb is not None
        self.assertEqual(rgb.shape, (8, 8, 3))

    def test_decode_image_from_base64_still_works(self):
        if Image is None:
            self.skipTest("Pillow not installed")
        payload = _tiny_jpeg_bytes()
        frame = {
            "sensor_id": "EO-1",
            "modality": "eo_ir",
            "payload": {"image_base64": base64.b64encode(payload).decode("ascii")},
            "metadata": {},
        }
        rgb = decode_image_from_frame(frame)
        self.assertIsNotNone(rgb)
        assert rgb is not None
        self.assertEqual(rgb.shape, (8, 8, 3))
        self.assertGreater(int(rgb[0, 0, 0]), 200)


if __name__ == "__main__":
    unittest.main()
