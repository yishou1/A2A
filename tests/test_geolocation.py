"""多域三维地理解算单元测试。"""

from __future__ import annotations

import unittest

from agent.inference.geo_estimator import TargetDomain, estimate_target_geo, infer_target_domain
from agent.inference.geolocation import (
    SensorGeorefContext,
    intersect_horizontal_plane,
    parse_sensor_georef_context,
    pixel_ray_enu,
)


def _ctx(**overrides) -> SensorGeorefContext:
    base = dict(
        platform_lat=30.518,
        platform_lon=114.375,
        platform_alt_m=3200.0,
        image_width=640,
        image_height=640,
        fov_h_deg=45.0,
        heading_deg=0.0,
        depression_angle_deg=75.0,
        ground_elevation_m=120.0,
        sea_surface_elevation_m=0.0,
    )
    base.update(overrides)
    return SensorGeorefContext(**base)


class GeolocationTests(unittest.TestCase):
    def test_infer_domain_from_class(self):
        self.assertEqual(infer_target_domain("tank"), TargetDomain.LAND)
        self.assertEqual(infer_target_domain("helicopter"), TargetDomain.AIR)
        self.assertEqual(infer_target_domain("warship"), TargetDomain.SEA)

    def test_land_surface_plus_vehicle_offset(self):
        bbox = [300, 300, 340, 340]
        geo = estimate_target_geo(bbox, _ctx(), class_name="tank")
        self.assertEqual(geo["domain"], "land")
        self.assertEqual(geo["alt_m"], 122.5)
        self.assertIn("surface", geo["geo_method"])

    def test_sea_msl_plus_superstructure(self):
        bbox = [300, 300, 360, 360]
        geo = estimate_target_geo(bbox, _ctx(), class_name="destroyer")
        self.assertEqual(geo["domain"], "sea")
        self.assertEqual(geo["alt_m"], 28.0)

    def test_air_with_laser_range(self):
        bbox = [300, 300, 340, 340]
        geo = estimate_target_geo(
            bbox,
            _ctx(depression_angle_deg=20.0, platform_alt_m=5000.0),
            class_name="airplane",
            frame_meta={"laser_range_m": 12000.0},
        )
        self.assertEqual(geo["domain"], "air")
        self.assertEqual(geo["alt_source"], "laser_range")
        self.assertGreater(geo["alt_m"], 500.0)
        self.assertLess(geo["alt_m"], 5000.0)

    def test_air_bbox_size_prior(self):
        bbox = [280, 250, 360, 310]
        geo = estimate_target_geo(bbox, _ctx(), class_name="helicopter")
        self.assertEqual(geo["domain"], "air")
        self.assertEqual(geo["alt_source"], "bbox_size_prior")
        self.assertGreater(geo["alt_m"], 200.0)

    def test_laser_overrides_land_domain_geometry(self):
        bbox = [300, 300, 340, 340]
        geo = estimate_target_geo(
            bbox,
            _ctx(),
            class_name="truck",
            frame_meta={"laser_range_m": 2800.0},
        )
        self.assertEqual(geo["alt_source"], "laser_range")
        self.assertNotEqual(geo["alt_m"], 123.0)

    def test_nadir_center_on_land(self):
        ctx = _ctx(depression_angle_deg=90.0)
        ray = pixel_ray_enu(320, 320, ctx)
        hit = intersect_horizontal_plane(ctx, ray, ctx.ground_elevation_m)
        self.assertAlmostEqual(hit["lat"], ctx.platform_lat, places=4)
        self.assertAlmostEqual(hit["lon"], ctx.platform_lon, places=4)

    def test_parse_sensor_georef_sea_level(self):
        frame = {
            "metadata": {
                "resolution": "1280x720",
                "platform_lat": 30.52,
                "platform_lon": 114.38,
                "altitude_m": 3000.0,
                "sea_surface_elevation_m": 5.0,
            },
            "payload": {"fov_deg": 50.0},
        }
        ctx = parse_sensor_georef_context(frame, {})
        self.assertEqual(ctx.sea_surface_elevation_m, 5.0)


if __name__ == "__main__":
    unittest.main()
