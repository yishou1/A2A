from __future__ import annotations

from xml.etree import ElementTree

from amos_platform.api.app_factory import create_app
from amos_platform.media.evidence_products import (
    EvidenceProductService,
    build_public_capture_record,
    render_evidence_product,
)


def _radar_capture() -> dict:
    return {
        "media_id": "MEDIA-RADAR-01",
        "capture_id": "CAPTURE-RADAR-01",
        "product_type": "derived_sensor_product",
        "captured_at_sec": 125,
        "platform_id": "SHIP-01",
        "sensor_instance_id": "AESA-RADAR",
        "capability_id": "CAP-RADAR-01",
        "observation_ids": ["OBS-01"],
        "capture_parameters": {"instrumented_range_nm": 24},
        "renderer_type": "radar_ppi",
        "title": "当前雷达批次",
    }


def _radar_state() -> dict:
    return {
        "clock": {"run_id": "RUN-01", "elapsed_sec": 125},
        "observations": [{
            "observation_id": "OBS-01",
            "asset_id": "SHIP-01",
            "sensor_id": "AESA-RADAR",
            "bearing_deg": 37.4,
            "range_nm": 9.8,
        }],
        "assets": [],
        "fused_tracks": [],
        "network": {},
    }


def test_svg_renderer_is_deterministic_and_uses_only_supplied_measurements() -> None:
    first = render_evidence_product(_radar_capture(), _radar_state())
    second = render_evidence_product(_radar_capture(), _radar_state())

    assert first["bytes"] == second["bytes"]
    assert first["checksum"] == second["checksum"]
    assert "37.4° / 9.8 NM" in first["svg"]
    assert "24.0 NM" in first["svg"]
    ElementTree.fromstring(first["bytes"])


def test_radar_ppi_separates_window_history_from_current_plots() -> None:
    capture = _radar_capture()
    capture["observation_ids"] = ["OBS-HISTORY", "OBS-CURRENT"]
    capture["capture_parameters"].update({
        "azimuth_coverage_deg": 120,
        "range_resolution_m": 60,
    })
    capture["platform_pose"] = {"heading_deg": 45}
    capture["product_data"] = {
        "observation_window": {"start_sec": 65, "end_sec": 125, "observation_count": 2},
        "observations": [
            {
                "observation_id": "OBS-HISTORY", "asset_id": "SHIP-01",
                "sensor_id": "AESA-RADAR", "sim_time": 95,
                "bearing_deg": 34.0, "range_nm": 10.2, "snr_db": 17.0,
            },
            {
                "observation_id": "OBS-CURRENT", "asset_id": "SHIP-01",
                "sensor_id": "AESA-RADAR", "sim_time": 125,
                "bearing_deg": 37.4, "range_nm": 9.8, "snr_db": 21.0,
            },
        ],
    }

    svg = render_evidence_product(capture, _radar_state())["svg"]

    assert "T+65s–T+125s" in svg
    assert "当前点迹 1" in svg
    assert "历史点迹 1" in svg
    assert "覆盖 120°" in svg
    assert "距离分辨率 60 m" in svg
    assert "SNR 21.0 dB" in svg


def test_missing_measurements_remain_unavailable() -> None:
    capture = _radar_capture()
    capture["capture_parameters"] = {}
    capture["observation_ids"] = []
    rendered = render_evidence_product(capture, {
        "clock": {"run_id": "RUN-01"},
        "observations": [],
    })

    assert "未提供" in rendered["svg"]
    assert "量程  0.0 NM" not in rendered["svg"]
    assert "当前窗口无匹配观测" in rendered["svg"]


def test_each_product_family_renders_supplied_public_values() -> None:
    cases = [
        (
            "track_table",
            {"product_data": {"tracks": [{
                "track_id": "TRK-PUBLIC-09", "domain_hint": "surface",
                "lat": 22.61, "lon": 120.48, "heading_deg": 271.5,
                "confidence": 0.87, "source_observation_ids": ["OBS-PUBLIC-09"],
            }]}},
            ("TRK-PUBLIC-09", "271.5°", "OBS-PUBLIC-09"),
        ),
        (
            "elint_spectrum",
            {
                "capture_parameters": {"frequency_band_mhz": [500, 6000]},
                "product_data": {"spectrum": [
                    {"frequency_mhz": 930.5, "power_dbm": -82.0},
                    {"frequency_mhz": 935.5, "power_dbm": -67.5},
                ]},
            },
            ("930.5–935.5 MHz", "-82.0–-67.5 dBm", "500.0–6000.0 MHz"),
        ),
        (
            "network_topology",
            {"product_data": {"network": {
                "nodes": 3, "links": 2, "degraded_links": 1,
                "avg_quality": 0.75, "resilience": 88,
            }}},
            (">3<", ">2<", ">1<", "0.75", "88.0%"),
        ),
        (
            "resource_status",
            {"product_data": {"assets": [{
                "platform_id": "UAV-PUBLIC-04", "role": "relay", "status": "active",
                "speed_kts": 42.5,
                "health": {"battery_pct": 73, "comms_strength": 81},
            }]}},
            ("UAV-PUBLIC-04", "42.5 kt", "73%", "81%"),
        ),
        (
            "execution_state",
            {"product_data": {"activities": [{
                "id": "ACT-PUBLIC-03", "name": "Current allocation", "status": "running",
            }]}},
            ("Current allocation", "running"),
        ),
    ]
    for renderer_type, additions, expected_values in cases:
        capture = {
            "media_id": "MEDIA-PUBLIC",
            "capture_id": "CAPTURE-PUBLIC",
            "renderer_type": renderer_type,
            "captured_at_sec": 20,
        }
        capture.update(additions)
        rendered = render_evidence_product(capture, {"clock": {"elapsed_sec": 20}})
        ElementTree.fromstring(rendered["bytes"])
        assert all(value in rendered["svg"] for value in expected_values)


def test_elint_product_uses_measured_time_frequency_window_and_current_bearing() -> None:
    capture = {
        "media_id": "MEDIA-ELINT-01",
        "capture_id": "CAPTURE-ELINT-01",
        "renderer_type": "elint_spectrum",
        "captured_at_sec": 180,
        "platform_id": "SHIP-01",
        "sensor_instance_id": "ELINT",
        "capture_parameters": {"frequency_band_mhz": [500, 6000]},
        "product_data": {"observations": [
            {
                "observation_id": "OBS-RF-01", "asset_id": "SHIP-01",
                "sensor_id": "ELINT", "sim_time": 90,
                "frequency_mhz": 932.0, "power_dbm": -83.0,
                "bearing_deg": 41.0, "range_nm": 17.2,
            },
            {
                "observation_id": "OBS-RF-02", "asset_id": "SHIP-01",
                "sensor_id": "ELINT", "sim_time": 180,
                "frequency_mhz": 936.0, "power_dbm": -66.0,
                "bearing_deg": 43.5, "range_nm": 15.8,
            },
        ]},
    }

    svg = render_evidence_product(capture, {"clock": {"elapsed_sec": 180}})["svg"]

    assert "T+90s–T+180s" in svg
    assert "932.0–936.0 MHz" in svg
    assert "500.0–6000.0 MHz" in svg
    assert "936.0 MHz · 43.5° · 15.8 NM" in svg
    assert "采样时刻 / 频点" in svg
    assert "测向随时间变化" in svg
    assert "发射源类别需后端判定" in svg


def test_ais_radar_product_shows_candidate_measurements_not_identity_claim() -> None:
    capture = {
        "media_id": "MAR-MEDIA-02", "capture_id": "MAR-CAPTURE-02",
        "renderer_type": "ais_radar_correlation", "captured_at_sec": 1440,
        "platform_id": "SHORE-RADAR-01", "sensor_instance_id": "AIS-RADAR",
        "capture_parameters": {"association_gate_nm": 1.2},
        "consumer_context": {
            "planned": True, "functional_role_ids": ["A1"],
            "model_requirement_ids": ["M06", "M07", "M19"],
            "execution_evidence": "backend_trace_required",
        },
        "product_data": {
            "observations": [
                {"observation_id": "OBS-RADAR-1", "sensor_id": "AESA_RADAR", "bearing_deg": 121.5, "range_nm": 37.50},
                {"observation_id": "OBS-AIS-1", "sensor_id": "AIS", "bearing_deg": 121.6, "range_nm": 37.55},
                {"observation_id": "OBS-RADAR-2", "sensor_id": "AESA_RADAR", "bearing_deg": 90.0, "range_nm": 22.0},
            ],
            "tracks": [
                {"track_id": "TRK-CANDIDATE", "source_observation_ids": ["OBS-RADAR-1", "OBS-AIS-1"]},
                {"track_id": "TRK-RADAR-ONLY", "source_observation_ids": ["OBS-RADAR-2"]},
            ],
        },
    }
    svg = render_evidence_product(capture, {"clock": {"elapsed_sec": 1440}})["svg"]

    assert "候选关联" in svg
    assert "仅雷达" in svg
    assert "ΔR 0.05 NM" in svg
    assert "不等于身份确认" in svg
    assert "PLANNED INPUT · A1 · M06/M07/M19 · TRACE REQUIRED" in svg
    assert "身份已确认" not in svg


def test_capture_record_whitelist_excludes_target_truth() -> None:
    media = {
        "media_id": "MEDIA-RADAR-01",
        "capture_id": "CAPTURE-RADAR-01",
        "sensor_id": "SHIP-01/AESA-RADAR",
        "mime_type": "image/svg+xml",
        "title": "当前雷达批次",
        "target_refs": ["SECRET-TRUTH-CONTACT"],
    }
    plan = {
        "media_id": "MEDIA-RADAR-01",
        "platform_id": "SHIP-01",
        "sensor_instance_id": "AESA-RADAR",
        "target_refs": ["SECRET-TRUTH-CONTACT"],
        "capture_parameters": {"instrumented_range_nm": 24},
    }
    capture = build_public_capture_record(media, plan, _radar_state())
    rendered = render_evidence_product(capture, _radar_state())

    assert "target_refs" not in capture
    assert "SECRET-TRUTH-CONTACT" not in repr(capture)
    assert "SECRET-TRUTH-CONTACT" not in rendered["svg"]


def test_service_freezes_only_released_svg_media_and_uses_run_scoped_uri() -> None:
    service = EvidenceProductService()
    operator_state = {
        "clock": {"run_id": "RUN/01", "elapsed_sec": 125},
        "assets": [],
        "fused_tracks": [],
        "network": {},
        "scenario_story": {"media_cues": [
            {
                "media_id": "MEDIA-RADAR-01", "capture_id": "CAPTURE-RADAR-01",
                "sensor_id": "SHIP-01/AESA-RADAR", "at_sec": 125,
                "mime_type": "image/svg+xml", "title": "当前雷达批次",
            },
            {
                "media_id": "MEDIA-EO-01", "capture_id": "CAPTURE-EO-01",
                "sensor_id": "UAV-01/EO", "at_sec": 125,
                "mime_type": "image/png", "title": "当前光电图像",
            },
        ]},
    }
    scenario = {"capture_plans": [
        {
            "media_id": "MEDIA-RADAR-01", "capture_id": "CAPTURE-RADAR-01",
            "platform_id": "SHIP-01", "sensor_instance_id": "AESA-RADAR",
            "capture_parameters": {"instrumented_range_nm": 24},
        },
        {
            "media_id": "MEDIA-FUTURE-01", "capture_id": "CAPTURE-FUTURE-01",
            "platform_id": "SHIP-01", "sensor_instance_id": "AESA-RADAR",
            "target_refs": ["FUTURE-TRUTH"],
        },
    ]}
    agent_state = {"observations": _radar_state()["observations"]}

    manifest = service.freeze_manifest(operator_state, agent_state, scenario)

    assert [item["media_id"] for item in manifest["products"]] == ["MEDIA-RADAR-01"]
    product = manifest["products"][0]
    assert product["uri"].startswith("/api/v1/evidence-products/RUN%2F01/MEDIA-RADAR-01/")
    assert "FUTURE" not in repr(manifest)
    stored = service.get("RUN/01", "MEDIA-RADAR-01", "125000")
    assert stored is not None
    assert stored.checksum == product["checksum"]["value"]


def test_manifest_reuses_capture_tick_product_instead_of_rerendering_old_evidence() -> None:
    service = EvidenceProductService()
    capture = {
        **_radar_capture(),
        "run_id": "RUN-IMMUTABLE",
        "captured_at_sim_time": 125,
    }
    frozen = service.freeze_capture_record(capture, _radar_state())
    assert frozen is not None
    media = {
        **capture,
        "mime_type": "image/svg+xml",
        "at_sec": 125,
        "dynamic_uri": frozen["uri"],
        "dynamic_checksum": frozen["checksum"]["value"],
    }
    operator_state = {
        "clock": {"run_id": "RUN-IMMUTABLE", "elapsed_sec": 900},
        "assets": [], "fused_tracks": [], "network": {}, "tasks": [], "alerts": [],
        "scenario_story": {"media_cues": [media]},
    }
    manifest = service.freeze_manifest(
        operator_state,
        {"observations": []},
        {"capture_plans": [capture]},
    )

    product = manifest["products"][0]
    assert product["uri"] == frozen["uri"]
    assert product["checksum"] == frozen["checksum"]
    assert product["snapshot_at_sec"] == 125


def test_product_routes_enforce_release_and_active_run_boundaries(monkeypatch) -> None:
    from amos_platform.api.routes import evidence_routes

    class FakeEngine:
        clock = {
            "run_id": "run-route-evidence",
            "scenario_id": "amphibious-landing-joint-operation",
            "elapsed_sec": 780,
        }

        def get_operator_state(self):
            return {
                "clock": dict(self.clock),
                "assets": [],
                "fused_tracks": [],
                "network": {},
                "tasks": [],
                "alerts": [],
                "scenario_story": {"media_cues": [{
                    "media_id": "AMP-MEDIA-02",
                    "capture_id": "AMP-CAPTURE-02",
                    "product_type": "derived_sensor_product",
                    "captured_at_sim_time": 780,
                    "platform_id": "DDG-01",
                    "sensor_instance_id": "AESA-RADAR",
                    "observation_ids": ["OBS-ROUTE-01"],
                    "mime_type": "image/svg+xml",
                    "title": "驱逐舰雷达批次",
                }]},
            }

        @staticmethod
        def get_agent_visible_state():
            return {"observations": [{
                "observation_id": "OBS-ROUTE-01",
                "asset_id": "DDG-01",
                "sensor_id": "AESA-RADAR",
                "bearing_deg": 42.5,
                "range_nm": 11.2,
            }]}

    engine = FakeEngine()
    monkeypatch.setattr(evidence_routes, "get_engine", lambda: engine)
    app = create_app()
    app.testing = True
    client = app.test_client()
    run_id = engine.clock["run_id"]

    guessed = client.get(
        f"/api/v1/evidence-products/{run_id}/AMP-MEDIA-02/779000.svg"
    )
    assert guessed.status_code == 404

    manifest = client.get("/api/v1/evidence-products").get_json()["data"]
    radar = next(item for item in manifest["products"] if item["media_id"] == "AMP-MEDIA-02")
    product_response = client.get(radar["uri"])
    assert product_response.status_code == 200
    assert product_response.mimetype == "image/svg+xml"
    assert product_response.headers["X-Content-Type-Options"] == "nosniff"
    ElementTree.fromstring(product_response.data)

    engine.clock["run_id"] = "run-route-replaced"
    assert client.get(radar["uri"]).status_code == 404
