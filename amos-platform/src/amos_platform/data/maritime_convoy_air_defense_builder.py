"""Formal scenario: maritime transport convoy escort and point air defence."""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_builder_support import (
    asset_profiles,
    agent_deployment,
    bind_media_consumers,
    compute_node,
    capture_plan,
    media_record,
    physical_devices,
    required_backend_roles,
    scenario_agents,
    sensor_capability,
    sensor_task,
)
from amos_platform.data.scenario_capabilities import planned_algorithms, planned_function_points
from amos_platform.domain.models import AssetSnapshot, ThreatSnapshot


SCENARIO_ID = "maritime-convoy-air-defense"
MEDIA_ROOT = f"/static/assets/scenarios/{SCENARIO_ID}"
CHECKSUMS = {
    "00-convoy-overview.png": "81235f981034c097678c071a5d0b80cbdfa166521b486a8d44edc406e552406f",
    "01-aew-radar-picture.svg": "c66106d37a6004688c64eaad188eb895775efa401c454c3b5c961f95116e125c",
    "02-civil-traffic-correlation.svg": "afe0f5e360562e2d3de4578c900ed288fe46fdabf987bcd82aab2e11b6cf72c1",
    "03-fast-surface-contact.png": "5fcd2ea562e4a8e613fa538d5b53d252b0848f2d7576ec6c10ee887cf843a0c5",
    "04-low-altitude-contact-ir.png": "aadc7052ac1035f5edf2423f548b0df8d2dd8c13582365d7eb8cd304baba2047",
    "05-elint-interference.svg": "8e4c9eb10ea1a4ef1597a279ea736415e88a542060e3b1187c4728b64457cf72",
    "06-convoy-maneuver-current.svg": "58eeb714d6ef7727833d51124d59e72bb6487dad82f0e4595b221bd9d048cb8f",
    "07-post-maneuver-observation.png": "64b1aca4782d7d466803406edbcfd3513958318b285f86041822747728d72a04",
}

PRODUCT_TYPES = {
    "MAR-MEDIA-00": "external_precollected",
    "MAR-MEDIA-01": "derived_sensor_product",
    "MAR-MEDIA-02": "derived_sensor_product",
    "MAR-MEDIA-03": "raw_sensor_frame",
    "MAR-MEDIA-04": "raw_sensor_frame",
    "MAR-MEDIA-05": "derived_sensor_product",
    "MAR-MEDIA-06": "command_product",
    "MAR-MEDIA-07": "raw_sensor_frame",
}


def _media_cues() -> list[dict[str, Any]]:
    rows = (
        ("MAR-MEDIA-00", "00-convoy-overview.png", 0, "FIND", "海上编队初始态势", "外部预采集光电资料记录编队、护航舰与周边民船的初始相对位置，所有外部接触均保持待识别。", "EXTERNAL-IMAGERY-01/EO", "eo_ir", "image/png"),
        ("MAR-MEDIA-01", "01-aew-radar-picture.svg", 720, "FIND", "双目标雷达批次", "侦察无人机形成两个海面接触的雷达批次，两者身份、属性和威胁等级均保持未知。", "AEW-01/AEW_RADAR", "radar", "image/svg+xml"),
        ("MAR-MEDIA-02", "02-civil-traffic-correlation.svg", 1440, "FIX", "AIS 与雷达关联记录", "AIS、岸基雷达和舰载雷达提供两个海面接触的跨源记录，后端负责区分敌方资源与渔船。", "SHORE-RADAR-01/AIS-RADAR", "telemetry", "image/svg+xml"),
        ("MAR-MEDIA-03", "03-fast-surface-contact.png", 2160, "TRACK", "高速海面目标光电复核", "舰载光电形成高速接触当前帧，外形、航速和接近行为供后端识别，前端不预设敌我结论。", "ESCORT-01/EO-IR", "eo_ir", "image/png"),
        ("MAR-MEDIA-04", "04-low-altitude-contact-ir.png", 2880, "TRACK", "渔船目标红外复核", "护航舰光电红外形成慢速海面接触的当前帧，供后端确认渔船身份并建立禁射约束。", "ESCORT-01/IR", "ir", "image/png"),
        ("MAR-MEDIA-05", "05-elint-interference.svg", 3600, "TARGET", "敌方目标辐射源复核", "护航舰电子侦察载荷冻结高速目标的当前频谱观测，供后端完成目标优先级、交战规则和武器方案审查。", "ESCORT-01/ELINT", "telemetry", "image/svg+xml"),
        ("MAR-MEDIA-06", "06-convoy-maneuver-current.svg", 4560, "ENGAGE", "武器攻击命令执行状态", "仅在后端完成敌方识别、渔船排除且操作员明确授权后，展示当前模拟发射和武器飞行状态。", "COMMANDER/EXECUTION", "telemetry", "image/svg+xml"),
        ("MAR-MEDIA-07", "07-post-maneuver-observation.png", 5580, "ASSESS", "攻击后效果评估", "补充侦察无人机在目标西南约 1.96 海里处冻结光电画面，记录高速攻击艇已毁、停航并局部燃烧的状态；渔船位于镜头视场外且保持安全。", "UAV-CONFIRM-01/EO", "eo_ir", "image/png"),
    )
    return [
        media_record(
            MEDIA_ROOT, media_id, filename, at_sec=at_sec, phase=phase, title=title,
            caption=caption, sensor_id=sensor, modality=modality,
            mime_type=mime_type, checksum=CHECKSUMS[filename],
            capture_id=f"MAR-CAPTURE-{media_id.rsplit('-', 1)[-1]}",
            product_type=PRODUCT_TYPES[media_id],
        )
        for media_id, filename, at_sec, phase, title, caption, sensor, modality, mime_type in rows
    ]


def _capture_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = [
        sensor_capability("MAR-CAP-EXT-EO", "EXTERNAL-IMAGERY-01", "EO", sensor_type="precollected_imagery",
                          modalities=["eo_ir"], source_kind="external_source",
                          parameters={"ground_sample_distance_m": 1.5, "collection_age_sec": 300}),
        sensor_capability("MAR-CAP-AEW-RADAR", "AEW-01", "AEW_RADAR", sensor_type="airborne_early_warning_radar",
                          modalities=["radar"], configured_sensor="AEW_RADAR",
                          parameters={"instrumented_range_nm": 120, "azimuth_coverage_deg": 360, "range_resolution_m": 120}),
        sensor_capability("MAR-CAP-SHORE-CORRELATION", "SHORE-RADAR-01", "AIS-RADAR", sensor_type="ais_radar_correlator",
                          modalities=["telemetry"], configured_sensor="AESA_RADAR",
                          parameters={"radar_range_nm": 40, "ais_range_nm": 40, "association_gate_nm": 1.2, "source_sensors": ["AESA_RADAR", "AIS"]}),
        sensor_capability("MAR-CAP-ESCORT-EO", "ESCORT-01", "EO-IR", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
        sensor_capability("MAR-CAP-ESCORT-IR", "ESCORT-01", "IR", sensor_type="infrared",
                          modalities=["ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 16, "horizontal_fov_deg": 24, "spectral_band_um": [8, 12]}),
        sensor_capability("MAR-CAP-ESCORT-ELINT", "ESCORT-01", "ELINT", sensor_type="elint",
                          modalities=["telemetry"], configured_sensor="ELINT",
                          parameters={"frequency_band_mhz": [500, 8000], "bearing_error_deg": 4, "observation_window_sec": 180}),
        sensor_capability("MAR-CAP-EXECUTION", "COMMANDER", "EXECUTION", sensor_type="command_status",
                          modalities=["telemetry"], source_kind="command_system",
                          parameters={"current_time_only": True, "simulation_execution_only": True}),
        sensor_capability("MAR-CAP-CONFIRM-EO", "UAV-CONFIRM-01", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
    ]
    by_id = {row["capability_id"]: row for row in capabilities}
    specs = (
        ("MAR-TASK-00", "MAR-CAP-EXT-EO", "ingest", 0, 1, [], ["*"]),
        ("MAR-TASK-01", "MAR-CAP-AEW-RADAR", "derive", 660, 750, ["CONTACT-HOSTILE-01", "CONTACT-FISHING-01"], ["*"]),
        ("MAR-TASK-02", "MAR-CAP-SHORE-CORRELATION", "derive", 1380, 1470, ["CONTACT-HOSTILE-01", "CONTACT-FISHING-01"], ["*"]),
        ("MAR-TASK-03", "MAR-CAP-ESCORT-EO", "capture", 2100, 2190, ["CONTACT-HOSTILE-01"], ["*"]),
        ("MAR-TASK-04", "MAR-CAP-ESCORT-IR", "capture", 2820, 2910, ["CONTACT-FISHING-01"], ["*"]),
        ("MAR-TASK-05", "MAR-CAP-ESCORT-ELINT", "derive", 3540, 3630, ["CONTACT-HOSTILE-01"], ["*"]),
        ("MAR-TASK-06", "MAR-CAP-EXECUTION", "command_product", 4500, 4590, ["CONTACT-HOSTILE-01"], ["*"]),
        ("MAR-TASK-07", "MAR-CAP-CONFIRM-EO", "capture", 5520, 5610, ["CONTACT-HOSTILE-01"], ["*"]),
    )
    tasks = [sensor_task(task_id, by_id[capability_id], task_type=task_type, start_sec=start,
                         end_sec=end, target_refs=targets, branch_ids=branches)
             for task_id, capability_id, task_type, start, end, targets, branches in specs]
    media_by_id = {row["media_id"]: row for row in _media_cues()}
    target_map = {task_id.replace("MAR-TASK", "MAR-MEDIA"): refs for task_id, _, _, _, _, refs, _ in specs}
    parameters = {
        "MAR-MEDIA-00": {"ground_sample_distance_m": 1.5, "collection_age_sec": 300, "precollected": True, "resolution_px": [1672, 941]},
        "MAR-MEDIA-01": {"instrumented_range_nm": 120, "azimuth_coverage_deg": 360, "range_resolution_m": 120, "observation_window_sec": 60, "data_source": "sensor_observations", "renderer_type": "radar_ppi"},
        "MAR-MEDIA-02": {"radar_range_nm": 40, "ais_range_nm": 40, "association_gate_nm": 1.2, "source_sensors": ["AESA_RADAR", "AIS"], "data_source": "sensor_observations", "renderer_type": "ais_radar_correlation"},
        "MAR-MEDIA-03": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 0, "point_at_target": True, "registration_group": "MAR-SURFACE-CONTACT-A", "frame_role": "initial_surface_eo", "resolution_px": [1672, 941]},
        "MAR-MEDIA-04": {"effective_range_nm": 16, "horizontal_fov_deg": 24, "look_angle_deg": 0, "spectral_band_um": [8, 12], "point_at_target": True, "resolution_px": [1672, 941]},
        "MAR-MEDIA-05": {"frequency_band_mhz": [500, 8000], "observation_window_sec": 180, "bearing_error_deg": 4, "data_source": "sensor_observations", "renderer_type": "elint_spectrum"},
        "MAR-MEDIA-06": {"input_cutoff_sec": 4560, "simulation_execution_only": True, "data_source": "task_state", "renderer_type": "execution_state"},
        "MAR-MEDIA-07": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 34, "point_at_target": True, "required_damage_state": "destroyed", "registration_group": "MAR-SURFACE-CONTACT-A", "reference_media_id": "MAR-MEDIA-03", "frame_role": "post_maneuver_eo", "subject_asset_ids": ["MERCHANT-01", "MERCHANT-02", "ESCORT-01"], "resolution_px": [1672, 941]},
    }
    captures = []
    for index, task in enumerate(tasks):
        media_id = f"MAR-MEDIA-{index:02d}"
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
        {"cue_id": "MAR-CUE-01", "at_sec": 0, "phase": "FIND", "level": "INFO", "title": "海上编队进入责任区", "description": "编队、护航舰和民用航道交通进入当前态势。", "media_ids": ["MAR-MEDIA-00"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M17"]},
        {"cue_id": "MAR-CUE-02", "at_sec": 720, "phase": "FIND", "level": "INFO", "title": "发现两个待识别海面目标", "description": "侦察无人机、岸基和舰载雷达形成两个相互独立的未知海面航迹。", "media_ids": ["MAR-MEDIA-01"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M01", "M15", "M17"]},
        {"cue_id": "MAR-CUE-03", "at_sec": 1440, "phase": "FIX", "level": "INFO", "title": "双目标跨源关联完成", "description": "AIS 与多部雷达记录已到达，可由后端分别固定目标位置并保持身份未知。", "media_ids": ["MAR-MEDIA-02"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M06", "M07", "M19"]},
        {"cue_id": "MAR-CUE-04", "at_sec": 2160, "phase": "TRACK", "level": "WARNING", "title": "高速目标持续接近编队", "description": "高速海面目标形成连续航迹与光电证据，但前端不预设其敌我属性。", "media_ids": ["MAR-MEDIA-03"], "functional_agent_ids": ["A2"], "model_requirement_ids": ["M05", "M16", "M19", "M20"]},
        {"cue_id": "MAR-CUE-05", "at_sec": 2880, "phase": "TRACK", "level": "INFO", "title": "慢速目标渔船特征形成", "description": "第二目标形成渔船外形、低速航行和 AIS 关联证据，等待后端给出民用识别结论。", "media_ids": ["MAR-MEDIA-04"], "functional_agent_ids": ["A2"], "model_requirement_ids": ["M03", "M05", "M16"]},
        {"cue_id": "MAR-CUE-06", "at_sec": 3600, "phase": "TARGET", "level": "WARNING", "title": "敌方目标与禁射目标完成区分", "description": "后端应把高速目标识别为敌方资源并生成攻击候选，同时把渔船列入禁射清单，完成规则和武器方案审查。", "media_ids": ["MAR-MEDIA-05"], "functional_agent_ids": ["A3", "A4", "A5"], "model_requirement_ids": ["M02", "M09", "M10", "M11", "M13", "M14"]},
        {"cue_id": "MAR-CUE-07", "at_sec": 4560, "phase": "ENGAGE", "level": "WARNING", "title": "等待并执行明确攻击命令", "description": "仅在识别、禁射校验和操作员授权全部满足后，AMOS 才执行舰载反舰导弹模拟发射。", "media_ids": ["MAR-MEDIA-06"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M04", "M16"]},
        {"cue_id": "MAR-CUE-08", "at_sec": 5580, "phase": "ASSESS", "level": "INFO", "title": "完成攻击效果与附带风险复核", "description": "核验无人机形成攻击后证据，检查敌方目标毁伤状态与渔船安全，再决定结束或重新攻击。", "media_ids": ["MAR-MEDIA-07"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M14", "M18"]},
    ]


def build_maritime_convoy_air_defense_scenario() -> dict[str, Any]:
    assets = [
        AssetSnapshot("MERCHANT-01", "maritime", "运输船一号", "active", 22.10, 121.28, heading=55, speed_kts=14, sensors=["AIS", "NAV-RADAR"], endurance_hr=240, autonomy_tier=1, health={"fuel_pct": 82, "comms_strength": 91}),
        AssetSnapshot("MERCHANT-02", "maritime", "运输船二号", "active", 22.06, 121.24, heading=55, speed_kts=14, sensors=["AIS", "NAV-RADAR"], endurance_hr=220, autonomy_tier=1, health={"fuel_pct": 79, "comms_strength": 89}),
        AssetSnapshot("ESCORT-01", "maritime", "编队护航舰", "active", 22.14, 121.22, heading=55, speed_kts=15, sensors=["AESA_RADAR", "EO/IR", "ELINT"], weapons=["舰载反舰导弹"], endurance_hr=300, autonomy_tier=2, health={"fuel_pct": 86, "comms_strength": 95}),
        AssetSnapshot("AEW-01", "air", "侦察无人机", "active", 22.42, 121.52, alt_ft=18000, heading=204, speed_kts=110, sensors=["AEW_RADAR", "EO/IR", "ESM"], endurance_hr=24, autonomy_tier=4, health={"battery_pct": 84, "comms_strength": 94}),
        AssetSnapshot("UAV-CONFIRM-01", "air", "补充侦察无人机", "active", 22.14, 121.22, alt_ft=8000, heading=70, speed_kts=92, sensors=["EO/IR", "SAR"], endurance_hr=18, autonomy_tier=4, health={"battery_pct": 88, "comms_strength": 86}),
        AssetSnapshot("SHORE-RADAR-01", "ground", "岸基警戒雷达", "active", 22.42, 120.92, heading=100, sensors=["AESA_RADAR", "AIS", "COMINT"], endurance_hr=9999, autonomy_tier=1, health={"battery_pct": 100, "comms_strength": 97}),
    ]
    threats = [
        ThreatSnapshot("CONTACT-HOSTILE-01", "敌方高速攻击艇", "maritime", 22.30, 121.62, heading=245, speed_kts=31, risk_level="UNKNOWN", rcs_dbsm=7, ir_signature="medium", iff_status="unknown", ais_match=False, rf_freq_mhz=5600, power_dbm=-58, behavior_script={"phases": [{"phase": 1, "name": "向编队方向高速接近", "duration_sec": 1200, "heading": 245, "speed_kts": 31, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "向南实施规避机动", "duration_sec": 1200, "heading": 180, "speed_kts": 25, "risk_level": "UNKNOWN"}, {"phase": 3, "name": "向东侧外海转向", "duration_sec": 1200, "heading": 80, "speed_kts": 22, "risk_level": "UNKNOWN"}, {"phase": 4, "name": "沿责任区北侧机动", "duration_sec": 2400, "heading": 20, "speed_kts": 18, "risk_level": "UNKNOWN"}]}),
        ThreatSnapshot("CONTACT-FISHING-01", "民用渔船", "maritime", 22.10, 121.40, heading=145, speed_kts=8, risk_level="UNKNOWN", rcs_dbsm=12, ir_signature="low", iff_status="unknown", ais_match=True, behavior_script={"phases": [{"phase": 1, "name": "沿渔区低速航行", "duration_sec": 1500, "heading": 145, "speed_kts": 8, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "减速进行捕捞作业", "duration_sec": 1500, "heading": 170, "speed_kts": 4, "risk_level": "UNKNOWN"}, {"phase": 3, "name": "恢复沿渔区航行", "duration_sec": 1500, "heading": 80, "speed_kts": 7, "risk_level": "UNKNOWN"}, {"phase": 4, "name": "驶离护航航线", "duration_sec": 1500, "heading": 35, "speed_kts": 9, "risk_level": "UNKNOWN"}]}),
    ]
    routes = {
        "MERCHANT-01": [{"lat": 22.15, "lng": 121.38}, {"lat": 22.22, "lng": 121.50}, {"lat": 22.30, "lng": 121.62}],
        "MERCHANT-02": [{"lat": 22.11, "lng": 121.34}, {"lat": 22.18, "lng": 121.46}, {"lat": 22.26, "lng": 121.58}],
        "ESCORT-01": [{"lat": 22.19, "lng": 121.32}, {"lat": 22.27, "lng": 121.44}, {"lat": 22.35, "lng": 121.56}],
        "AEW-01": [
            {"lat": 22.32, "lng": 121.47, "label": "ORBIT-SE"},
            {"lat": 22.26, "lng": 121.32, "label": "ORBIT-S"},
            {"lat": 22.31, "lng": 121.15, "label": "ORBIT-SW"},
            {"lat": 22.43, "lng": 121.08, "label": "ORBIT-W"},
            {"lat": 22.55, "lng": 121.15, "label": "ORBIT-NW"},
            {"lat": 22.61, "lng": 121.32, "label": "ORBIT-N"},
            {"lat": 22.55, "lng": 121.47, "label": "ORBIT-NE"},
            {"lat": 22.42, "lng": 121.52, "label": "ORBIT-E"},
        ],
        "UAV-CONFIRM-01": [{"lat": 22.16, "lng": 121.30}, {"lat": 22.22, "lng": 121.48}, {"lat": 22.28, "lng": 121.62}],
        "SHORE-RADAR-01": [],
    }
    route_modes = {
        "MERCHANT-01": "hold", "MERCHANT-02": "hold", "ESCORT-01": "hold",
        "AEW-01": "loop", "UAV-CONFIRM-01": "hold", "SHORE-RADAR-01": "hold",
    }
    motion_windows = {"UAV-CONFIRM-01": {"start_sec": 2160, "activate_on_follow": True}}
    extra_devices = [{
        "device_id": "ASCM-01",
        "name": "舰载反舰导弹一号",
        "device_type": "anti_ship_missile",
        "status": "stowed",
        "asset_ref": "ESCORT-01",
    }]
    compute_nodes = [
        compute_node("ESCORT-01-COMPUTE", "护航舰舰载任务计算节点", "ESCORT-01",
                     host_device_type="escort_ship", compute_type="ship_edge",
                     cpu="32 cores", accelerator="tactical_gpu", memory_gb=128, network="shipboard_fabric"),
        compute_node("UAV-CONFIRM-01-COMPUTE", "补充侦察无人机边缘计算模块", "UAV-CONFIRM-01",
                     host_device_type="uav", compute_type="uav_edge",
                     cpu="8 cores", accelerator="embedded_ai", memory_gb=32, network="line_of_sight_datalink"),
        compute_node("ASCM-01-COMPUTE", "反舰导弹弹载制导计算单元", "ASCM-01",
                     host_device_type="anti_ship_missile", compute_type="onboard_guidance",
                     status="standby", cpu="4 cores", accelerator="signal_processor", memory_gb=8, network="weapon_datalink"),
        compute_node("AEW-01-COMPUTE", "侦察无人机任务处理节点", "AEW-01",
                     host_device_type="uav", compute_type="uav_edge",
                     cpu="24 cores", accelerator="radar_dsp", memory_gb=96, network="tactical_air_link"),
        compute_node("SHORE-RADAR-01-COMPUTE", "岸基融合处理节点", "SHORE-RADAR-01",
                     host_device_type="ground_station", compute_type="ground_edge",
                     cpu="48 cores", accelerator="server_gpu", memory_gb=192, network="fiber_backhaul"),
    ]
    agent_deployments = [
        agent_deployment("A1", "AEW-01-COMPUTE", roles=["early_warning_detection", "multi_source_perception"], runtime_status="planned"),
        agent_deployment("A1", "SHORE-RADAR-01-COMPUTE", roles=["ais_radar_correlation", "intelligence_ingest"], runtime_status="planned"),
        agent_deployment("A2", "ESCORT-01-COMPUTE", roles=["track_update", "threat_assessment"], runtime_status="planned"),
        agent_deployment("A3", "ESCORT-01-COMPUTE", roles=["resource_allocation", "uav_tasking"], runtime_status="planned"),
        agent_deployment("A4", "ESCORT-01-COMPUTE", roles=["attack_option_selection", "decision_planning"], runtime_status="planned"),
        agent_deployment("A5", "ESCORT-01-COMPUTE", roles=["roe_review", "civilian_no_strike_check"], runtime_status="planned"),
        agent_deployment("A6", "ESCORT-01-COMPUTE", roles=["fire_command", "closed_loop_control"], runtime_status="planned"),
        agent_deployment("A1", "UAV-CONFIRM-01-COMPUTE", roles=["supplemental_reconnaissance", "target_follow"], runtime_status="standby"),
        agent_deployment("A2", "UAV-CONFIRM-01-COMPUTE", roles=["post_strike_tracking", "damage_observation"], runtime_status="standby"),
        agent_deployment("A6", "ASCM-01-COMPUTE", roles=["terminal_guidance", "weapon_flight_monitor"], runtime_status="standby"),
    ]
    asset_capabilities, asset_task_schedule, capture_plans = _capture_contract()
    timeline = _timeline()
    media_cues = bind_media_consumers(_media_cues(), timeline)
    return {
        "schema_version": "amos.scenario.v2", "id": SCENARIO_ID,
        "name": "海上编队护航与要地防空",
        "operator_brief": "编队进入台湾东南外海虚构护航责任区后发现两个待识别海面目标。系统需将敌方资源与渔船可靠区分，把渔船列入禁射约束，并在收到明确攻击命令后由护航舰执行模拟武器攻击和毁伤评估。",
        "description": "验证两个未知海面目标从发现、定位、跟踪、识别、目标选择、授权攻击到毁伤评估的完整 F2T2EA 杀伤链，并以渔船目标检验交战规则和附带风险控制。",
        "scenario_type": "scripted_agent_demo",
        "theater": {"theater_id": "taiwan_southeast_convoy_corridor", "name": "台湾东南外海护航仿真区", "location_profile": "fictional_training_area", "center": {"lat": 22.28, "lng": 121.32}, "zoom": 10, "ao": {"north": 22.75, "south": 21.85, "east": 121.78, "west": 120.84}},
        "map_display": {"default_layers": {"sensors": False, "ao": True}, "track_style": "tactical_local", "base_surface": "coastal"},
        "environment": {"weather": "partly_cloudy", "visibility_nm": 20, "wind_speed_kts": 16, "wind_direction_deg": 85, "sea_state": 3, "precipitation": "none", "intermittent_jamming": True, "shipping_channel": "SIM-TAIWAN-SE-A", "restricted_area": "SIM-NO-GO-01"},
        "asset_routes": routes, "asset_route_modes": route_modes,
        "asset_motion_windows": motion_windows,
        "asset_follow_tasks": [{
            "task_id": "MAR-FOLLOW-HIGH-THREAT",
            "asset_id": "UAV-CONFIRM-01",
            "launch_from_asset": "ESCORT-01",
            "start_sec": 0,
            "hide_until_follow": True,
            "target_selector": "confirmed_highest_threat",
            "required_assessment_status": ["confirmed"],
            "required_threat_levels": ["HIGH", "CRITICAL"],
            "excluded_classifications": ["FISHING_VESSEL", "FISHING BOAT", "FISHING", "CIVILIAN", "MERCHANT"],
            "requires_operator_authorization": True,
            "standoff_nm": 1.8,
            "station_bearing_slew_dps": 0.35,
            "fallback_target_speed_kts": 31,
            "closure_gain_kts_per_nm": 12,
            "return_to_launch_after_strike": True,
            "return_after_sec": 5610,
            "return_hide_distance_nm": 0.2,
        }],
        "asset_profiles": asset_profiles(assets),
        "physical_devices": physical_devices(assets, extra_devices),
        "compute_nodes": compute_nodes,
        "agent_deployments": agent_deployments,
        "asset_capabilities": asset_capabilities,
        "asset_task_schedule": asset_task_schedule,
        "capture_plans": capture_plans,
        "threat_observation_windows": {
            "CONTACT-HOSTILE-01": {"start_sec": 660},
            "CONTACT-FISHING-01": {"start_sec": 660},
        },
        "assets": assets, "threats": threats,
        "protected_assets": [{"asset_id": "CONVOY-GROUP-01", "asset_name": "海上运输编队", "asset_type": "convoy", "lat": 22.08, "lon": 121.26, "alt": 0, "protection_radius_m": 18000, "criticality": 0.92, "status": "protected", "metadata": {"simulation_only": True, "location_profile": "fictional_training_area"}}],
        "timeline": timeline, "media_cues": media_cues, "cover_media_id": "MAR-MEDIA-00",
        "demo_controls": {"recommended_speed": 32, "duration_sec": 6000, "auto_agent_interval_sec": 600, "show_truth": False, "latest_visual_only": True, "auto_stop": True, "advance_while_analyzing": True},
        "default_seed": 33031, "supported_modes": ["integration", "demonstration"],
        "functional_agents": scenario_agents(), "required_agents": required_backend_roles(),
        "algorithm_coverage": planned_algorithms("clustering", "association", "linear_regression", "logistic_regression", "random_forest", "neural_network", "naive_bayes_network", "large_language_model", "retrieval_augmented_generation", "agent_collaboration", "federated_learning", "reinforcement_learning", "explainable_ai", "multimodal_fusion", "time_series_prediction", "real_time_object_detection", "change_detection", "multi_target_tracking", "graph_neural_network"),
        "function_point_coverage": planned_function_points(*[f"FP-{i:02d}" for i in range(1, 30)]),
        "demo_checkpoints": [
            {"checkpoint_id": "MAR-CP-PERCEPTION", "title": "海空观测融合输入就绪", "min_elapsed_sec": 1470, "conditions": {"stable_track_count_at_least": 2, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["MAR-MEDIA-01", "MAR-MEDIA-02"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "MAR-CP-ASSESS", "title": "敌方与渔船识别输入就绪", "min_elapsed_sec": 2910, "conditions": {"stable_track_count_at_least": 2, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["MAR-MEDIA-03", "MAR-MEDIA-04"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "MAR-CP-PLAN", "title": "攻击方案与禁射约束输入就绪", "min_elapsed_sec": 3630, "conditions": {"media_ids_released": ["MAR-MEDIA-05"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "MAR-CP-CLOSE", "title": "毁伤评估与渔船安全复核就绪", "min_elapsed_sec": 5610, "conditions": {"media_ids_released": ["MAR-MEDIA-07"]}, "pause": True, "submit_analysis": True},
        ],
        "fault_injections": [
            {"fault_id": "MAR-FAULT-JAM", "type": "communication_degradation", "target": "ESCORT-01", "at_checkpoint": "MAR-CP-ASSESS", "status": "available"},
            {"fault_id": "MAR-FAULT-RESOURCE", "type": "resource_unavailable", "target": "AEW-01", "at_checkpoint": "MAR-CP-PLAN", "status": "available"},
        ],
        "expected_branches": [
            {"branch_id": "standard", "name": "标准杀伤链", "description": "完成敌方识别、渔船排除、授权攻击和毁伤评估。"},
            {"branch_id": "communication_degraded", "name": "通信降级", "description": "注入链路质量下降，检验后端是否重评估。"},
            {"branch_id": "resource_unavailable", "name": "资源不可用", "description": "请求后端处理侦察无人机资源不可用条件。"},
            {"branch_id": "behavior_changed", "name": "目标行为变化", "description": "以当前新航迹状态触发重新评估和规划。"},
            {"branch_id": "civilian_misidentification", "name": "民用目标误识别", "description": "检验渔船身份置信不足时是否禁止攻击并请求补充侦察。"},
        ],
        "default_branch": "standard",
        "acceptance_profile": {"functional_agents": 6, "core_algorithms": 15, "engineering_models": 4, "function_points": 29, "evidence_policy": "backend_trace_only", "required_target_count": 2, "requires_civilian_no_strike": True, "requires_explicit_fire_authorization": True},
        "engagement_policy": {"decision_authority": "operator", "requires_backend_identification": True, "requires_explicit_authorization": True, "requires_prior_warning": True, "warning_delay_sec": 300, "minimum_threat_levels": ["HIGH", "CRITICAL"], "eligible_kill_chain_phases": ["TARGET", "ENGAGE"], "authorized_asset_ids": ["ESCORT-01"], "authorized_weapons": ["舰载反舰导弹"], "protected_classifications": ["FISHING_VESSEL", "FISHING BOAT", "FISHING", "CIVILIAN", "MERCHANT"], "protected_truth_ids": ["CONTACT-FISHING-01"]},
        "agent_plan": {"mode": "commander_workflow", "steps": ["submit_current_snapshot", "execute_a1_a6_workflow", "project_run_scoped_evidence", "request_operator_fire_authorization", "execute_authorized_fire_command", "assess_effects"]},
        "events": [row["title"] for row in _timeline()],
    }


__all__ = ["SCENARIO_ID", "build_maritime_convoy_air_defense_scenario"]
