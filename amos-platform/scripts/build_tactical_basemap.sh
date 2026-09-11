#!/usr/bin/env bash
set -euo pipefail

readonly PMTILES_VERSION="1.31.2"
readonly BASEMAP_BUILD="20260824.pmtiles"
readonly BOUNDS="105.0,8.0,135.0,35.0"
readonly MAX_ZOOM="9"

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
task_dir=$(mktemp -d)
trap 'rm -rf "${task_dir}"' EXIT

archive_url="https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_x86_64.tar.gz"
build_url="https://build.protomaps.com/${BASEMAP_BUILD}"
output_path="${repo_root}/static/tiles/taiwan-southeast-tactical.pmtiles"

curl -fsSL --retry 3 --max-time 120 "${archive_url}" -o "${task_dir}/pmtiles.tar.gz"
tar -xzf "${task_dir}/pmtiles.tar.gz" -C "${task_dir}"
"${task_dir}/pmtiles" extract \
  "${build_url}" \
  "${task_dir}/taiwan-southeast-tactical.pmtiles" \
  --bbox="${BOUNDS}" \
  --maxzoom="${MAX_ZOOM}" \
  --download-threads=8
install -m 0644 "${task_dir}/taiwan-southeast-tactical.pmtiles" "${output_path}"

"${task_dir}/pmtiles" show "${output_path}" --header-json
