import json
from pathlib import Path

from PIL import Image

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
    assert "hostileSurface" in map_script
    assert "unknownSurface" in map_script
    assert "civilianSurface" in map_script
    assert "SymbolLibrary.svg(kind, heading)" in map_script
    for platform_kind in ("merchant", "escort", "aew", "uav", "shoreRadar", "satellite", "uavSwarm"):
        assert platform_kind in symbols


def test_map_defaults_to_a_larger_view_and_supports_expanded_mode() -> None:
    css = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")

    assert "--workspace-width:34vw" in css
    assert ".main.map-expanded #map-panel" in css
    assert 'id="btn-toggle-map-expanded"' in dashboard
    assert "function setMapExpanded(expanded)" in controller
    assert 'sessionStorage.setItem("amos.map.expanded"' in controller
