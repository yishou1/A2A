"""Focused smoke verification for the AMOS web runtime."""

from amos_platform.api.app_factory import create_app


SCENARIO_IDS = (
    "maritime-convoy-air-defense",
    "coastal-joint-recon-strike",
    "air-space-sea-carrier-strike",
)


def main() -> int:
    app = create_app()
    app.testing = True
    client = app.test_client()

    assert client.get("/").status_code == 200
    for path in (
        "/static/css/platform.css",
        "/static/js/api/platform-api.js",
        "/static/js/map/tactical-symbols.js",
        "/static/js/map/platform-map.js",
        "/static/js/workflow/commander-workflow.js",
        "/static/js/app/platform.js",
        "/static/vendor/leaflet/leaflet.css",
        "/static/vendor/leaflet/leaflet.js",
        "/static/vendor/protomaps/protomaps-leaflet.js",
        "/static/assets/maps/south-china-sea-basemap.geojson",
        "/static/tiles/manifest.json",
        "/static/tiles/taiwan-southeast-tactical.pmtiles",
        "/static/assets/maps/taiwan-se-relief/manifest.json",
        "/static/assets/maps/taiwan-se-relief/terrain-bathymetry.png",
        "/static/assets/maps/taiwan-se-relief/hillshade.png",
        "/static/assets/maps/taiwan-se-relief/contours.png",
    ):
        assert client.get(path).status_code == 200, path

    tile_range = client.get(
        "/static/tiles/taiwan-southeast-tactical.pmtiles",
        headers={"Range": "bytes=0-126"},
    )
    assert tile_range.status_code == 206
    assert len(tile_range.data) == 127

    scenarios = client.get("/api/v1/scenarios").get_json()["data"]["scenarios"]
    assert tuple(item["id"] for item in scenarios) == SCENARIO_IDS
    for scenario_id in SCENARIO_IDS:
        detail = client.get(f"/api/v1/scenarios/{scenario_id}")
        assert detail.status_code == 200
        data = detail.get_json()["data"]
        assert all(float(item.get("at_sec", 0)) <= 0 for item in data["timeline"])
        assert all(float(item.get("at_sec", 0)) <= 0 for item in data["media_cues"])
        assert "demo_checkpoints" not in data
        assert "asset_routes" not in data
    assert client.get("/api/v1/scenario-support").status_code == 200

    for index, scenario_id in enumerate(SCENARIO_IDS):
        reset = client.post("/api/v1/sim/reset", json={
            "scenario_id": scenario_id,
            "seed": 20260811 + index,
        })
        assert reset.status_code == 200
        started = client.post("/api/v1/sim/start", json={
            "scenario_id": scenario_id,
            "seed": 20260811 + index,
        })
        assert started.status_code == 200
        assert started.get_json()["data"]["scenario_id"] == scenario_id
        state = client.get("/api/v1/sim/state").get_json()["data"]
        assert state["clock"]["scenario_id"] == scenario_id
        assert client.post("/api/v1/sim/stop", json={}).status_code == 200
    print("AMOS smoke verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
