"""Formal scenario: border UAV reconnaissance and personnel evacuation."""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_builder_support import (
    asset_profiles,
    bind_media_consumers,
    capture_plan,
    media_record,
    required_backend_roles,
    scenario_agents,
    sensor_capability,
    sensor_task,
)
from amos_platform.data.scenario_capabilities import planned_algorithms, planned_function_points
from amos_platform.domain.models import AssetSnapshot, ThreatSnapshot


SCENARIO_ID = "border-uav-evacuation"
MEDIA_ROOT = f"/static/assets/scenarios/{SCENARIO_ID}"
CHECKSUMS = {
    "00-border-search-overview.png": "f8b953d2979327fc1008b511b266231ebb6ffe0922bde2c9ece7025a09a42492",
    "01-personnel-ir.png": "a11c83d3adb1d48ad097f25f6ac64703b136da1d59e9bc7602de661c433d93e6",
    "02-vehicle-eo.png": "775358d40e7e3060dfdb4cf746d2ef02978f0df81f48d0acd7171ce39abf5cb5",
    "03-ground-radar-tracks.svg": "b5fd1e3c41c3c9efd1d694e54f6c764d0b6f7f2890755c5edb59e977f36fccde",
    "04-link-degradation.svg": "278d4117b9cbde9f3a3c29c34d8e4155086b8461012637c9d579e9d308254d83",
    "05-current-topology.svg": "dde1888ea690a3c4deb21e17a43e3d351b4e3840400f4b196e7653d524820cf4",
    "06-evacuation-route-current.svg": "8bd74ab85069f494c3e095bef8137b844a995985466b7f9531fcd3639be27e77",
    "07-evacuation-progress.png": "fe01f985089508befcb7692d1c356b29409f9f57d99e009bc11c1be9230e61bd",
}

PRODUCT_TYPES = {
    "BOR-MEDIA-00": "external_precollected",
    "BOR-MEDIA-01": "raw_sensor_frame",
    "BOR-MEDIA-02": "raw_sensor_frame",
    "BOR-MEDIA-03": "derived_sensor_product",
    "BOR-MEDIA-04": "derived_sensor_product",
    "BOR-MEDIA-05": "derived_sensor_product",
    "BOR-MEDIA-06": "command_product",
    "BOR-MEDIA-07": "raw_sensor_frame",
}


def _media_cues() -> list[dict[str, Any]]:
    rows = (
        ("BOR-MEDIA-00", "00-border-search-overview.png", 0, "FIND", "山地搜索区初始资料", "外部预采集演训资料记录山谷、道路和无人机展开区域，不包含人员或车辆识别结论。", "EXTERNAL-IMAGERY-02/EO", "eo_ir", "image/png"),
        ("BOR-MEDIA-01", "01-personnel-ir.png", 420, "FIND", "人员热源初始观测", "红外画面出现数个待核实人员热源，身份和状态尚待多源确认。", "UAV-SEARCH-02/IR", "ir", "image/png"),
        ("BOR-MEDIA-02", "02-vehicle-eo.png", 780, "FIX", "车辆目标初始观测", "光电画面形成一处山谷道路车辆当前帧，身份和用途由后端分类。", "UAV-TRACK-03/EO", "eo_ir", "image/png"),
        ("BOR-MEDIA-03", "03-ground-radar-tracks.svg", 1080, "TRACK", "山地监视雷达航迹记录", "地面监视雷达和道路传感器在 12 km 局部监视范围内形成独立观测。", "MOUNTAIN-RADAR-01/RADAR", "radar", "image/svg+xml"),
        ("BOR-MEDIA-04", "04-link-degradation.svg", 1470, "TARGET", "中继链路质量下降", "通信降级分支注入后，最近 420 秒窗口显示中继相关链路的 RSSI、SNR、时延和丢包变化；是否重分配中继任务由后端决定。", "RELAY-UAV-01/DATALINK", "telemetry", "image/svg+xml"),
        ("BOR-MEDIA-05", "05-current-topology.svg", 1800, "TARGET", "集群当前通信拓扑", "AMOS 冻结 T+30 分钟时的可达关系、链路指标、电量和任务占用，不显示未来调度结果。", "AMOS/NETWORK", "telemetry", "image/svg+xml"),
        ("BOR-MEDIA-06", "06-evacuation-route-current.svg", 2220, "ENGAGE", "撤离任务当前执行状态", "显示当前已执行路段、禁入区和民用区域；未执行路段不投影。", "COMMANDER/EXECUTION", "telemetry", "image/svg+xml"),
        ("BOR-MEDIA-07", "07-evacuation-progress.png", 2820, "ASSESS", "撤离区域复查", "二次光电观测记录人员和车辆的当前分布，供后端进行变化检测和闭环评估。", "UAV-VERIFY-04/EO", "eo_ir", "image/png"),
    )
    return [media_record(MEDIA_ROOT, mid, fn, at_sec=t, phase=p, title=title, caption=caption,
                         sensor_id=sensor, modality=modality, mime_type=mime, checksum=CHECKSUMS[fn],
                         capture_id=f"BOR-CAPTURE-{mid.rsplit('-', 1)[-1]}",
                         product_type=PRODUCT_TYPES[mid],
                         branch_ids=["communication_degraded"] if mid == "BOR-MEDIA-04" else ["*"])
            for mid, fn, t, p, title, caption, sensor, modality, mime in rows]


def _capture_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = [
        sensor_capability("BOR-CAP-EXT-EO", "EXTERNAL-IMAGERY-02", "EO", sensor_type="precollected_imagery",
                          modalities=["eo_ir"], source_kind="external_source",
                          parameters={"ground_sample_distance_m": 1.0, "collection_age_sec": 120}),
        sensor_capability("BOR-CAP-SEARCH-IR", "UAV-SEARCH-02", "IR", sensor_type="infrared",
                          modalities=["ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 9, "horizontal_fov_deg": 30, "spectral_band_um": [8, 12]}),
        sensor_capability("BOR-CAP-TRACK-EO", "UAV-TRACK-03", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
        sensor_capability("BOR-CAP-GROUND-RADAR", "MOUNTAIN-RADAR-01", "RADAR", sensor_type="ground_surveillance_radar",
                          modalities=["radar"], configured_sensor="GROUND-RADAR",
                          parameters={"instrumented_range_km": 12, "azimuth_coverage_deg": 120, "range_resolution_m": 35}),
        sensor_capability("BOR-CAP-RELAY-LINK", "RELAY-UAV-01", "DATALINK", sensor_type="link_monitor",
                          modalities=["telemetry"], configured_sensor="DATALINK",
                          parameters={"sample_interval_sec": 5, "observation_window_sec": 180, "metrics": ["rssi", "snr", "latency", "packet_loss"]}),
        sensor_capability("BOR-CAP-NETWORK", "AMOS", "NETWORK", sensor_type="network_topology_processor",
                          modalities=["telemetry"], source_kind="simulation_processor",
                          parameters={"refresh_interval_sec": 30, "current_time_only": True}),
        sensor_capability("BOR-CAP-EXECUTION", "COMMANDER", "EXECUTION", sensor_type="command_status",
                          modalities=["telemetry"], source_kind="command_system",
                          parameters={"current_time_only": True, "simulation_execution_only": True}),
        sensor_capability("BOR-CAP-VERIFY-EO", "UAV-VERIFY-04", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
    ]
    by_id = {row["capability_id"]: row for row in capabilities}
    specs = (
        ("BOR-TASK-00", "BOR-CAP-EXT-EO", "ingest", 0, 60, [], ["*"]),
        ("BOR-TASK-01", "BOR-CAP-SEARCH-IR", "capture", 360, 480, ["CONTACT-PERSONNEL-01"], ["*"]),
        ("BOR-TASK-02", "BOR-CAP-TRACK-EO", "capture", 720, 840, ["CONTACT-VEHICLE-01"], ["*"]),
        ("BOR-TASK-03", "BOR-CAP-GROUND-RADAR", "derive", 1020, 1110, ["CONTACT-PERSONNEL-01", "CONTACT-VEHICLE-01"], ["*"]),
        ("BOR-TASK-04", "BOR-CAP-RELAY-LINK", "derive", 1440, 1500, [], ["communication_degraded"]),
        ("BOR-TASK-05", "BOR-CAP-NETWORK", "derive", 1740, 1830, [], ["*"]),
        ("BOR-TASK-06", "BOR-CAP-EXECUTION", "command_product", 2160, 2250, ["CONTACT-PERSONNEL-01", "CONTACT-VEHICLE-01"], ["*"]),
        ("BOR-TASK-07", "BOR-CAP-VERIFY-EO", "capture", 2760, 2850, ["CONTACT-PERSONNEL-01", "CONTACT-VEHICLE-01"], ["*"]),
    )
    tasks = [sensor_task(task_id, by_id[capability_id], task_type=task_type, start_sec=start,
                         end_sec=end, target_refs=targets, branch_ids=branches)
             for task_id, capability_id, task_type, start, end, targets, branches in specs]
    media_by_id = {row["media_id"]: row for row in _media_cues()}
    target_map = {task_id.replace("BOR-TASK", "BOR-MEDIA"): refs for task_id, _, _, _, _, refs, _ in specs}
    parameters = {
        "BOR-MEDIA-00": {"ground_sample_distance_m": 1.0, "collection_age_sec": 120, "precollected": True, "evidence_scope": "area_overview", "resolution_px": [1672, 941]},
        "BOR-MEDIA-01": {"effective_range_nm": 9, "horizontal_fov_deg": 30, "look_angle_deg": 32, "spectral_band_um": [8, 12], "point_at_target": True, "resolution_px": [1672, 941]},
        "BOR-MEDIA-02": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 6, "point_at_target": True, "resolution_px": [1672, 941]},
        "BOR-MEDIA-03": {"instrumented_range_km": 12, "azimuth_coverage_deg": 120, "range_resolution_m": 35, "observation_window_sec": 120, "data_source": "sensor_observations", "renderer_type": "radar_ppi"},
        "BOR-MEDIA-04": {"sample_interval_sec": 5, "observation_window_sec": 420, "fault_precedes_capture": True, "data_source": "network", "requires_observation": False, "renderer_type": "link_monitor"},
        "BOR-MEDIA-05": {"input_cutoff_sec": 1800, "refresh_interval_sec": 30, "future_data_included": False, "data_source": "network", "requires_observation": False, "renderer_type": "network_topology"},
        "BOR-MEDIA-06": {"input_cutoff_sec": 2220, "simulation_execution_only": True, "data_source": "task_state", "renderer_type": "execution_state"},
        "BOR-MEDIA-07": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 8, "point_at_target": True, "resolution_px": [1672, 941]},
    }
    captures = []
    for index, task in enumerate(tasks):
        media_id = f"BOR-MEDIA-{index:02d}"
        item = media_by_id[media_id]
        captures.append(capture_plan(
            item["capture_id"], media_id, by_id[task["capability_id"]], task,
            product_type=item["product_type"], at_sec=item["at_sec"],
            target_refs=target_map[media_id], parameters=parameters[media_id],
            branch_ids=item["branch_ids"],
        ))
    return capabilities, tasks, captures


def _timeline() -> list[dict[str, Any]]:
    return [
        {"cue_id": "BOR-CUE-01", "at_sec": 0, "phase": "FIND", "level": "INFO", "title": "无人机集群展开搜索", "description": "四架无人机、中继平台和地面传感器开始分区作业。", "media_ids": ["BOR-MEDIA-00"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M11", "M17"]},
        {"cue_id": "BOR-CUE-02", "at_sec": 420, "phase": "FIND", "level": "INFO", "title": "人员热源观测形成", "description": "单一红外源形成初始检测，身份仍保持未知。", "media_ids": ["BOR-MEDIA-01"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M01", "M06", "M15", "M17"]},
        {"cue_id": "BOR-CUE-03", "at_sec": 780, "phase": "FIX", "level": "INFO", "title": "道路车辆观测形成", "description": "独立光电源形成车辆观测，可进行概率分类和跨源核验。", "media_ids": ["BOR-MEDIA-02"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M07", "M19"]},
        {"cue_id": "BOR-CUE-04", "at_sec": 1080, "phase": "TRACK", "level": "INFO", "title": "多源航迹达到评估条件", "description": "地面与空中观测已到达，可维持人员和车辆航迹并评估区域风险。", "media_ids": ["BOR-MEDIA-03"], "functional_agent_ids": ["A2"], "model_requirement_ids": ["M05", "M16", "M19", "M20"]},
        {"cue_id": "BOR-CUE-05", "at_sec": 1470, "phase": "TARGET", "level": "WARNING", "title": "中继链路质量下降", "description": "通信降级分支已经注入环境变化；中继失效与接管必须由后端证据确认。", "media_ids": ["BOR-MEDIA-04"], "functional_agent_ids": ["A3"], "model_requirement_ids": ["M02", "M03", "M12", "M13"], "branch_ids": ["communication_degraded"]},
        {"cue_id": "BOR-CUE-06", "at_sec": 1800, "phase": "TARGET", "level": "INFO", "title": "调度与路线约束输入就绪", "description": "当前拓扑、电量、禁入区和民用区域可供方案生成与合规审查。", "media_ids": ["BOR-MEDIA-05"], "functional_agent_ids": ["A3", "A4", "A5"], "model_requirement_ids": ["M04", "M09", "M10", "M11", "M14"]},
        {"cue_id": "BOR-CUE-07", "at_sec": 2220, "phase": "ENGAGE", "level": "INFO", "title": "撤离任务进入模拟执行阶段", "description": "AMOS 推进已进入执行阶段的模拟资源，并持续反馈当前状态；后端结论以工作流证据为准。", "media_ids": ["BOR-MEDIA-06"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M03", "M16"]},
        {"cue_id": "BOR-CUE-08", "at_sec": 2820, "phase": "ASSESS", "level": "INFO", "title": "撤离区域复查资料到达", "description": "前后观测可供变化检测；任务完成度由后端闭环评估。", "media_ids": ["BOR-MEDIA-07"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M08", "M14", "M18"]},
    ]


def build_border_uav_evacuation_scenario() -> dict[str, Any]:
    assets = [
        AssetSnapshot("EDGE-C2-01", "ground", "山地任务指挥节点", "active", 23.68, 121.16, sensors=["COMINT", "MISSION-SERVER"], endurance_hr=9999, autonomy_tier=1, health={"battery_pct": 100, "comms_strength": 96}),
        AssetSnapshot("UAV-LEAD-01", "air", "集群任务长机", "active", 23.78, 121.08, alt_ft=7500, heading=80, speed_kts=85, sensors=["EO/IR", "SAR"], endurance_hr=16, autonomy_tier=4, health={"battery_pct": 91, "comms_strength": 91}),
        AssetSnapshot("UAV-SEARCH-02", "air", "人员搜索无人机", "active", 23.84, 121.22, alt_ft=5200, heading=130, speed_kts=72, sensors=["EO/IR"], endurance_hr=12, autonomy_tier=4, health={"battery_pct": 88, "comms_strength": 84}),
        AssetSnapshot("UAV-TRACK-03", "air", "车辆跟踪无人机", "active", 23.72, 121.34, alt_ft=6000, heading=230, speed_kts=76, sensors=["EO/IR", "RADAR"], endurance_hr=13, autonomy_tier=4, health={"battery_pct": 86, "comms_strength": 82}),
        AssetSnapshot("UAV-VERIFY-04", "air", "撤离路线核验无人机", "active", 23.57, 121.20, alt_ft=4800, heading=20, speed_kts=70, sensors=["EO/IR", "SAR"], endurance_hr=14, autonomy_tier=4, health={"battery_pct": 93, "comms_strength": 89}),
        AssetSnapshot("RELAY-UAV-01", "air", "固定翼通信中继", "active", 23.90, 121.12, alt_ft=10500, heading=160, speed_kts=95, sensors=["DATALINK", "COMINT"], endurance_hr=18, autonomy_tier=3, health={"battery_pct": 90, "comms_strength": 95}),
        AssetSnapshot("MOUNTAIN-RADAR-01", "ground", "山地监视雷达", "active", 23.82, 121.28, sensors=["GROUND-RADAR", "EO"], endurance_hr=9999, autonomy_tier=1, health={"battery_pct": 100, "comms_strength": 92}),
        AssetSnapshot("GROUND-PATROL-01", "ground", "地面搜救分队", "active", 23.58, 121.15, heading=35, speed_kts=18, sensors=["EO", "DATALINK"], endurance_hr=20, autonomy_tier=1, health={"fuel_pct": 84, "comms_strength": 83}),
    ]
    threats = [
        ThreatSnapshot("CONTACT-PERSONNEL-01", "待核实人员目标", "ground", 23.73, 121.23, heading=210, speed_kts=2, risk_level="UNKNOWN", rcs_dbsm=0.2, ir_signature="medium", iff_status="unknown", ais_match=False, behavior_script={"phases": [{"phase": 1, "name": "沿山径向西南移动", "duration_sec": 600, "heading": 210, "speed_kts": 2, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "在林缘短暂停留", "duration_sec": 600, "heading": 210, "speed_kts": 0, "risk_level": "UNKNOWN"}, {"phase": 3, "name": "沿支路向北移动", "duration_sec": 900, "heading": 15, "speed_kts": 1.5, "risk_level": "UNKNOWN"}, {"phase": 4, "name": "接近临时集合点", "duration_sec": 900, "heading": 55, "speed_kts": 0.8, "risk_level": "UNKNOWN"}]}),
        ThreatSnapshot("CONTACT-VEHICLE-01", "待分类车辆目标", "ground", 23.84, 121.34, heading=220, speed_kts=18, risk_level="UNKNOWN", rcs_dbsm=5, ir_signature="medium", iff_status="unknown", ais_match=False, behavior_script={"phases": [{"phase": 1, "name": "沿山谷道路向西南机动", "duration_sec": 750, "heading": 220, "speed_kts": 18, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "转入东南向支路", "duration_sec": 750, "heading": 155, "speed_kts": 14, "risk_level": "UNKNOWN"}, {"phase": 3, "name": "沿谷地道路向东北调整", "duration_sec": 750, "heading": 40, "speed_kts": 10, "risk_level": "UNKNOWN"}, {"phase": 4, "name": "在道路节点停止", "duration_sec": 750, "heading": 40, "speed_kts": 0, "risk_level": "UNKNOWN"}]}),
    ]
    routes = {
        "EDGE-C2-01": [], "MOUNTAIN-RADAR-01": [],
        "UAV-LEAD-01": [{"lat": 23.84, "lng": 121.18}, {"lat": 23.78, "lng": 121.34}, {"lat": 23.66, "lng": 121.30}, {"lat": 23.68, "lng": 121.12}],
        "UAV-SEARCH-02": [{"lat": 23.81, "lng": 121.28}, {"lat": 23.73, "lng": 121.23}, {"lat": 23.65, "lng": 121.16}],
        "UAV-TRACK-03": [{"lat": 23.78, "lng": 121.30}, {"lat": 23.72, "lng": 121.24}, {"lat": 23.64, "lng": 121.20}],
        "UAV-VERIFY-04": [{"lat": 23.63, "lng": 121.20}, {"lat": 23.68, "lng": 121.15}],
        "RELAY-UAV-01": [{"lat": 23.94, "lng": 121.18}, {"lat": 23.88, "lng": 121.32}, {"lat": 23.74, "lng": 121.36}, {"lat": 23.68, "lng": 121.18}],
        "GROUND-PATROL-01": [{"lat": 23.62, "lng": 121.17}, {"lat": 23.67, "lng": 121.20}, {"lat": 23.71, "lng": 121.22}],
    }
    route_modes = {
        "EDGE-C2-01": "hold", "MOUNTAIN-RADAR-01": "hold",
        "UAV-LEAD-01": "loop", "UAV-SEARCH-02": "loop", "UAV-TRACK-03": "loop",
        "UAV-VERIFY-04": "hold", "RELAY-UAV-01": "loop", "GROUND-PATROL-01": "hold",
    }
    motion_windows = {
        "GROUND-PATROL-01": {"start_sec": 1080},
        "UAV-VERIFY-04": {"start_sec": 1980},
    }
    asset_capabilities, asset_task_schedule, capture_plans = _capture_contract()
    timeline = _timeline()
    media_cues = bind_media_consumers(_media_cues(), timeline)
    return {
        "schema_version": "amos.scenario.v2", "id": SCENARIO_ID,
        "name": "无人机集群山地侦察与人员撤离保障",
        "operator_brief": "无人机集群在台湾东部山地演训区和链路受扰条件下搜索人员与车辆；身份判断、动态调度、撤离路线、约束审查及闭环结果均由 Commander 返回。",
        "description": "验证山地多无人机感知、联邦状态同步、人员与车辆航迹、通信中继失效、任务重分配、道路与民用区域约束和撤离区域变化检测。",
        "scenario_type": "scripted_agent_demo",
        "theater": {"theater_id": "taiwan_east_mountain_evacuation_area", "name": "台湾东部山地搜索与撤离仿真区", "location_profile": "fictional_training_area", "center": {"lat": 23.73, "lng": 121.22}, "zoom": 11, "ao": {"north": 23.98, "south": 23.48, "east": 121.42, "west": 121.02}},
        "map_display": {"default_layers": {"sensors": False, "ao": True}, "track_style": "tactical_local", "base_surface": "land"},
        "environment": {"weather": "overcast", "visibility_nm": 7, "wind_speed_kts": 12, "wind_direction_deg": 70, "precipitation": "light_rain", "terrain": "mountain_valley", "road_condition": "wet", "communications": "intermittent", "civilian_area": "SIM-CIV-02", "restricted_area": "SIM-NO-GO-02"},
        "asset_routes": routes, "asset_route_modes": route_modes,
        "asset_motion_windows": motion_windows,
        "asset_profiles": asset_profiles(assets),
        "asset_capabilities": asset_capabilities,
        "asset_task_schedule": asset_task_schedule,
        "capture_plans": capture_plans,
        "threat_observation_windows": {
            "CONTACT-PERSONNEL-01": {"start_sec": 420},
            "CONTACT-VEHICLE-01": {"start_sec": 780},
        },
        "assets": assets, "threats": threats,
        "protected_assets": [{"asset_id": "EVAC-GROUP-01", "asset_name": "待撤离人员组", "asset_type": "personnel_group", "lat": 23.73, "lon": 121.23, "alt": 0, "protection_radius_m": 1200, "criticality": 0.95, "status": "location_unverified", "metadata": {"simulation_only": True, "location_profile": "fictional_training_area"}}],
        "timeline": timeline, "media_cues": media_cues, "cover_media_id": "BOR-MEDIA-00",
        "demo_controls": {"recommended_speed": 16, "duration_sec": 3000, "auto_agent_interval_sec": 300, "show_truth": False, "latest_visual_only": True, "auto_stop": True},
        "default_seed": 44041, "supported_modes": ["integration", "demonstration"],
        "functional_agents": scenario_agents(), "required_agents": required_backend_roles(),
        "algorithm_coverage": planned_algorithms("clustering", "association", "linear_regression", "logistic_regression", "random_forest", "neural_network", "naive_bayes_network", "generative_adversarial_network", "large_language_model", "retrieval_augmented_generation", "agent_collaboration", "federated_learning", "reinforcement_learning", "explainable_ai", "multimodal_fusion", "time_series_prediction", "real_time_object_detection", "change_detection", "multi_target_tracking", "graph_neural_network"),
        "function_point_coverage": planned_function_points(*[f"KC-{i:02d}" for i in range(1, 29)]),
        "demo_checkpoints": [
            {"checkpoint_id": "BOR-CP-DETECT", "title": "人员和车辆观测就绪", "min_elapsed_sec": 810, "conditions": {"stable_track_count_at_least": 2, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["BOR-MEDIA-01", "BOR-MEDIA-02"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "BOR-CP-TRACK", "title": "多源航迹输入就绪", "min_elapsed_sec": 1110, "conditions": {"stable_track_count_at_least": 2, "minimum_track_confidence": 0.6, "minimum_track_samples": 3, "media_ids_released": ["BOR-MEDIA-03"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "BOR-CP-LINK", "title": "链路降级与调度输入就绪", "min_elapsed_sec": 1500, "conditions": {"media_ids_released": ["BOR-MEDIA-04"]}, "pause": True, "submit_analysis": True, "branch_ids": ["communication_degraded"]},
            {"checkpoint_id": "BOR-CP-PLAN", "title": "撤离方案约束输入就绪", "min_elapsed_sec": 1830, "conditions": {"media_ids_released": ["BOR-MEDIA-05"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "BOR-CP-CLOSE", "title": "撤离区域复查输入就绪", "min_elapsed_sec": 2850, "conditions": {"media_ids_released": ["BOR-MEDIA-07"]}, "pause": True, "submit_analysis": True},
        ],
        "fault_injections": [
            {"fault_id": "BOR-FAULT-LINK", "type": "communication_degradation", "target": "RELAY-UAV-01", "at_checkpoint": "BOR-CP-TRACK", "status": "available"},
            {"fault_id": "BOR-FAULT-RELAY", "type": "agent_unavailable", "target": "RELAY-UAV-01", "at_checkpoint": "BOR-CP-TRACK", "status": "available"},
        ],
        "expected_branches": [
            {"branch_id": "standard", "name": "标准撤离", "description": "链路和平台保持可用。"},
            {"branch_id": "communication_degraded", "name": "通信干扰", "description": "注入链路降级并检验任务重分配。"},
            {"branch_id": "agent_failure", "name": "中继平台失效", "description": "请求后端验证 A3/A4 重新调度和备用路线。"},
            {"branch_id": "low_confidence", "name": "低置信度观测", "description": "维持未知状态并追加观测；困难样本仅进入事后训练闭环。"},
        ],
        "default_branch": "standard",
        "acceptance_profile": {"functional_agents": 6, "core_algorithms": 16, "engineering_models": 4, "function_points": 28, "evidence_policy": "backend_trace_only"},
        "agent_plan": {"mode": "commander_workflow", "steps": ["submit_current_snapshot", "execute_a1_a6_workflow", "project_run_scoped_evidence"]},
        "events": [row["title"] for row in _timeline()],
    }


__all__ = ["SCENARIO_ID", "build_border_uav_evacuation_scenario"]
