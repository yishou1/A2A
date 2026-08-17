# 目标三维地理解算说明

本文档描述感知流水线中，如何从二维检测框解算敌方目标的 **WGS84 三维位置**（`lat`、`lon`、`alt_m`），以及高度按 **陆 / 空 / 海** 分域测算的策略。

**实现入口**

| 模块 | 路径 | 职责 |
|------|------|------|
| 相机几何 | `agent/inference/geolocation.py` | 像素 → ENU 射线、平面求交、沿射线取点 |
| 多域高度策略 | `agent/inference/geo_estimator.py` | 域判定、测距优先级、按类型算 `alt_m` |
| 跟踪接入 | `agent/inference/models/motr_kalman.py` | MOTR 关联后调用 `estimate_target_geo` |
| 感知编排 | `agent/skills/perception/skill.py` | RT-DETR → EDL → MOTR 全链路 |
| 载荷适配 | `tactical_intelligence_agent/payload_adapter.py` | Commander 载荷 → `SensorBatch` |
| 配置 | `config/default.yaml` | 默认地表/海平面、类型高度参数 |

**单元测试**：`tests/test_geolocation.py`

---

## 1. 整体流水线

```
RT-DETR 检测 → bbox + class_name
        ↓
MOTR 关联 + Neural Kalman（仅更新图像平面 bbox: cx,cy,w,h,vx,vy）
        ↓
estimate_target_geo(bbox, 平台位姿, class_name, 传感器数据)
        ↓
geo { lat, lon, alt_m, domain, alt_source, ... }
        ↓
写入 Detection.geo → 最终 intelligence_packet.targets[].geo
```

感知技能中的调用顺序：

```50:72:agent/skills/perception/skill.py
        track_result = self.tracker.run(
            {
                "verified_detections": verified,
                "prior_tracks": prior_tracks or [],
                "visual_frame": visual_frames[0] if visual_frames else None,
                "batch_context": batch.context,
            }
        )
        ...
        for det, track in zip(verified, track_result.get("tracks", [])):
            detections.append(
                Detection(
                    track_id=track.get("track_id"),
                    class_name=det.get("class_name", "unknown"),
                    ...
                    geo=track.get("geo"),
                )
            )
```

MOTR 跟踪完成后，对每个目标调用三维解算：

```204:214:agent/inference/models/motr_kalman.py
            geo = estimate_target_geo(
                bbox,
                georef,
                class_name=str(det.get("class_name", "unknown")),
                det_meta=det.get("metadata") if isinstance(det.get("metadata"), dict) else {},
                frame_meta=frame_meta,
                batch_context=batch_context,
                config=cfg,
                prior_geo=prior_geo if isinstance(prior_geo, dict) else None,
                smooth_alpha=float(cfg.get("geo_smooth_alpha", 0.7)),
            )
```

**要点**：经纬度与高度在同一套射线几何下计算，但 **高度公式** 随目标域（陆/空/海）和是否有激光/雷达测距而变化。

---

## 2. 第一步：公共几何（所有目标相同）

从检测框中心像素 `(cx, cy)` 出发：

1. 读取平台位姿：`platform_lat/lon`、`altitude_m`、`heading_deg`、`depression_angle_deg`、`fov_deg`
2. 像素 → 相机射线 → 旋转到 ENU（东、北、天）单位向量 `ray`
3. **经纬度**由该射线决定（与地表/海平面求交，或沿射线走斜距 `R`）

### 2.1 解析传感器位姿

`parse_sensor_georef_context()` 从帧 `metadata`、批次 `context`、`config/default.yaml` 合并位姿：

```105:178:agent/inference/geolocation.py
def parse_sensor_georef_context(
    visual_frame: dict[str, Any] | None,
    config: dict[str, Any] | None,
    *,
    batch_context: dict[str, Any] | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> SensorGeorefContext:
    ...
    return SensorGeorefContext(
        platform_lat=platform_lat,
        platform_lon=platform_lon,
        platform_alt_m=platform_alt_m,
        ...
        ground_elevation_m=ground_elevation_m,
        sea_surface_elevation_m=sea_surface_elevation_m,
    )
```

### 2.2 像素 → ENU 视线

```185:194:agent/inference/geolocation.py
def pixel_ray_enu(u: float, v: float, ctx: SensorGeorefContext) -> tuple[float, float, float]:
    """像素 → 单位视线向量（ENU：东、北、天）。"""
    cx = ctx.image_width / 2.0
    cy = ctx.image_height / 2.0
    fov_rad = math.radians(ctx.fov_h_deg)
    fx = (ctx.image_width / 2.0) / max(math.tan(fov_rad / 2.0), 1e-6)
    fy = fx
    ray_cam = _normalize_vec(((u - cx) / fx, (v - cy) / fy, 1.0))
    rot = _camera_to_enu_rotation(ctx.heading_deg, ctx.depression_angle_deg)
    return _mat_vec_mul(rot, ray_cam)
```

### 2.3 两条底层几何公式

**公式 A — 与水平面求交**（陆地/海上无测距）

```210:233:agent/inference/geolocation.py
def intersect_horizontal_plane(
    ctx: SensorGeorefContext,
    ray_enu: tuple[float, float, float],
    plane_alt_m: float,
) -> dict[str, float]:
    ...
    t = delta_u / ray_z
    east_m = t * ray_enu[0]
    north_m = t * ray_enu[1]
    up_m = t * ray_enu[2]
    return {
        **enu_offset_to_wgs84(ctx, east_m, north_m, up_m),
        "slant_range_m": t,
    }
```

**公式 B — 沿视线走斜距 R**（有激光/雷达，或空中 bbox 估距）

```236:247:agent/inference/geolocation.py
def point_along_ray(
    ctx: SensorGeorefContext,
    ray_enu: tuple[float, float, float],
    slant_range_m: float,
) -> dict[str, float]:
    east_m = slant_range_m * ray_enu[0]
    north_m = slant_range_m * ray_enu[1]
    up_m = slant_range_m * ray_enu[2]
    return {
        **enu_offset_to_wgs84(ctx, east_m, north_m, up_m),
        "slant_range_m": slant_range_m,
    }
```

其中 `enu_offset_to_wgs84` 将 ENU 偏移转为 WGS84，`alt_m = platform_alt_m + up_m`。

---

## 3. 第二步：判断目标作战域（land / air / sea）

根据检测 `class_name` 关键词，或 metadata 显式指定 `target_domain`：

```86:106:agent/inference/geo_estimator.py
def infer_target_domain(
    class_name: str,
    *,
    explicit_domain: str | None = None,
    config: dict[str, Any] | None = None,
) -> TargetDomain:
    ...
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in norm for kw in keywords):
            return TargetDomain(domain)
    return TargetDomain.LAND
```

| 域 | 触发关键词示例 | 未匹配时 |
|----|----------------|----------|
| `air` | helicopter, drone, airplane, uav, fighter… | — |
| `sea` | ship, destroyer, boat, warship… | — |
| `land` | tank, car, person… | **默认** |

类型高度参数从 `config/default.yaml` → `geo_target_profiles` 读取（车体偏移、舰高、机型参考尺寸）。

---

## 4. 第三步：斜距优先级

`_resolve_slant_range()` 按以下顺序取 `slant_range_m`：

| 优先级 | 条件 | `alt_source` | 置信度 |
|--------|------|--------------|--------|
| 1 | `laser_range_m` | `laser_range` | 0.92 |
| 2 | `radar_range_m` | `radar_range` | 0.88 |
| 3 | **仅空中**且无上面两项 | `bbox_size_prior` | 0.45 |
| 4 | 都没有 | 平面求交（无斜距） | 0.25~0.72 |

数据来源顺序：`det.metadata` → 帧 `metadata` → `batch.context`。

```157:179:agent/inference/geo_estimator.py
    laser = _first_float(
        det_meta.get("laser_range_m"),
        frame_meta.get("laser_range_m"),
        batch_context.get("laser_range_m"),
    )
    if laser is not None and laser > 0:
        return laser, "laser_range", 0.92

    radar_range = _first_float(
        det_meta.get("radar_range_m"),
        frame_meta.get("radar_range_m"),
        batch_context.get("radar_range_m"),
    )
    ...
    if domain == TargetDomain.AIR:
        ref_h = float(profile.get("reference_height_m", 5.0))
        est = _estimate_slant_from_bbox(bbox, ctx, ref_h)
```

空中 bbox 估距：用机型 `reference_height_m` 与 bbox 像素高度，按相似三角形反推斜距。

---

## 5. 第四步：按域计算高度 `alt_m`

主入口 `estimate_target_geo()`：

```188:265:agent/inference/geo_estimator.py
def estimate_target_geo(...) -> dict[str, Any]:
    domain = infer_target_domain(class_name, ...)
    ray = pixel_ray_enu(cx, cy, ctx)
    slant, alt_source, confidence = _resolve_slant_range(...)

    if slant is not None and alt_source in {"laser_range", "radar_range"}:
        raw = point_along_ray(ctx, ray, slant)
        if domain == TargetDomain.LAND:
            raw["alt_m"] = raw["alt_m"] + vertical_offset * 0.5
        elif domain == TargetDomain.SEA:
            raw["alt_m"] = max(raw["alt_m"], ctx.sea_surface_elevation_m + vertical_offset)
    elif domain == TargetDomain.AIR and slant is not None and alt_source == "bbox_size_prior":
        raw = point_along_ray(ctx, ray, slant)
    elif domain == TargetDomain.AIR:
        ...  # 弱先验兜底
    else:
        plane_alt = _surface_elevation(domain, ctx)
        raw = intersect_horizontal_plane(ctx, ray, plane_alt)
        raw["alt_m"] = plane_alt + vertical_offset
```

### 5.1 陆地 `land`

| 条件 | 高度策略 |
|------|----------|
| 有激光/雷达 | 公式 B → `alt_m = 几何点海拔 + 半个车体偏移` |
| 无测距 | 公式 A 与 `ground_elevation_m` 求交 → `alt_m = 地表海拔 + vertical_offset_m` |

示例：坦克 `120 + 2.5 = 122.5 m`。

### 5.2 海上 `sea`

| 条件 | 高度策略 |
|------|----------|
| 无测距 | 与 `sea_surface_elevation_m` 求交 → `alt_m = 海平面 + 舰桥/桅杆偏移` |
| 有激光/雷达 | 公式 B，且 `alt_m ≥ MSL + 舰高偏移` |

### 5.3 空中 `air`

| 条件 | 高度策略 |
|------|----------|
| 有激光/雷达 | 公式 B → 真空中高度 `alt_m = platform_alt + R × ray_z` |
| 无测距 | bbox + 机型参考高度估斜距 → 公式 B |
| 都没有 | 弱先验（低置信度，仅兜底） |

**注意**：空中目标不能用地表/海平面当高度；无测距时精度明显低于有激光/雷达。

### 5.4 决策树

```
有激光/雷达？
  ├─ 是 → 沿射线 R（陆/空/海）；海上再与 MSL+舰高 取较大
  └─ 否
        ├─ domain=air → bbox 估距？→ 沿射线 R : 弱先验
        ├─ domain=sea → 海平面求交 + 舰高偏移
        └─ domain=land → 地表求交 + 车体偏移
```

---

## 6. 第五步：跨帧平滑

同一 `track_id` 再次出现时，对 `lat` / `lon` / `alt_m` 加权平滑（默认 `geo_smooth_alpha=0.7`，即 70% 新值 + 30% 历史）：

```258:263:agent/inference/geo_estimator.py
    if prior_geo and all(k in prior_geo for k in ("lat", "lon", "alt_m")):
        a = smooth_alpha
        geo["lat"] = round(a * geo["lat"] + (1 - a) * float(prior_geo["lat"]), 6)
        geo["lon"] = round(a * geo["lon"] + (1 - a) * float(prior_geo["lon"]), 6)
        geo["alt_m"] = round(a * geo["alt_m"] + (1 - a) * float(prior_geo["alt_m"]), 1)
```

---

## 7. 输出字段

每个 track 的 `geo` 示例：

```json
{
  "lat": 30.519123,
  "lon": 114.376456,
  "alt_m": 122.5,
  "slant_range_m": 3080.2,
  "domain": "land",
  "alt_source": "ground_surface",
  "alt_confidence": 0.72,
  "geo_method": "land_surface_intersection",
  "vertical_offset_m": 2.5,
  "class_name": "tank"
}
```

| 字段 | 含义 |
|------|------|
| `lat` / `lon` | 目标水平位置（WGS84） |
| `alt_m` | 目标海拔（米） |
| `domain` | `land` / `air` / `sea` |
| `alt_source` | 高度来源：`ground_surface`、`laser_range`、`bbox_size_prior` 等 |
| `alt_confidence` | 高度可信度 0~1 |
| `geo_method` | 具体算法路径 |
| `vertical_offset_m` | 相对地表/海面的结构高度偏移 |
| `slant_range_m` | 沿视线的斜距（米） |

---

## 8. 前端 / Commander 载荷要求

### 8.1 帧 metadata（平台位姿，必填项建议）

通过 `attachments[].meta` 或 `sensor_frames[].metadata` 传入：

```json
{
  "sensor_id": "EO-FWD-1",
  "platform_lat": 30.518,
  "platform_lon": 114.375,
  "altitude_m": 3200.0,
  "heading_deg": 0.0,
  "depression_angle_deg": 75.0,
  "fov_deg": 45.0,
  "resolution": "1920x1080",
  "ground_elevation_m": 120.0,
  "sea_surface_elevation_m": 0.0
}
```

### 8.2 批次 context（区域兜底）

`payload_adapter` 会透传到 `SensorBatch.context`：

```133:137:tactical_intelligence_agent/payload_adapter.py
        "area_of_operations": upstream_context.get("area_of_operations"),
        "ground_elevation_m": upstream_context.get("ground_elevation_m"),
        "sea_surface_elevation_m": upstream_context.get("sea_surface_elevation_m"),
        "laser_range_m": upstream_context.get("laser_range_m"),
        "radar_range_m": upstream_context.get("radar_range_m"),
```

### 8.3 sendMessage 示例

```json
{
  "workflow_id": "wf-001",
  "work_item": "wf-001:process-intel",
  "command": "process_intelligence",
  "attachments": [{
    "id": "att-001",
    "uri": "s3://bucket/frame-001.jpg",
    "kind": "image",
    "mime_type": "image/jpeg",
    "meta": {
      "sensor_id": "EO-FWD-1",
      "platform_lat": 30.518,
      "platform_lon": 114.375,
      "altitude_m": 3200.0,
      "heading_deg": 0.0,
      "depression_angle_deg": 75.0,
      "fov_deg": 45.0,
      "resolution": "1920x1080",
      "ground_elevation_m": 120.0
    }
  }],
  "context": {
    "ground_elevation_m": 120.0,
    "sea_surface_elevation_m": 0.0,
    "laser_range_m": 8500.0
  }
}
```

联调内联帧：设置 `TIA_ALLOW_INLINE_FRAMES=1` 后可在 payload 中附加完整 `sensor_frames` 数组。

### 8.4 各场景数据建议

| 场景 | 建议传入 | 高度可靠性 |
|------|----------|------------|
| 陆地 | `ground_elevation_m` + 平台位姿；有激光更佳 | 中~高 |
| 海上 | `sea_surface_elevation_m` + 平台位姿 | 中 |
| 空中 | **尽量传 `laser_range_m` 或 `radar_range_m`** | 无测距时低 |

---

## 9. 配置项

`config/default.yaml` 中相关字段：

```yaml
inference:
  ground_elevation_m: 120.0
  sea_surface_elevation_m: 0.0
  default_platform_altitude_m: 3200.0
  default_platform_heading_deg: 0.0
  default_depression_angle_deg: 75.0
  default_fov_h_deg: 45.0
  geo_smooth_alpha: 0.7
  geo_target_profiles:
    land:
      tank: { vertical_offset_m: 2.5 }
      person: { vertical_offset_m: 1.8 }
    air:
      helicopter: { reference_height_m: 4.5 }
      drone: { reference_height_m: 1.2 }
    sea:
      destroyer: { vertical_offset_m: 28.0 }
      ship: { vertical_offset_m: 18.0 }
```

可通过 `geo_domain_overrides` 强制某 `class_name` 的作战域。

---

## 10. 从 sendMessage 到情报包的数据流

```
Commander sendMessage
  attachments[].meta  ──→  SensorFrame.metadata
  context             ──→  SensorBatch.context
        ↓
payload_adapter.commander_payload_to_batch()
        ↓
PerceptionSkill.execute()
  RT-DETR → EDL → MOTR+Kalman
        ↓
estimate_target_geo()  →  track.geo
        ↓
Detection.geo / SemanticIntelligencePacket.targets[].geo
```

---

## 11. 当前局限与后续扩展

| 项目 | 现状 | 建议扩展 |
|------|------|----------|
| 地表模型 | 水平面常量 `ground_elevation_m` | 接入 DEM：`alt_m = DEM(lat, lon)` |
| 域判定 | 感知阶段 `class_name` 关键词 | 认知分类结果回写或融合 |
| 空中无测距 | bbox 估距，置信度 ~0.45 | 雷达俯仰角、ADS-B |
| 时间对齐 | 假定位姿与帧同步 | 按时间戳插值 INS |

---

## 12. 运行测试

```powershell
cd D:\a2a_project\A2A-main
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_geolocation.py" -v
```

覆盖：域推断、陆地地表+偏移、海上 MSL+舰高、空中激光/bbox 估距等场景。
