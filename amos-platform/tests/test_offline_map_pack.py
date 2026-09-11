import json
import math
from pathlib import Path

from PIL import Image
import pytest

from amos_platform.api.app_factory import create_app
from amos_platform.data.maritime_convoy_air_defense_builder import build_maritime_convoy_air_defense_scenario


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "static/assets/maps/taiwan-se-relief"
TILE_MANIFEST = ROOT / "static/tiles/manifest.json"


def test_relief_pack_is_complete_and_runtime_offline() -> None:
    manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "amos.offline-relief.v1"
    assert manifest["projection"] == "EPSG:3857"
    assert manifest["cache_version"]
    assert manifest["runtime_network_required"] is False
    assert manifest["source"]["doi"] == "10.25921/fd45-gt74"
    assert len(manifest["source"]["source_tiles"]) == 6
    assert all(len(tile["sha256"]) == 64 for tile in manifest["source"]["source_tiles"])
    expected_kinds = {"terrain", "hillshade", "contours"}
    assert {layer["kind"] for layer in manifest["layers"].values()} == expected_kinds
    assert sum(layer.get("max_zoom") == 8 for layer in manifest["layers"].values()) == 3
    assert sum(layer.get("min_zoom") == 9 for layer in manifest["layers"].values()) == 3
    for layer in manifest["layers"].values():
        path = PACK / layer["path"]
        assert path.is_file()
        render_size = layer.get("render_size", manifest["render_size"])
        expected_size = (render_size["width"], render_size["height"])
        with Image.open(path) as image:
            assert image.size == expected_size


def test_relief_pack_covers_active_scenario_and_is_served_locally() -> None:
    manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    bounds = manifest["bounds"]
    scenario = build_maritime_convoy_air_defense_scenario()
    ao = scenario["theater"]["ao"]

    assert bounds["south"] <= ao["south"] < ao["north"] <= bounds["north"]
    assert bounds["west"] <= ao["west"] < ao["east"] <= bounds["east"]
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    assert scenario["theater"]["theater_id"] in map_script
    assert "/static/assets/maps/taiwan-se-relief/manifest.json" in map_script
    assert "manifest.cache_version" in map_script
    assert "Boolean(configuredRelief)" in map_script

    app = create_app()
    app.testing = True
    client = app.test_client()
    filenames = ("manifest.json", "terrain-bathymetry.png", "hillshade.png", "contours.png")
    for filename in filenames:
        assert client.get(f"/static/assets/maps/taiwan-se-relief/{filename}").status_code == 200
    for filename in filenames[1:]:
        assert client.get(f"/static/assets/maps/taiwan-se-relief/detail/{filename}").status_code == 200


def test_expanded_relief_and_vector_packs_share_western_pacific_coverage() -> None:
    relief = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    tiles = json.loads(TILE_MANIFEST.read_text(encoding="utf-8"))
    expected_bounds = {"south": 8.0, "west": 105.0, "north": 35.0, "east": 135.0}

    assert relief["bounds"] == expected_bounds
    assert tiles["bounds"] == expected_bounds
    assert tiles["available"] is True
    assert tiles["format"] == "pmtiles-mvt"
    assert tiles["max_zoom"] == 9
    assert (ROOT / tiles["path"].removeprefix("/")).is_file()

    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    assert f'maxDataZoom: {tiles["max_zoom"]}' in map_script


def test_relief_zoom_switch_falls_back_without_partial_view_seams() -> None:
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")

    assert "reliefLayers[name]._amosBounds = L.latLngBounds(imageBounds)" in map_script
    assert "layer._amosBounds.contains(viewBounds)" in map_script
    assert "var selected = candidates[0] || null" in map_script
    assert 'map.on("moveend", function () { applyLayerVisibility();' in map_script


def test_browser_map_code_has_no_remote_runtime_dependency() -> None:
    scripts = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in ("static/js/map/tactical-symbols.js", "static/js/map/platform-map.js")
    )
    assert 'fetch("http' not in scripts
    assert "fetch('http" not in scripts
    assert 'url: "http' not in scripts
    assert "url: 'http" not in scripts
    assert 'fetch(path)' in scripts
    assert 'L.imageOverlay' in scripts


def test_tactical_symbols_keep_affiliation_frames_north_up() -> None:
    symbols = (ROOT / "static/js/map/tactical-symbols.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")

    assert 'class="symbol-body"' in symbols
    assert 'transform="rotate(' in symbols
    assert 'return prefix + "Surface"' in map_script
    assert 'prefix + "MissileSite"' in map_script
    assert '? "hostile" : "unknown"' in map_script
    assert "civilianSurface" in map_script
    assert "SymbolLibrary.svg(kind, heading)" in map_script
    for platform_kind in (
        "merchant", "escort", "aew", "uav", "shoreRadar", "satellite", "uavSwarm",
        "j16", "wz10", "commandCenter", "missileSite", "aircraftCarrier",
        "commandUav", "strikeUav", "loiterUav", "mobileSam", "radarVehicle",
        "runway", "groundCommand",
    ):
        assert platform_kind in symbols


def test_map_defaults_to_a_larger_view_and_supports_expanded_mode() -> None:
    css = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")

    assert "--workspace-width:46vw" in css
    assert ".main.map-expanded #map-panel" in css
    assert 'id="btn-toggle-map-expanded"' in dashboard
    assert "function setMapExpanded(expanded)" in controller
    assert 'sessionStorage.setItem("amos.map.expanded"' in controller


@pytest.mark.parametrize("subdirectory", ["", "detail"])
def test_natural_terrain_tiles_cover_the_entire_offline_region(subdirectory) -> None:
    pack = ROOT / "static/tiles/natural-terrain" / subdirectory
    manifest = json.loads((pack / "manifest.json").read_text())
    bounds = manifest["bounds"]
    count = 0
    for zoom in range(manifest["min_zoom"], manifest["max_zoom"] + 1):
        n = 2 ** zoom
        north = (1 - math.asinh(math.tan(math.radians(bounds["north"]))) / math.pi) / 2 * n
        south = (1 - math.asinh(math.tan(math.radians(bounds["south"]))) / math.pi) / 2 * n
        for x in range(math.floor((bounds["west"] + 180) / 360 * n), math.ceil((bounds["east"] + 180) / 360 * n)):
            for y in range(math.floor(north), math.ceil(south)):
                assert (pack / str(zoom) / str(x) / f"{y}.webp").is_file()
                count += 1
    assert count == manifest["tile_count"]
    assert manifest["runtime_network_required"] is False
    client = create_app().test_client()
    sample = next(pack.glob("*/*/*.webp"))
    response = client.get("/" + sample.relative_to(ROOT).as_posix())
    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    with Image.open(sample) as image:
        assert image.size == (256, 256)


def test_own_force_labels_use_collision_aware_layout() -> None:
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")

    assert "function layoutOwnLabels()" in map_script
    assert "function labelPenalty(" in map_script
    assert "scheduleOwnLabelLayout();" in map_script
    assert "function syncOwnLabel(marker, asset)" in map_script
    assert 'bindLabel(marker, label, "own-label", "center")' in map_script
    assert ".map-resource-label.own-label.label-decluttered::after" in css
    assert ".map-resource-label.own-label.label-low-zoom" in css


def test_map_uses_decluttered_short_trails_and_slow_space_projection() -> None:
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")

    assert "function visualAssetPosition(marker, asset, actual)" in map_script
    assert "spaceVisualSpeedFactor" in map_script
    assert "function renderSpaceGroundTracks(tracks)" in map_script
    assert "function renderSpaceOperations(elapsedSec)" in map_script
    assert "function updateSpaceGroundTracks(elapsedSec)" in map_script
    assert 'id="space-operations-strip"' in dashboard
    assert ".space-operations-strip" in css
    assert "trackTrailWindowSec" in map_script
    assert 'opacity: 0.30, weight: 1.25, dashArray: "4 6"' in map_script
    assert 'renderTrail(\n        trackTrails, id, track.history_path, trackTrailStyle(kind), 48, true,' in map_script
