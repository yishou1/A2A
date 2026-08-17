"""Formal scenario: amphibious landing joint operation and assessment loop."""

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


SCENARIO_ID = "amphibious-landing-joint-operation"
MEDIA_ROOT = f"/static/assets/scenarios/{SCENARIO_ID}"
CHECKSUMS = {
    "00-satellite-coast-sar.png": "7e347ff9ec28026c31c22912687b8bc1b2c0705d9b8b9b586cadedb79ebcf12f",
    "01-uav-beach-eo.png": "ad31daa185f905a7658ab3b0ce4f76b079feac73be358c3dd21a64331a1c965d",
    "02-ship-radar-picture.svg": "4ed454af57f9fd8a0832c119c7c44f658b5a8c2abb68473d9b85a6cd9aaf15f4",
    "03-elint-emitter.svg": "f4528bbd128219261f277aad70b072a458921ca5d6c3e4e678e0a4ab964b0d36",
    "04-fused-current-targets.svg": "158ccc04411d53043624654a0efafd2de78d0b068dd0484bc711e70c64f4e91c",
    "05-resource-status.svg": "1b22a6b9651f536183af1f1fb7cb3ba1ed23cc993bf6b5e670761020d389f396",
    "06-approved-plan-current.svg": "e7291468c52649d35c4e1d215edc5b9794820e65ea1176d551e268a969eb12cd",
    "07-post-action-eo.png": "ff13f629402104c33f7e37eb4eba40a6943892c25635b9f0d2375d3bc911fc1b",
    "08-post-action-ir.png": "f08209a20c7a75432602ee7eaa73610dde5e6a6d654c96c7d9e65cd3c80cee46",
    "09-second-pass-eo.png": "ba361f3a7728db1a27b4e8bcce88476f085719457cb751050ef2bce71e378da0",
}

PRODUCT_TYPES = {
    "AMP-MEDIA-00": "external_precollected",
    "AMP-MEDIA-01": "raw_sensor_frame",
    "AMP-MEDIA-02": "derived_sensor_product",
    "AMP-MEDIA-03": "derived_sensor_product",
    "AMP-MEDIA-04": "derived_sensor_product",
    "AMP-MEDIA-05": "derived_sensor_product",
    "AMP-MEDIA-06": "command_product",
    "AMP-MEDIA-07": "raw_sensor_frame",
    "AMP-MEDIA-08": "raw_sensor_frame",
    "AMP-MEDIA-09": "raw_sensor_frame",
}


def _media_cues() -> list[dict[str, Any]]:
    rows = (
        ("AMP-MEDIA-00", "00-satellite-coast-sar.png", 0, "FIND", "岸滩区域卫星遥感", "卫星遥感记录岸线、工事疑似区域和近海交通，尚未形成目标结论。", "SAT-SAR-01/SAR", "sar", "image/png"),
        ("AMP-MEDIA-01", "01-uav-beach-eo.png", 450, "FIND", "无人机岸滩光电观测", "无人机形成台湾西南低平岸段、道路与设施的当前画面，等待多源检测与验证。", "UAV-ISR-01/EO", "eo_ir", "image/png"),
        ("AMP-MEDIA-02", "02-ship-radar-picture.svg", 780, "FIX", "驱逐舰雷达批次", "舰载雷达在 24 NM 量程内形成近海高速接触和沿岸杂波批次，尚未完成航迹关联。", "DDG-01/AESA-RADAR", "radar", "image/svg+xml"),
        ("AMP-MEDIA-03", "03-elint-emitter.svg", 1110, "TRACK", "电子侦察活动记录", "电子侦察在当前 90 秒频谱瀑布图中记录间歇辐射，来源和性质由后端关联。", "UAV-ISR-01/ELINT", "telemetry", "image/svg+xml"),
        ("AMP-MEDIA-04", "04-fused-current-targets.svg", 1440, "TRACK", "当前多域航迹集合", "AMOS 根据 T+24 分钟前已到达的海面、岸滩和电磁观测生成航迹输入，不包含后端结论。", "AMOS/FUSION", "telemetry", "image/svg+xml"),
        ("AMP-MEDIA-05", "05-resource-status.svg", 1800, "TARGET", "联合资源状态快照", "AMOS 冻结登陆艇、舰艇、巡飞平台、直升机和侦察平台的当前状态，供后端调度使用。", "AMOS/RESOURCE-STATE", "telemetry", "image/svg+xml"),
        ("AMP-MEDIA-06", "06-approved-plan-current.svg", 2220, "ENGAGE", "模拟任务执行状态", "仅记录已进入模拟执行的活动和当前平台反馈；后端未返回的结论保持未提供。", "COMMANDER/EXECUTION", "telemetry", "image/svg+xml"),
        ("AMP-MEDIA-07", "07-post-action-eo.png", 2640, "ASSESS", "行动后光电复查", "当前光电复查画面到达，前端不自行判定设施状态。", "UAV-BDA-01/EO", "eo_ir", "image/png"),
        ("AMP-MEDIA-08", "08-post-action-ir.png", 3000, "ASSESS", "行动后红外复查", "当前红外资料记录局部热特征变化，与基线资料共同构成变化检测输入。", "UAV-BDA-01/IR", "ir", "image/png"),
        ("AMP-MEDIA-09", "09-second-pass-eo.png", 3420, "ASSESS", "二次光电复查", "二次复查形成新的当前证据，是否闭环由后端全局任务评估结果确认。", "UAV-BDA-02/EO", "eo_ir", "image/png"),
    )
    return [media_record(MEDIA_ROOT, mid, fn, at_sec=t, phase=p, title=title, caption=caption,
                         sensor_id=sensor, modality=modality, mime_type=mime, checksum=CHECKSUMS[fn],
                         capture_id=f"AMP-CAPTURE-{mid.rsplit('-', 1)[-1]}",
                         product_type=PRODUCT_TYPES[mid])
            for mid, fn, t, p, title, caption, sensor, modality, mime in rows]


def _capture_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = [
        sensor_capability("AMP-CAP-EXT-SAR", "SAT-SAR-01", "SAR", sensor_type="satellite_sar",
                          modalities=["sar"], source_kind="external_source",
                          parameters={"ground_sample_distance_m": 3, "swath_width_km": 40}),
        sensor_capability("AMP-CAP-ISR-EO", "UAV-ISR-01", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
        sensor_capability("AMP-CAP-DDG-RADAR", "DDG-01", "AESA-RADAR", sensor_type="surface_search_radar",
                          modalities=["radar"], configured_sensor="AESA_RADAR",
                          parameters={"instrumented_range_nm": 24, "azimuth_coverage_deg": 360, "range_resolution_m": 60}),
        sensor_capability("AMP-CAP-ISR-ELINT", "UAV-ISR-01", "ELINT", sensor_type="elint",
                          modalities=["telemetry"], configured_sensor="ELINT",
                          parameters={"frequency_band_mhz": [500, 6000], "bearing_error_deg": 3, "observation_window_sec": 90}),
        sensor_capability("AMP-CAP-FUSION", "AMOS", "FUSION", sensor_type="fusion_processor",
                          modalities=["telemetry"], source_kind="simulation_processor",
                          parameters={"current_time_only": True, "source_track_limit": 32}),
        sensor_capability("AMP-CAP-RESOURCE", "AMOS", "RESOURCE-STATE", sensor_type="resource_monitor",
                          modalities=["telemetry"], source_kind="simulation_processor",
                          parameters={"refresh_interval_sec": 30, "resource_types": ["platform", "payload", "link"]}),
        sensor_capability("AMP-CAP-EXECUTION", "COMMANDER", "EXECUTION", sensor_type="command_status",
                          modalities=["telemetry"], source_kind="command_system",
                          parameters={"current_time_only": True, "simulation_execution_only": True}),
        sensor_capability("AMP-CAP-BDA1-EO", "UAV-BDA-01", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
        sensor_capability("AMP-CAP-BDA1-IR", "UAV-BDA-01", "IR", sensor_type="infrared",
                          modalities=["ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 10, "horizontal_fov_deg": 28, "spectral_band_um": [8, 12]}),
        sensor_capability("AMP-CAP-BDA2-EO", "UAV-BDA-02", "EO", sensor_type="electro_optical",
                          modalities=["eo_ir"], configured_sensor="EO/IR",
                          parameters={"effective_range_nm": 12, "horizontal_fov_deg": 35, "resolution_px": [1920, 1080]}),
    ]
    by_id = {row["capability_id"]: row for row in capabilities}
    specifications = (
        ("AMP-TASK-00", "AMP-CAP-EXT-SAR", "ingest", 0, 1, ["CONTACT-FORTIFICATION-01"], ["*"]),
        ("AMP-TASK-01", "AMP-CAP-ISR-EO", "capture", 390, 510, ["CONTACT-FORTIFICATION-01"], ["*"]),
        ("AMP-TASK-02", "AMP-CAP-DDG-RADAR", "derive", 720, 840, ["CONTACT-SURFACE-FAST"], ["*"]),
        ("AMP-TASK-03", "AMP-CAP-ISR-ELINT", "derive", 1020, 1140, ["CONTACT-EMITTER-01"], ["*"]),
        ("AMP-TASK-04", "AMP-CAP-FUSION", "derive", 1380, 1470, ["CONTACT-SURFACE-FAST", "CONTACT-FORTIFICATION-01", "CONTACT-EMITTER-01"], ["*"]),
        ("AMP-TASK-05", "AMP-CAP-RESOURCE", "derive", 1740, 1830, [], ["*"]),
        ("AMP-TASK-06", "AMP-CAP-EXECUTION", "command_product", 2160, 2250, ["CONTACT-SURFACE-FAST", "CONTACT-FORTIFICATION-01"], ["*"]),
        ("AMP-TASK-07", "AMP-CAP-BDA1-EO", "capture", 2580, 2670, ["CONTACT-FORTIFICATION-01"], ["*"]),
        ("AMP-TASK-08", "AMP-CAP-BDA1-IR", "capture", 2940, 3030, ["CONTACT-FORTIFICATION-01"], ["*"]),
        ("AMP-TASK-09", "AMP-CAP-BDA2-EO", "capture", 3360, 3450, ["CONTACT-FORTIFICATION-01"], ["*"]),
    )
    tasks = [
        sensor_task(task_id, by_id[capability_id], task_type=task_type, start_sec=start, end_sec=end,
                    target_refs=targets, branch_ids=branches)
        for task_id, capability_id, task_type, start, end, targets, branches in specifications
    ]
    task_by_id = {row["task_id"]: row for row in tasks}
    media = _media_cues()
    media_by_id = {row["media_id"]: row for row in media}
    capture_parameters = {
        "AMP-MEDIA-00": {"ground_sample_distance_m": 3, "swath_width_km": 40, "precollected": True, "resolution_px": [1672, 941]},
        "AMP-MEDIA-01": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 17, "point_at_target": True, "registration_group": "AMP-COASTAL-SITE-A", "frame_role": "baseline_eo", "resolution_px": [1672, 941]},
        "AMP-MEDIA-02": {"instrumented_range_nm": 24, "azimuth_coverage_deg": 360, "range_resolution_m": 60, "observation_window_sec": 60, "data_source": "sensor_observations", "renderer_type": "radar_ppi"},
        "AMP-MEDIA-03": {"frequency_band_mhz": [500, 6000], "observation_window_sec": 90, "bearing_error_deg": 3, "data_source": "sensor_observations", "renderer_type": "elint_spectrum"},
        "AMP-MEDIA-04": {"input_cutoff_sec": 1440, "future_data_included": False, "maximum_tracks": 32, "data_source": "track_fusion", "renderer_type": "track_table"},
        "AMP-MEDIA-05": {"input_cutoff_sec": 1800, "refresh_interval_sec": 30, "data_source": "asset_state", "renderer_type": "resource_status"},
        "AMP-MEDIA-06": {"input_cutoff_sec": 2220, "simulation_execution_only": True, "data_source": "task_state", "renderer_type": "execution_state"},
        "AMP-MEDIA-07": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 15, "point_at_target": True, "registration_group": "AMP-COASTAL-SITE-A", "reference_media_id": "AMP-MEDIA-01", "frame_role": "post_action_eo", "resolution_px": [1672, 941]},
        "AMP-MEDIA-08": {"effective_range_nm": 10, "horizontal_fov_deg": 28, "look_angle_deg": 9, "spectral_band_um": [8, 12], "point_at_target": True, "registration_group": "AMP-COASTAL-SITE-A", "reference_media_id": "AMP-MEDIA-01", "frame_role": "post_action_ir", "resolution_px": [1672, 941]},
        "AMP-MEDIA-09": {"effective_range_nm": 12, "horizontal_fov_deg": 35, "look_angle_deg": 12, "point_at_target": True, "registration_group": "AMP-COASTAL-SITE-A", "reference_media_id": "AMP-MEDIA-01", "frame_role": "second_pass_eo", "resolution_px": [1672, 941]},
    }
    targets = {task_id.replace("AMP-TASK", "AMP-MEDIA"): refs for task_id, _, _, _, _, refs, _ in specifications}
    captures = []
    for index, task in enumerate(tasks):
        media_id = f"AMP-MEDIA-{index:02d}"
        item = media_by_id[media_id]
        capability = by_id[task["capability_id"]]
        captures.append(capture_plan(
            item["capture_id"], media_id, capability, task, product_type=item["product_type"],
            at_sec=item["at_sec"], target_refs=targets[media_id],
            parameters=capture_parameters[media_id], branch_ids=item["branch_ids"],
        ))
    return capabilities, tasks, captures


def _timeline() -> list[dict[str, Any]]:
    return [
        {"cue_id": "AMP-CUE-01", "at_sec": 0, "phase": "FIND", "level": "INFO", "title": "联合侦察开始", "description": "卫星、无人机、舰载雷达和电子侦察按计划形成当前观测。", "media_ids": ["AMP-MEDIA-00"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M17"]},
        {"cue_id": "AMP-CUE-02", "at_sec": 450, "phase": "FIND", "level": "INFO", "title": "岸滩光电资料到达", "description": "光电资料可供检测、特征提取和多源融合。", "media_ids": ["AMP-MEDIA-01"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M01", "M06", "M15", "M17"]},
        {"cue_id": "AMP-CUE-03", "at_sec": 780, "phase": "FIX", "level": "INFO", "title": "近海雷达批次到达", "description": "24 NM 量程内的海面接触形成雷达批次，保持待识别状态。", "media_ids": ["AMP-MEDIA-02"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M19", "M20"]},
        {"cue_id": "AMP-CUE-04", "at_sec": 1110, "phase": "TRACK", "level": "WARNING", "title": "间歇电磁活动出现", "description": "频谱与测向资料可供后端进行关联和意图概率修正。", "media_ids": ["AMP-MEDIA-03"], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M05", "M07", "M20"]},
        {"cue_id": "AMP-CUE-05", "at_sec": 1440, "phase": "TRACK", "level": "INFO", "title": "多域航迹达到评估条件", "description": "海面、岸滩和辐射源记录形成当前多域输入。", "media_ids": ["AMP-MEDIA-04"], "functional_agent_ids": ["A2"], "model_requirement_ids": ["M05", "M16", "M19"]},
        {"cue_id": "AMP-CUE-06", "at_sec": 1800, "phase": "TARGET", "level": "INFO", "title": "联合资源进入调度输入", "description": "当前距离、时间窗、资源和任务依赖可供 A3 分配；后端缺失时保持未提供。", "media_ids": ["AMP-MEDIA-05"], "functional_agent_ids": ["A3", "A4", "A5"], "model_requirement_ids": ["M02", "M03", "M04", "M09", "M10", "M11", "M13", "M14"]},
        {"cue_id": "AMP-CUE-07", "at_sec": 2220, "phase": "ENGAGE", "level": "INFO", "title": "联合任务进入模拟执行阶段", "description": "AMOS 推进已进入执行阶段的模拟资源并反馈当前平台状态；后端结论仍以工作流证据为准。", "media_ids": ["AMP-MEDIA-06"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M03", "M16"]},
        {"cue_id": "AMP-CUE-08", "at_sec": 2640, "phase": "ASSESS", "level": "INFO", "title": "首轮光电复查资料到达", "description": "基线与当前光电资料形成比较输入，效果仍等待后端判定。", "media_ids": ["AMP-MEDIA-07"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M04", "M14", "M18"]},
        {"cue_id": "AMP-CUE-09", "at_sec": 3000, "phase": "ASSESS", "level": "WARNING", "title": "红外复查资料到达", "description": "热特征变化可供闭环评估；是否需要补充任务由后端输出。", "media_ids": ["AMP-MEDIA-08"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M03", "M18"]},
        {"cue_id": "AMP-CUE-10", "at_sec": 3420, "phase": "ASSESS", "level": "INFO", "title": "二次复查资料到达", "description": "补充观测的当前资料到达，可进行全局任务完成度评估。", "media_ids": ["AMP-MEDIA-09"], "functional_agent_ids": ["A3", "A4", "A5", "A6"], "model_requirement_ids": ["M04", "M14", "M16"]},
    ]


def build_amphibious_landing_scenario() -> dict[str, Any]:
    assets = [
        AssetSnapshot("C2-JOINT-01", "ground", "联合行动指挥节点", "active", 22.67, 120.42, sensors=["MISSION-SERVER", "COMINT"], endurance_hr=9999, autonomy_tier=1, health={"battery_pct": 100, "comms_strength": 97}),
        AssetSnapshot("DDG-01", "maritime", "防空驱逐舰", "active", 22.42, 120.05, heading=55, speed_kts=16, sensors=["AESA_RADAR", "ELINT", "EO/IR"], weapons=["舰炮", "防空单元"], endurance_hr=320, autonomy_tier=2, health={"fuel_pct": 88, "comms_strength": 96}),
        AssetSnapshot("LANDING-01", "maritime", "登陆艇编队", "active", 22.37, 120.15, heading=60, speed_kts=18, sensors=["NAV-RADAR", "EO"], endurance_hr=30, autonomy_tier=1, health={"fuel_pct": 91, "comms_strength": 88}),
        AssetSnapshot("UAV-ISR-01", "air", "多载荷侦察无人机", "active", 22.62, 120.22, alt_ft=12000, heading=120, speed_kts=96, sensors=["EO/IR", "SAR", "ELINT"], endurance_hr=20, autonomy_tier=4, health={"battery_pct": 90, "comms_strength": 91}),
        AssetSnapshot("UAV-BDA-01", "air", "效果评估无人机", "active", 22.52, 120.30, alt_ft=9000, heading=285, speed_kts=82, sensors=["EO/IR", "SAR"], endurance_hr=16, autonomy_tier=4, health={"battery_pct": 93, "comms_strength": 89}),
        AssetSnapshot("UAV-BDA-02", "air", "二次复查无人机", "active", 22.48, 120.25, alt_ft=8500, heading=45, speed_kts=84, sensors=["EO/IR"], endurance_hr=15, autonomy_tier=4, health={"battery_pct": 95, "comms_strength": 90}),
        AssetSnapshot("LOITER-01", "air", "巡飞弹待命单元", "active", 22.45, 120.18, alt_ft=6500, heading=45, speed_kts=65, sensors=["EO"], weapons=["模拟任务载荷"], endurance_hr=3, autonomy_tier=3, health={"battery_pct": 100, "comms_strength": 91}),
        AssetSnapshot("HELO-01", "air", "武装直升机", "active", 22.35, 120.04, alt_ft=1200, heading=55, speed_kts=110, sensors=["EO/IR", "RADAR"], weapons=["模拟任务载荷"], endurance_hr=4, autonomy_tier=2, health={"fuel_pct": 87, "comms_strength": 92}),
        AssetSnapshot("ARTY-01", "ground", "远程火力支援单元", "active", 22.72, 120.48, sensors=["FIRE-CONTROL"], weapons=["模拟远程火力"], endurance_hr=48, autonomy_tier=1, health={"fuel_pct": 90, "comms_strength": 88}),
    ]
    threats = [
        ThreatSnapshot("CONTACT-SURFACE-FAST", "待识别近海高速目标", "maritime", 22.55, 120.30, heading=235, speed_kts=34, risk_level="UNKNOWN", rcs_dbsm=6, ir_signature="medium", iff_status="unknown", ais_match=False, behavior_script={"phases": [{"phase": 1, "name": "沿岸向西南机动", "duration_sec": 900, "heading": 235, "speed_kts": 34, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "向外海转向", "duration_sec": 900, "heading": 285, "speed_kts": 28, "risk_level": "UNKNOWN"}, {"phase": 3, "name": "向北调整航向", "duration_sec": 900, "heading": 20, "speed_kts": 24, "risk_level": "UNKNOWN"}, {"phase": 4, "name": "沿近岸水域向东机动", "duration_sec": 900, "heading": 70, "speed_kts": 20, "risk_level": "UNKNOWN"}]}),
        ThreatSnapshot("CONTACT-FORTIFICATION-01", "待验证岸滩固定设施", "ground", 22.62, 120.49, heading=0, speed_kts=0, risk_level="UNKNOWN", rcs_dbsm=24, ir_signature="low", iff_status="unknown", ais_match=False),
        ThreatSnapshot("CONTACT-EMITTER-01", "待关联岸基辐射源", "ground", 22.72, 120.44, heading=0, speed_kts=0, risk_level="UNKNOWN", rf_freq_mhz=3200, power_dbm=-18, rcs_dbsm=15, ir_signature="medium", iff_status="unknown", ais_match=False),
    ]
    routes = {
        "C2-JOINT-01": [], "ARTY-01": [],
        "DDG-01": [{"lat": 22.48, "lng": 120.12}, {"lat": 22.53, "lng": 120.18}, {"lat": 22.58, "lng": 120.20}],
        "LANDING-01": [{"lat": 22.42, "lng": 120.22}, {"lat": 22.49, "lng": 120.30}, {"lat": 22.56, "lng": 120.38}],
        "UAV-ISR-01": [{"lat": 22.70, "lng": 120.32}, {"lat": 22.64, "lng": 120.46}, {"lat": 22.50, "lng": 120.40}, {"lat": 22.52, "lng": 120.20}],
        "UAV-BDA-01": [{"lat": 22.58, "lng": 120.40}, {"lat": 22.68, "lng": 120.35}, {"lat": 22.55, "lng": 120.22}],
        "UAV-BDA-02": [{"lat": 22.55, "lng": 120.36}, {"lat": 22.62, "lng": 120.44}, {"lat": 22.68, "lng": 120.42}],
        "LOITER-01": [{"lat": 22.50, "lng": 120.26}, {"lat": 22.57, "lng": 120.36}, {"lat": 22.62, "lng": 120.48}],
        "HELO-01": [{"lat": 22.42, "lng": 120.14}, {"lat": 22.50, "lng": 120.26}, {"lat": 22.58, "lng": 120.38}],
    }
    route_modes = {
        "C2-JOINT-01": "hold", "ARTY-01": "hold", "DDG-01": "hold", "LANDING-01": "hold",
        "UAV-ISR-01": "loop", "UAV-BDA-01": "loop", "UAV-BDA-02": "loop",
        "LOITER-01": "loop", "HELO-01": "hold",
    }
    motion_windows = {
        "LOITER-01": {"start_sec": 1980},
        "HELO-01": {"start_sec": 1980},
        "UAV-BDA-01": {"start_sec": 2340},
        "UAV-BDA-02": {"start_sec": 3060},
    }
    asset_capabilities, asset_task_schedule, capture_plans = _capture_contract()
    timeline = _timeline()
    media_cues = bind_media_consumers(_media_cues(), timeline)
    return {
        "schema_version": "amos.scenario.v2", "id": SCENARIO_ID,
        "name": "抢滩登陆联合行动",
        "operator_brief": "多域侦察、航迹评估、联合资源、授权约束和行动后复查按当前时刻注入；所有目标性质、方案选择和效果结论由 Commander 工作流返回。",
        "description": "覆盖岸滩多域发现、海面和固定目标威胁评估、联合资源分配、候选方案与规则核查、模拟执行、变化检测和 re_attack_required 闭环。",
        "scenario_type": "scripted_agent_demo",
        "theater": {"theater_id": "taiwan_southwest_amphibious_area", "name": "台湾西南近岸联合行动仿真区", "location_profile": "fictional_training_area", "center": {"lat": 22.55, "lng": 120.25}, "zoom": 11, "ao": {"north": 22.78, "south": 22.30, "east": 120.56, "west": 119.92}},
        "map_display": {"default_layers": {"sensors": False, "ao": True}, "track_style": "tactical_local", "base_surface": "coastal"},
        "environment": {"weather": "broken_cloud", "visibility_nm": 14, "wind_speed_kts": 18, "wind_direction_deg": 65, "sea_state": 3, "precipitation": "none", "landing_corridor": "SIM-LANE-01", "civilian_area": "SIM-CIV-03", "authorized_area": "SIM-AUTH-01"},
        "asset_routes": routes, "asset_route_modes": route_modes,
        "asset_motion_windows": motion_windows,
        "asset_profiles": asset_profiles(assets),
        "asset_capabilities": asset_capabilities,
        "asset_task_schedule": asset_task_schedule,
        "capture_plans": capture_plans,
        "threat_observation_windows": {
            "CONTACT-FORTIFICATION-01": {"start_sec": 450},
            "CONTACT-SURFACE-FAST": {"start_sec": 720},
            "CONTACT-EMITTER-01": {"start_sec": 1020},
        },
        "assets": assets, "threats": threats,
        "protected_assets": [{"asset_id": "LANDING-GROUP-01", "asset_name": "登陆输送编队", "asset_type": "landing_group", "lat": 22.40, "lon": 120.17, "alt": 0, "protection_radius_m": 15000, "criticality": 0.98, "status": "protected", "metadata": {"simulation_only": True, "location_profile": "fictional_training_area"}}],
        "timeline": timeline, "media_cues": media_cues, "cover_media_id": "AMP-MEDIA-00",
        "demo_controls": {"recommended_speed": 16, "duration_sec": 3600, "auto_agent_interval_sec": 300, "show_truth": False, "latest_visual_only": True, "auto_stop": True},
        "default_seed": 55051, "supported_modes": ["integration", "demonstration"],
        "functional_agents": scenario_agents(), "required_agents": required_backend_roles(),
        "algorithm_coverage": planned_algorithms("clustering", "association", "linear_regression", "logistic_regression", "random_forest", "neural_network", "naive_bayes_network", "large_language_model", "retrieval_augmented_generation", "agent_collaboration", "reinforcement_learning", "explainable_ai", "multimodal_fusion", "time_series_prediction", "real_time_object_detection", "change_detection", "multi_target_tracking", "graph_neural_network"),
        "function_point_coverage": planned_function_points(*[f"FP-{i:02d}" for i in range(1, 30)]),
        "demo_checkpoints": [
            {"checkpoint_id": "AMP-CP-PERCEPTION", "title": "多域感知输入就绪", "min_elapsed_sec": 810, "conditions": {"stable_track_count_at_least": 2, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["AMP-MEDIA-01", "AMP-MEDIA-02"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "AMP-CP-ASSESS", "title": "威胁评估输入就绪", "min_elapsed_sec": 1470, "conditions": {"stable_track_count_at_least": 3, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["AMP-MEDIA-03", "AMP-MEDIA-04"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "AMP-CP-PLAN", "title": "资源、方案和规则输入就绪", "min_elapsed_sec": 1830, "conditions": {"media_ids_released": ["AMP-MEDIA-05"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "AMP-CP-BDA", "title": "首轮效果评估输入就绪", "min_elapsed_sec": 3030, "conditions": {"media_ids_released": ["AMP-MEDIA-07", "AMP-MEDIA-08"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "AMP-CP-CLOSE", "title": "二次评估输入就绪", "min_elapsed_sec": 3450, "conditions": {"media_ids_released": ["AMP-MEDIA-09"]}, "pause": True, "submit_analysis": True},
        ],
        "fault_injections": [
            {"fault_id": "AMP-FAULT-LOW-SCORE", "type": "low_assessment_score", "target": "A6", "at_checkpoint": "AMP-CP-BDA", "status": "available"},
            {"fault_id": "AMP-FAULT-RESOURCE", "type": "resource_unavailable", "target": "LOITER-01", "at_checkpoint": "AMP-CP-PLAN", "status": "available"},
            {"fault_id": "AMP-FAULT-COMPLIANCE", "type": "compliance_rejected", "target": "A5", "at_checkpoint": "AMP-CP-PLAN", "status": "available"},
        ],
        "expected_branches": [
            {"branch_id": "standard", "name": "标准行动", "description": "按当前资源和授权条件推进。"},
            {"branch_id": "low_score_replan", "name": "中度效果重规划", "description": "后端返回 re_attack_required 后重新触发 A3-A6。"},
            {"branch_id": "resource_unavailable", "name": "资源不可用", "description": "检验 A3 重新分配资源和 A4 重新生成方案。"},
            {"branch_id": "compliance_rejected", "name": "授权不通过", "description": "检验未经 A5 批准的方案不得进入模拟执行。"},
            {"branch_id": "evidence_insufficient", "name": "证据不足", "description": "保持目标状态未知并追加侦察。"},
        ],
        "default_branch": "standard",
        "acceptance_profile": {"functional_agents": 6, "core_algorithms": 14, "engineering_models": 4, "function_points": 29, "evidence_policy": "backend_trace_only", "required_loop_event": "re_attack_required"},
        "agent_plan": {"mode": "commander_workflow", "steps": ["submit_current_snapshot", "execute_a1_a6_workflow", "project_run_scoped_evidence"]},
        "events": [row["title"] for row in _timeline()],
    }


__all__ = ["SCENARIO_ID", "build_amphibious_landing_scenario"]
