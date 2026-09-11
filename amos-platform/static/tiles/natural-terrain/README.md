# Natural terrain offline basemap

Natural Earth II 3.2.0 (overview) and Mapzen Terrain (detailed region) replace
the previous light/dark Protomaps base colors and ETOPO image overlays.
The runtime loads local WebP XYZ tiles at full opacity.
Ocean colors use a blue-grey cartographic palette, retaining the source's
shaded relief; land retains the original natural colors. Color is not a numerical
elevation/depth measurement, so the old ETOPO quantitative legend is not shown.

Coverage: 105–135°E, 8–35°N. EPSG:3857, XYZ, 256 px, z5–9.
The source is 21600 × 10800 pixels (about 1.85 km/pixel at the equator).
This overview is not detailed imagery of buildings or runways.
Overzoom does not provide additional source detail. Chinese place names use the
existing offline OSM/Protomaps archive as a separate transparent vector layer.

The detailed pack at `detail/` covers 119–124°E, 17–25°N at z8–11 and
contains Mapzen Terrarium DEM data from AWS Open Data, colored by elevation
with shaded relief. Terrain RGB is decoded to meters before rendering, using
neighboring tiles for edge-continuous hillshade. Source resolution varies;
the z11 rendering grid is approximately 70m at 21°N, not a claim of uniform
70m source accuracy. Labels remain vectors when zoomed further.
The detailed pack is used only when the full viewport is inside its bounds;
otherwise the overview covers the entire viewport to avoid partial overlays.

Detail sources: Mapzen Terrain Tiles; global SRTM and GMTED2010 terrain data
courtesy of the U.S. Geological Survey; global ETOPO1 terrain data by
DOC/NOAA/NESDIS/NCEI. The source government data is public domain;
Mapzen's full attribution guidance is at
https://github.com/tilezen/joerd/blob/master/docs/attribution.md.
The derived cartography is not an official USGS or NOAA product.

Build the detailed pack (network access only during download; cached reruns):

```sh
python amos-platform/scripts/build_terrain_detail_tiles.py /path/to/dem-cache amos-platform/static/tiles/natural-terrain/detail
```

Source: https://www.naturalearthdata.com/downloads/10m-raster-data/10m-natural-earth-2/

License: public domain, https://www.naturalearthdata.com/about/terms-of-use/

Rebuild from the extracted `NE2_HR_LC_SR_W.tif`, from the repository root:

```sh
python amos-platform/scripts/build_natural_terrain_tiles.py /path/to/NE2_HR_LC_SR_W.tif amos-platform/static/tiles/natural-terrain
```

`manifest.json` records the source checksum, coverage, tile count, and format.
The source download belongs in a build cache; only regional tiles are deployed.
