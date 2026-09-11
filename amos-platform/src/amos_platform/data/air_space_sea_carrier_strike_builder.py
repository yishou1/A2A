"""Formal scenario: public-capability-inspired carrier unmanned aviation exercise."""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_builder_support import (
    agent_deployment,
    asset_profiles,
    bind_media_consumers,
    capture_plan,
    compute_node,
    media_record,
    physical_devices,
    required_backend_roles,
    scenario_agents,
    sensor_capability,
    sensor_task,
)
from amos_platform.data.scenario_capabilities import planned_algorithms, planned_function_points
from amos_platform.domain.models import AssetSnapshot, ThreatSnapshot


SCENARIO_ID = "air-space-sea-carrier-strike"
MEDIA_ROOT = f"/static/assets/scenarios/{SCENARIO_ID}"
CHECKSUMS = {
    "00-theater-overview.svg": "8a78a9bd99306ff2c8cd2f31320e67860984daa9ae96c2e33c1d40388cd88d29",
    "01-satellite-sar-airfield.png": "312bbe85974fc502858273a786549e2d1fac82ad2da3c94ba2e3629073eb059e",
    "02-uav-eo-airfield.png": "83d7d6fa4e378cfd968299749c4102887515c73999749ab777e64bb0fa90f5f4",
    "03-intelligence-datalink.svg": "85e760de442de4b44a55233193e928a3b9f35b59b9e37eaf02487d11c2a88a6d",
    "04-uav-ir-mobile-targets.png": "152987a90fde6385c0d2781d435666b104cea4350fac9e3bef07c2efc19fd085",
    "05-target-weapon-allocation.svg": "6085f82a9c12ca2807899684c9f9e23811b927f160a4dd6ab668e7adc13ccdc2",
    "06-weapon-chain.svg": "a842413e3894370cb465653c9b4cbcfd1fbc73b497a50d9dbc05de60fc61d788",
    "07-operator-authorization.svg": "b996a32e7bc09033915ec2d5cadaa365ff1ec3731692b8630682e7dc9c9636c2",
    "08-post-strike-sar-bda.png": "3d59a45dbdfdc4488ec32e7f597e45dcbb4e936b55151df8baf70d1c682f639c",
    "09-recovery-summary.svg": "bdcdef2d44306fbac57b4dbe98f97969bac6362f7116fc02d05b192ec5e2450d",
}


TARGET_IDS = (
    "COASTAL-AIRFIELD-01",
    "MOBILE-COASTAL-AD-01",
    "CIVILIAN-PORT-01",
)


def _media_cues() -> list[dict[str, Any]]:
    rows = (
        ("ASC-MEDIA-00", "00-theater-overview.svg", 0, "FIND", "空天海任务区与航母航空作业编组", "虚构训练区初始态势只展示航母特遣群、天基过境方向、舰载航空作业区、机场搜索区和保护区，不预设目标识别结论。", "EXTERNAL-THEATER/THEATER-MAP", "telemetry", "external_precollected"),
        ("ASC-MEDIA-01", "01-satellite-sar-airfield.png", 360, "FIND", "卫星 SAR 发现疑似滨海机场", "一次真实过境形成虚构滨海机场、沿海道路活动和民用港区的宽域 SAR 线索；目标属性由后端依据当前证据判断。", "SAT-A2S-01/ORBITAL-SAR", "radar", "derived_sensor_product"),
        ("ASC-MEDIA-02", "02-uav-eo-airfield.png", 1260, "FIX", "舰载侦察无人机光电复核", "UAV-ISR-01 从海上安全距离回传稳定光电帧，复核机场跑道和与民用港区的安全间隔。", "UAV-ISR-01/EO-IR", "eo_ir", "raw_sensor_frame"),
        ("ASC-MEDIA-03", "03-intelligence-datalink.svg", 1800, "TRACK", "星—舰—预警机情报共享完成", "卫星与侦察无人机数据在 CV-01 完成时标对齐、完整性校验和航迹融合；AEW-01 负责空中战术协同，UAV-TANKER-01 只承担加油保障和链路中继。", "CV-01/C2-FUSION", "telemetry", "command_product"),
        ("ASC-MEDIA-04", "04-uav-ir-mobile-targets.png", 2160, "TRACK", "红外持续跟踪机动岸防单元", "UAV-ISR-01 以白热红外持续观察一辆沿海公路机动的雷达/防空单元，并同步校验其与民用港区的空间间隔。", "UAV-ISR-01/EO-IR", "eo_ir", "raw_sensor_frame"),
        ("ASC-MEDIA-05", "05-target-weapon-allocation.svg", 2700, "TARGET", "按目标类型分配武器", "AEW-01 按 CV-01 的任务级决策协调单架舰载攻击无人机：空地导弹负责机场跑道拒止；其携带的一枚空射巡飞攻击载荷仅预分配给持续跟踪的机动岸防单元；民用港区始终列为禁射保护对象。", "CV-01/TARGET-ALLOCATION", "telemetry", "command_product"),
        ("ASC-MEDIA-06", "06-weapon-chain.svg", 3180, "ENGAGE", "两类武器链就绪", "第一波由舰载攻击无人机释放空地导弹；空射巡飞攻击载荷先在指定海岸外等待，第二波只在第一波毁伤评估后，对仍具威胁且远离民用港区的机动岸防单元执行条件末端攻击。", "CV-01/WEAPON-CHAIN", "telemetry", "command_product"),
        ("ASC-MEDIA-07", "07-operator-authorization.svg", 3360, "ENGAGE", "保护区与人工授权审查", "系统完成目标去重、证据时效、保护区、安全缓冲和链路状态检查，所有攻击仍需操作员明确授权。", "CV-01/AUTHORIZATION-STATUS", "telemetry", "command_product"),
        ("ASC-MEDIA-08", "08-post-strike-sar-bda.png", 4200, "ASSESS", "机场跑道变化检测与毁伤评估", "UAV-ISR-01 对机场跑道实施复查，变化产品只用于判断模拟跑道拒止效果，民用港区始终排除在目标集合之外。", "UAV-ISR-01/SAR", "radar", "raw_sensor_frame"),
        ("ASC-MEDIA-09", "09-recovery-summary.svg", 5520, "ASSESS", "舰载航空器分批回收与一次性载荷核销", "预警机先保持空中指挥直至无人机回收窗口建立；加油中继、侦察和攻击无人机按甲板调度分批返航，已执行末端攻击的空射巡飞载荷转为已消耗武器。", "CV-01/RECOVERY-STATUS", "telemetry", "command_product"),
    )
    return [
        media_record(
            MEDIA_ROOT,
            media_id,
            filename,
            at_sec=at_sec,
            phase=phase,
            title=title,
            caption=caption,
            sensor_id=sensor_id,
            modality=modality,
            mime_type="image/png" if filename.endswith(".png") else "image/svg+xml",
            checksum=CHECKSUMS[filename],
            capture_id=f"ASC-CAPTURE-{media_id.rsplit('-', 1)[-1]}",
            product_type=product_type,
        )
        for media_id, filename, at_sec, phase, title, caption, sensor_id, modality, product_type in rows
    ]


def _capture_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = [
        sensor_capability("ASC-CAP-EXT", "EXTERNAL-THEATER", "THEATER-MAP", sensor_type="precollected_map", modalities=["telemetry"], source_kind="external_source", parameters={"collection_age_sec": 300, "simulation_only": True}),
        sensor_capability("ASC-CAP-SAT-SAR", "SAT-A2S-01", "ORBITAL-SAR", sensor_type="spaceborne_sar", modalities=["radar"], configured_sensor="ORBITAL-SAR", parameters={"swath_width_km": 160, "ground_sample_distance_m": 3, "source_sensors": ["ORBITAL-SAR"]}),
        sensor_capability("ASC-CAP-ISR-EO", "UAV-ISR-01", "EO-IR", sensor_type="electro_optical", modalities=["eo_ir"], configured_sensor="EO-IR", parameters={"effective_range_nm": 60, "horizontal_fov_deg": 16, "resolution_px": [1672, 941]}),
        sensor_capability("ASC-CAP-C2-FUSION", "CV-01", "C2-FUSION", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "input_sources": ["SAT-A2S-01", "SAT-A2S-02", "SAT-COM-A2S-01", "UAV-ISR-01"]}),
        sensor_capability("ASC-CAP-ISR-IR", "UAV-ISR-01", "EO-IR", sensor_type="infrared", modalities=["eo_ir"], configured_sensor="EO-IR", parameters={"effective_range_nm": 24, "polarity": "white_hot", "resolution_px": [1672, 941]}),
        sensor_capability("ASC-CAP-ALLOC", "CV-01", "TARGET-ALLOCATION", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "target_deconfliction": True}),
        sensor_capability("ASC-CAP-CHAIN", "CV-01", "WEAPON-CHAIN", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "simulation_execution_only": True}),
        sensor_capability("ASC-CAP-AUTH", "CV-01", "AUTHORIZATION-STATUS", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "operator_authorization_required": True}),
        sensor_capability("ASC-CAP-BDA", "UAV-ISR-01", "SAR", sensor_type="airborne_sar", modalities=["radar"], configured_sensor="SAR", parameters={"effective_range_nm": 32, "ground_sample_distance_m": 1.2, "source_sensors": ["SAR"]}),
        sensor_capability("ASC-CAP-RECOVERY", "CV-01", "RECOVERY-STATUS", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "resource_accounting": True}),
    ]
    by_id = {item["capability_id"]: item for item in capabilities}
    specs = (
        ("ASC-MEDIA-00", "ASC-TASK-00", "ASC-CAP-EXT", "ingest", 0, 1, []),
        ("ASC-MEDIA-01", "ASC-TASK-01", "ASC-CAP-SAT-SAR", "derive", 300, 420, list(TARGET_IDS)),
        ("ASC-MEDIA-02", "ASC-TASK-02", "ASC-CAP-ISR-EO", "capture", 1200, 1320, ["COASTAL-AIRFIELD-01", "CIVILIAN-PORT-01"]),
        ("ASC-MEDIA-03", "ASC-TASK-03", "ASC-CAP-C2-FUSION", "command_product", 1740, 1860, list(TARGET_IDS)),
        ("ASC-MEDIA-04", "ASC-TASK-04", "ASC-CAP-ISR-IR", "capture", 2100, 2220, ["MOBILE-COASTAL-AD-01", "CIVILIAN-PORT-01"]),
        ("ASC-MEDIA-05", "ASC-TASK-05", "ASC-CAP-ALLOC", "command_product", 2640, 2760, list(TARGET_IDS)),
        ("ASC-MEDIA-06", "ASC-TASK-06", "ASC-CAP-CHAIN", "command_product", 3120, 3240, list(TARGET_IDS)),
        ("ASC-MEDIA-07", "ASC-TASK-07", "ASC-CAP-AUTH", "command_product", 3300, 3420, list(TARGET_IDS)),
        ("ASC-MEDIA-08", "ASC-TASK-08", "ASC-CAP-BDA", "capture", 4140, 4260, ["COASTAL-AIRFIELD-01"]),
        ("ASC-MEDIA-09", "ASC-TASK-09", "ASC-CAP-RECOVERY", "command_product", 5460, 5580, list(TARGET_IDS)),
    )
    tasks = [sensor_task(task_id, by_id[capability_id], task_type=task_type, start_sec=start, end_sec=end, target_refs=targets, branch_ids=["*"]) for media_id, task_id, capability_id, task_type, start, end, targets in specs]
    tasks_by_media = {media_id: task for (media_id, *_), task in zip(specs, tasks)}
    media_by_id = {item["media_id"]: item for item in _media_cues()}
    parameters = {
        "ASC-MEDIA-00": {"precollected": True, "simulation_only": True},
        "ASC-MEDIA-01": {"renderer_type": "radar_ppi", "swath_width_km": 160, "ground_sample_distance_m": 3, "registration_group": "ASC-AIRFIELD", "resolution_px": [1672, 941], "data_source": "sensor_observations"},
        "ASC-MEDIA-02": {"effective_range_nm": 60, "horizontal_fov_deg": 16, "point_at_target": True, "resolution_px": [1672, 941]},
        "ASC-MEDIA-03": {"renderer_type": "network_topology", "required_source_media_ids": ["ASC-MEDIA-01", "ASC-MEDIA-02"], "data_source": "network_and_track_state"},
        "ASC-MEDIA-04": {"effective_range_nm": 24, "polarity": "white_hot", "point_at_target": True, "resolution_px": [1672, 941]},
        "ASC-MEDIA-05": {"renderer_type": "resource_status", "data_source": "task_state", "assignment_mode": "target_specific"},
        "ASC-MEDIA-06": {"renderer_type": "execution_state", "data_source": "task_state", "simulation_execution_only": True},
        "ASC-MEDIA-07": {"renderer_type": "authorization_state", "data_source": "task_state", "operator_authorization_required": True},
        "ASC-MEDIA-08": {"effective_range_nm": 32, "ground_sample_distance_m": 1.2, "registration_group": "ASC-AIRFIELD", "reference_media_id": "ASC-MEDIA-01", "resolution_px": [1672, 941], "data_source": "sensor_observations"},
        "ASC-MEDIA-09": {"renderer_type": "resource_status", "data_source": "task_state", "resource_accounting": True},
    }
    captures = [
        capture_plan(
            media_by_id[media_id]["capture_id"], media_id, by_id[task["capability_id"]], task,
            product_type=media_by_id[media_id]["product_type"], at_sec=media_by_id[media_id]["at_sec"],
            target_refs=task["target_refs"], parameters=parameters[media_id], branch_ids=media_by_id[media_id]["branch_ids"],
        )
        for media_id, task in tasks_by_media.items()
    ]
    return capabilities, tasks, captures


def _timeline() -> list[dict[str, Any]]:
    return [
        {"cue_id": "ASC-CUE-01", "at_sec": 0, "phase": "FIND", "level": "INFO", "title": "航母特遣群建立航空作业区", "description": "CV-01、DDG-01、FFG-01 保持防空反潜警戒；AEW-01、UAV-TANKER-01、UAV-ISR-01 与 UAV-STRIKE-01 按甲板序列待命。", "media_ids": ["ASC-MEDIA-00"], "functional_agent_ids": ["A1", "A3"], "model_requirement_ids": ["M17"], "function_ids": []},
        {"cue_id": "ASC-CUE-02", "at_sec": 360, "phase": "FIND", "level": "INFO", "title": "卫星过境产品形成机场宽域线索", "description": "卫星已离开本地地图；其一次过境 SAR 产品发现跑道、沿海道路活动和民用港区，所有地面接触保持未知属性。", "media_ids": ["ASC-MEDIA-01"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M01", "M15", "M17"], "function_ids": ["KC-01", "KC-08"]},
        {"cue_id": "ASC-CUE-03", "at_sec": 720, "phase": "FIX", "level": "INFO", "title": "预警指挥与无人航空保障依次升空", "description": "AEW-01 先起飞建立海上预警与战术协同席位；UAV-TANKER-01 随后建立外海加油/通信保障航线，UAV-ISR-01 再沿海上安全航路实施复核。", "media_ids": [], "functional_agent_ids": ["A1", "A3"], "model_requirement_ids": ["M06", "M19"], "function_ids": ["KC-04", "KC-05"]},
        {"cue_id": "ASC-CUE-04", "at_sec": 1260, "phase": "FIX", "level": "INFO", "title": "光电复核固定设施布局", "description": "侦察无人机确认跑道、固定雷达与加固设施的空间关系，但不在传感器端直接给出敌我结论。", "media_ids": ["ASC-MEDIA-02"], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M05", "M07", "M20"], "function_ids": ["KC-06", "KC-07", "KC-09", "KC-10"]},
        {"cue_id": "ASC-CUE-04A", "at_sec": 1740, "phase": "TRACK", "level": "INFO", "title": "第二颗低轨卫星接替复访", "description": "侦察卫星02进入后续访问窗口，经通信中继卫星向CV-01下传区域变化与航迹更新；首颗卫星的SAR产品继续按有效期参与融合。", "media_ids": [], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M15", "M19"], "function_ids": []},
        {"cue_id": "ASC-CUE-05", "at_sec": 1800, "phase": "TRACK", "level": "WARNING", "title": "多源情报共享并持续跟踪岸防单元", "description": "CV-01 完成星、舰、机情报融合并保留任务级决策权；AEW-01 承担空中战术协同，UAV-TANKER-01 只为舰载机提供加油保障和数据链中继。", "media_ids": ["ASC-MEDIA-03", "ASC-MEDIA-04"], "functional_agent_ids": ["A1", "A2", "A3"], "model_requirement_ids": ["M02", "M10", "M11", "M19"], "function_ids": ["KC-11", "KC-12", "KC-13", "KC-14", "KC-15"]},
        {"cue_id": "ASC-CUE-06", "at_sec": 2700, "phase": "TARGET", "level": "WARNING", "title": "按目标类型完成差异化分配", "description": "空地导弹唯一分配给机场跑道拒止；攻击无人机携带的一枚空射巡飞载荷仅预分配给持续跟踪的机动岸防单元；民用港区列入禁射清单。", "media_ids": ["ASC-MEDIA-05"], "functional_agent_ids": ["A3", "A4", "A5"], "model_requirement_ids": ["M09", "M10", "M11", "M13", "M14"], "function_ids": ["KC-16", "KC-17", "KC-18", "KC-20", "KC-21"]},
        {"cue_id": "ASC-CUE-07", "at_sec": 3180, "phase": "ENGAGE", "level": "CRITICAL", "title": "有人—无人双波次武器链建立", "description": "AEW-01 协调第一波攻击无人机进入防区外等待点；其挂载的一枚空射巡飞攻击载荷尚未释放，只有在分配完成且安全航路成立后才进入独立巡飞区。", "media_ids": ["ASC-MEDIA-06"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M03", "M13", "M16"], "function_ids": ["KC-22", "KC-23"]},
        {"cue_id": "ASC-CUE-08", "at_sec": 3360, "phase": "ENGAGE", "level": "CRITICAL", "title": "第一波等待操作员明确授权", "description": "完成民用港区保护区、证据时效和目标去重检查后，攻击无人机才可释放一枚空地导弹实施机场跑道拒止。", "media_ids": ["ASC-MEDIA-07"], "functional_agent_ids": ["A5", "A6"], "model_requirement_ids": ["M04", "M13", "M16"], "function_ids": ["KC-24"]},
        {"cue_id": "ASC-CUE-09", "at_sec": 4200, "phase": "ASSESS", "level": "INFO", "title": "侦察无人机实施第一波毁伤评估", "description": "当前 SAR 变化产品确认固定目标效果，并判定机动威胁是否仍需第二波补充打击。", "media_ids": ["ASC-MEDIA-08"], "functional_agent_ids": ["A2", "A6"], "model_requirement_ids": ["M18"], "function_ids": ["KC-02", "KC-25", "KC-27"]},
        {"cue_id": "ASC-CUE-10", "at_sec": 4380, "phase": "ENGAGE", "level": "WARNING", "title": "空射巡飞攻击载荷按条件重新授权", "description": "空射巡飞载荷只能攻击持续跟踪、远离民用港区且仍具威胁的机动岸防单元；失联、证据过期或授权撤销时保持巡飞并转入预设处置。", "media_ids": [], "functional_agent_ids": ["A4", "A5", "A6"], "model_requirement_ids": ["M13", "M14", "M16"], "function_ids": ["KC-24", "KC-26"]},
        {"cue_id": "ASC-CUE-11", "at_sec": 5520, "phase": "ASSESS", "level": "INFO", "title": "甲板窗口内分批回收可回收资源", "description": "侦察、攻击及加油中继无人机先后返航，AEW-01 最后结束空中指挥并回收；已执行末端攻击的空射巡飞载荷完成核销，特遣群撤离。", "media_ids": ["ASC-MEDIA-09"], "functional_agent_ids": ["A3", "A6"], "model_requirement_ids": ["M14", "M18"], "function_ids": ["KC-28"]},
    ]


def _phase(at_sec: int, behavior: str, label: str, speed_kts: float, route: list[dict[str, Any]], *, mode: str = "hold", status: str = "active") -> dict[str, Any]:
    return {"at_sec": at_sec, "behavior": behavior, "label": label, "speed_kts": speed_kts, "status": status, "mode": mode, "route": route}


def build_air_space_sea_carrier_strike_scenario() -> dict[str, Any]:
    assets = [
        AssetSnapshot("SAT-A2S-01", "space", "低轨SAR侦察卫星01（星下点投影）", "active", 17.86, 121.34, alt_ft=1640420, heading=55, speed_kts=14500, sensors=["ORBITAL-SAR", "ELINT", "SATCOM"], endurance_hr=9999, autonomy_tier=4, health={"battery_pct": 97, "comms_strength": 98}, formation_role="first_orbital_access", network_role="intelligence_source"),
        AssetSnapshot("SAT-A2S-02", "space", "低轨SAR侦察卫星02（接力星下点投影）", "active", 17.74, 121.46, alt_ft=1706037, heading=54, speed_kts=14400, sensors=["ORBITAL-SAR", "ELINT", "SATCOM"], endurance_hr=9999, autonomy_tier=4, health={"battery_pct": 96, "comms_strength": 97}, formation_role="follow_on_orbital_access", network_role="intelligence_source"),
        AssetSnapshot("SAT-COM-A2S-01", "space", "地球同步通信中继卫星", "active", 18.20, 122.34, alt_ft=117421260, heading=0, speed_kts=0, sensors=["SATCOM"], endurance_hr=9999, autonomy_tier=3, health={"battery_pct": 98, "comms_strength": 99}, formation_role="persistent_communications_relay", network_role="communications_relay"),
        AssetSnapshot("CV-01", "maritime", "航母一号", "active", 18.18, 122.85, heading=285, speed_kts=16, sensors=["AESA_RADAR", "C2-FUSION", "SATCOM", "DATALINK"], endurance_hr=960, autonomy_tier=2, health={"fuel_pct": 94, "comms_strength": 99}, formation_role="carrier_task_group_flagship", network_role="mission_command"),
        AssetSnapshot("DDG-01", "maritime", "防空驱逐舰一号", "active", 18.42, 122.95, heading=285, speed_kts=17, sensors=["AESA_RADAR", "ESM", "DATALINK"], endurance_hr=720, autonomy_tier=2, health={"fuel_pct": 91, "comms_strength": 96}, formation_role="area_air_defense", network_role="escort_mesh"),
        AssetSnapshot("FFG-01", "maritime", "反潜护卫舰一号", "active", 17.95, 122.78, heading=285, speed_kts=16, sensors=["SURFACE_RADAR", "SONAR", "DATALINK"], endurance_hr=680, autonomy_tier=2, health={"fuel_pct": 90, "comms_strength": 95}, formation_role="anti_submarine_screen", network_role="escort_mesh"),
        AssetSnapshot("AEW-01", "air", "舰载有人预警指挥机一号", "active", 18.18, 122.85, alt_ft=25000, heading=300, speed_kts=300, sensors=["AEW_RADAR", "ESM", "IFF", "SATCOM", "DATALINK"], endurance_hr=6, autonomy_tier=2, health={"fuel_pct": 98, "comms_strength": 99}, formation_role="airborne_early_warning", network_role="airborne_battle_management"),
        AssetSnapshot("UAV-TANKER-01", "air", "舰载无人加油/通信中继机一号", "active", 18.18, 122.85, alt_ft=22000, heading=285, speed_kts=280, sensors=["SATCOM", "DATALINK"], endurance_hr=10, autonomy_tier=4, health={"fuel_pct": 98, "comms_strength": 98}, formation_role="carrier_based_tanker_relay", network_role="airborne_support_relay"),
        AssetSnapshot("UAV-ISR-01", "air", "舰载固定翼侦察无人机一号", "active", 18.17, 122.82, alt_ft=18000, heading=270, speed_kts=135, sensors=["SAR", "EO-IR", "ELINT", "DATALINK"], endurance_hr=12, autonomy_tier=4, health={"fuel_pct": 97, "comms_strength": 96}, formation_role="persistent_isr", network_role="intelligence_relay"),
        AssetSnapshot("UAV-STRIKE-01", "air", "舰载固定翼攻击无人机一号", "active", 18.18, 122.85, alt_ft=14000, heading=270, speed_kts=140, sensors=["EO-IR", "RWR", "DATALINK"], weapons=["舰载无人机空地导弹"], endurance_hr=8, autonomy_tier=4, health={"fuel_pct": 95, "comms_strength": 94}, formation_role="airfield_runway_strike_and_payload_carrier", network_role="weapon_chain_member"),
        AssetSnapshot("LOITER-UAV-01", "air", "空射巡飞攻击载荷一号", "active", 18.18, 122.85, alt_ft=6000, heading=270, speed_kts=90, sensors=["EO-IR", "DATALINK"], weapons=["巡飞攻击载荷"], endurance_hr=2, autonomy_tier=4, health={"battery_pct": 100, "comms_strength": 92}, formation_role="air_launched_mobile_target_attack", network_role="one_way_weapon_chain_member"),
    ]
    threats = [
        ThreatSnapshot("COASTAL-AIRFIELD-01", "疑似滨海机场跑道与保障区", "ground", 18.30, 121.78, risk_level="UNKNOWN", rcs_dbsm=20, ir_signature="low"),
        ThreatSnapshot("MOBILE-COASTAL-AD-01", "疑似机动岸防雷达/防空单元", "ground", 18.18, 121.88, heading=165, speed_kts=5, risk_level="UNKNOWN", rf_freq_mhz=5450, power_dbm=-45, rcs_dbsm=12, ir_signature="medium", behavior_script={"phases": [{"phase": 1, "name": "沿机场东侧海岸道路低速转场", "duration_sec": 2400, "heading": 165, "speed_kts": 5, "risk_level": "UNKNOWN"}, {"phase": 2, "name": "短暂停车并关闭辐射", "duration_sec": 900, "heading": 165, "speed_kts": 0, "risk_level": "UNKNOWN", "state_updates": {"power_dbm": -120}}, {"phase": 3, "name": "向备用阵位低速回撤", "duration_sec": 2700, "heading": 330, "speed_kts": 4, "risk_level": "UNKNOWN", "state_updates": {"power_dbm": -48}}]}),
        ThreatSnapshot("CIVILIAN-PORT-01", "民用渔港与救援码头", "ground", 18.08, 121.82, risk_level="UNKNOWN", rcs_dbsm=14, ir_signature="low", iff_status="civilian", ais_match=True),
    ]

    ship_routes = {
        "CV-01": [{"lat": 18.20, "lng": 122.82, "label": "CARRIER-LAUNCH-AREA"}, {"lat": 18.28, "lng": 122.76, "label": "CARRIER-NORTHWEST"}, {"lat": 18.18, "lng": 122.68, "label": "CARRIER-WEST"}, {"lat": 18.08, "lng": 122.76, "label": "CARRIER-SOUTH"}],
        "DDG-01": [{"lat": 18.42, "lng": 122.95}, {"lat": 18.49, "lng": 122.87}, {"lat": 18.40, "lng": 122.77}, {"lat": 18.33, "lng": 122.87}],
        "FFG-01": [{"lat": 17.95, "lng": 122.78}, {"lat": 18.03, "lng": 122.68}, {"lat": 17.94, "lng": 122.59}, {"lat": 17.86, "lng": 122.70}],
    }
    air_routes = {
        "SAT-A2S-01": [{"lat": 17.95, "lng": 121.46}, {"lat": 18.24, "lng": 121.88}, {"lat": 18.54, "lng": 122.30}],
        "SAT-A2S-02": [{"lat": 17.88, "lng": 121.60}, {"lat": 18.20, "lng": 121.96}, {"lat": 18.58, "lng": 122.42}],
        "AEW-01": [{"lat": 18.34, "lng": 122.66}, {"lat": 18.48, "lng": 122.50}, {"lat": 18.39, "lng": 122.36}, {"lat": 18.25, "lng": 122.52}],
        "UAV-TANKER-01": [{"lat": 18.12, "lng": 122.68}, {"lat": 18.20, "lng": 122.46}, {"lat": 18.10, "lng": 122.32}, {"lat": 18.02, "lng": 122.54}],
        "UAV-ISR-01": [{"lat": 18.21, "lng": 122.30}, {"lat": 18.26, "lng": 122.06}, {"lat": 18.31, "lng": 121.99}, {"lat": 18.37, "lng": 122.09}],
    }
    for index in range(1, 2):
        air_routes[f"UAV-STRIKE-{index:02d}"] = [
            {"lat": 18.14 + index * 0.018, "lng": 122.56 - index * 0.012, "label": f"STRIKE-{index:02d}-INGRESS"},
            {"lat": 18.14 + index * 0.025, "lng": 122.28 + index * 0.018, "label": f"STRIKE-{index:02d}-HOLD"},
            {"lat": 18.08 + index * 0.018, "lng": 122.38 + index * 0.022, "label": f"STRIKE-{index:02d}-ORBIT"},
        ]
    loiter_centers = ((18.20, 122.07),)
    for index, (lat, lng) in enumerate(loiter_centers, start=1):
        air_routes[f"LOITER-UAV-{index:02d}"] = [
            {"lat": 18.15 - index * 0.012, "lng": 122.30 - index * 0.015, "label": f"LOITER-{index:02d}-AIR-RELEASE"},
            {"lat": lat, "lng": lng, "label": f"LOITER-{index:02d}-STATION"},
            {"lat": lat + 0.035, "lng": lng + 0.025, "label": f"LOITER-{index:02d}-NORTH"},
            {"lat": lat - 0.035, "lng": lng + 0.015, "label": f"LOITER-{index:02d}-SOUTH"},
        ]
    routes = {**ship_routes, **air_routes}
    routes["SAT-COM-A2S-01"] = []
    route_modes = {asset_id: ("hold" if asset_id.startswith("SAT-A2S-") else "loop") for asset_id in routes}
    behavior_phases: dict[str, list[dict[str, Any]]] = {
        "SAT-A2S-01": [_phase(0, "awaiting_orbital_access", "等待首个轨道访问窗口", 0, [], status="staged"), _phase(240, "orbital_ground_track_pass", "首颗低轨卫星过境成像", 14500, air_routes["SAT-A2S-01"]), _phase(600, "outside_local_map", "已越出本地视区，星上任务继续", 14500, [], status="off_station")],
        "SAT-A2S-02": [_phase(0, "awaiting_follow_on_access", "等待接力轨道访问窗口", 0, [], status="staged"), _phase(1680, "follow_on_orbital_pass", "第二颗低轨卫星接力复访", 14400, air_routes["SAT-A2S-02"]), _phase(2040, "outside_local_map", "接力卫星越出本地视区", 14400, [], status="off_station")],
        "SAT-COM-A2S-01": [_phase(0, "persistent_satcom_relay", "持续承担星—舰通信中继", 0, [], status="active")],
        "CV-01": [_phase(0, "carrier_launch_area_transit", "航母航空作业区机动", 16, ship_routes["CV-01"], mode="loop"), _phase(3000, "carrier_strike_control_patrol", "双波次任务控制与回收准备", 12, ship_routes["CV-01"], mode="loop"), _phase(5700, "carrier_group_withdrawal", "完成全部航空器回收后向东侧外海撤离", 18, [{"lat": 18.12, "lng": 123.00}, {"lat": 18.06, "lng": 123.18}])],
        "DDG-01": [_phase(0, "area_air_defense_screen", "航母北侧区域防空警戒", 17, ship_routes["DDG-01"], mode="loop"), _phase(5700, "escort_withdrawal_screen", "掩护航母向东撤离", 19, [{"lat": 18.40, "lng": 123.07}, {"lat": 18.32, "lng": 123.23}])],
        "FFG-01": [_phase(0, "anti_submarine_screen", "航母南侧反潜警戒", 16, ship_routes["FFG-01"], mode="loop"), _phase(5700, "escort_withdrawal_screen", "保持反潜屏护向东撤离", 18, [{"lat": 17.94, "lng": 122.94}, {"lat": 17.88, "lng": 123.12}])],
    }
    behavior_phases["AEW-01"] = [_phase(0, "deck_standby", "飞行甲板预警值班位待命", 0, [], status="staged"), _phase(720, "carrier_launch", "从 CV-01 起飞建立外海预警轨道", 300, air_routes["AEW-01"], mode="loop"), _phase(1080, "airborne_battle_management", "保持空中预警、识别与战术协同", 240, air_routes["AEW-01"], mode="loop"), _phase(5580, "carrier_recovery", "确认无人机回收窗口后最后返航", 285, [{"lat": 18.36, "lng": 122.54}, {"lat": 18.28, "lng": 122.72}, {"lat": 18.18, "lng": 122.85}])]
    behavior_phases["UAV-TANKER-01"] = [_phase(0, "deck_standby", "无人加油机甲板待命", 0, [], status="staged"), _phase(840, "carrier_launch", "从 CV-01 起飞建立外海保障航线", 280, air_routes["UAV-TANKER-01"], mode="loop"), _phase(1320, "tanker_relay_racetrack", "执行加油待命与通信中继，不承担战术指挥", 220, air_routes["UAV-TANKER-01"], mode="loop"), _phase(5340, "carrier_recovery", "完成最后保障窗口后返航回收", 270, [{"lat": 18.10, "lng": 122.48}, {"lat": 18.13, "lng": 122.68}, {"lat": 18.18, "lng": 122.85}])]
    behavior_phases["UAV-ISR-01"] = [_phase(0, "deck_standby", "飞行甲板待命", 0, [], status="staged"), _phase(960, "carrier_launch", "从 CV-01 起飞实施复核", 135, air_routes["UAV-ISR-01"], mode="loop"), _phase(1500, "persistent_isr_orbit", "机场东侧外沿 SAR/光电/电子侦察盘旋", 120, air_routes["UAV-ISR-01"], mode="loop"), _phase(3900, "post_strike_bda_orbit", "第一波攻击后变化检测", 125, [{"lat": 18.30, "lng": 122.02}, {"lat": 18.37, "lng": 122.14}, {"lat": 18.22, "lng": 122.20}, {"lat": 18.14, "lng": 122.08}], mode="loop"), _phase(5040, "carrier_recovery", "完成综合评估后进入首个无人机回收窗口", 140, [{"lat": 18.24, "lng": 122.28}, {"lat": 18.21, "lng": 122.57}, {"lat": 18.18, "lng": 122.85}])]
    for index in range(1, 2):
        asset_id = f"UAV-STRIKE-{index:02d}"
        launch_at = 1260
        behavior_phases[asset_id] = [_phase(0, "deck_standby", "挂载空地导弹与一枚巡飞载荷在甲板待命", 0, [], status="staged"), _phase(launch_at, "carrier_launch", "从 CV-01 起飞建立机场拒止航线", 140, air_routes[asset_id], mode="loop"), _phase(2700, "standoff_weapon_hold", "机场东侧防区外释放航线等待授权", 125, air_routes[asset_id], mode="loop"), _phase(3600, "payload_release_and_recovery_hold", "空射巡飞载荷分离后在外海回收等待航线盘旋", 125, [{"lat": 18.16, "lng": 122.30}, {"lat": 18.09, "lng": 122.43}, {"lat": 18.15, "lng": 122.56}], mode="loop"), _phase(5160, "post_launch_carrier_recovery", "获得甲板窗口后沿东向航线返航", 145, [{"lat": 18.14, "lng": 122.48}, {"lat": 18.16, "lng": 122.68}, {"lat": 18.18, "lng": 122.85}])]
    for index in range(1, 2):
        asset_id = f"LOITER-UAV-{index:02d}"
        launch_at = 3600
        behavior_phases[asset_id] = [_phase(0, "carried_standby", "作为载荷挂载于 UAV-STRIKE-01，尚未释放", 0, [], status="staged"), _phase(launch_at, "air_launch", f"由 UAV-STRIKE-01 空中释放巡飞攻击载荷{index:02d}", 105, air_routes[asset_id], mode="loop"), _phase(3900, "assigned_loiter_area", "只在已分配机动目标附近的安全巡飞区等待", 85, air_routes[asset_id][1:], mode="loop"), _phase(4200, "conditional_attack_hold", "等待第一波评估和操作员再次授权", 90, air_routes[asset_id][1:], mode="loop")]

    visibility_windows = {
        "SAT-A2S-01": {"visible_from_sec": 240, "visible_until_sec": 600}, "SAT-A2S-02": {"visible_from_sec": 1680, "visible_until_sec": 2040}, "AEW-01": {"visible_from_sec": 720}, "UAV-TANKER-01": {"visible_from_sec": 840}, "UAV-ISR-01": {"visible_from_sec": 960},
        "UAV-STRIKE-01": {"visible_from_sec": 1260}, "LOITER-UAV-01": {"visible_from_sec": 3600},
    }
    asset_ammo = {"UAV-STRIKE-01": {"舰载无人机空地导弹": 1}, "LOITER-UAV-01": {"巡飞攻击载荷": 1}}

    extra_devices = [
        {"device_id": "AGM-01", "name": "空地导弹01", "device_type": "air_to_ground_missile", "status": "stowed", "asset_ref": "UAV-STRIKE-01"},
        {"device_id": "LOITER-PAYLOAD-01", "name": "空射巡飞攻击载荷01", "device_type": "air_launched_one_way_payload", "status": "carried", "asset_ref": "UAV-STRIKE-01", "carried_asset_ref": "LOITER-UAV-01"},
    ]
    compute_nodes = [
        compute_node("ASC-SAT-COMPUTE", "卫星任务处理节点", "SAT-A2S-01", host_device_type="spacecraft", compute_type="space_edge", cpu="16 cores", accelerator="sar_processor", memory_gb=64, network="satcom"),
        compute_node("ASC-SAT-COMPUTE-02", "接力卫星任务处理节点", "SAT-A2S-02", host_device_type="spacecraft", compute_type="space_edge", cpu="16 cores", accelerator="sar_processor", memory_gb=64, network="satcom"),
        compute_node("ASC-SAT-RELAY-COMPUTE", "通信中继卫星路由节点", "SAT-COM-A2S-01", host_device_type="spacecraft", compute_type="space_relay", cpu="8 cores", accelerator="link_processor", memory_gb=32, network="satcom"),
        compute_node("ASC-CV-COMPUTE", "航母联合任务计算节点", "CV-01", host_device_type="aircraft_carrier", compute_type="maritime_command", cpu="96 cores", accelerator="server_gpu", memory_gb=384, network="multi_domain_fabric"),
        compute_node("ASC-AEW-COMPUTE", "预警机战术协同节点", "AEW-01", host_device_type="crewed_aew_aircraft", compute_type="airborne_battle_management", cpu="32 cores", accelerator="mission_processor", memory_gb=128, network="tactical_air_link"),
        compute_node("ASC-TANKER-UAV-COMPUTE", "无人加油机链路保障节点", "UAV-TANKER-01", host_device_type="tanker_uav", compute_type="airborne_support_relay", cpu="12 cores", accelerator="link_processor", memory_gb=48, network="tactical_air_link"),
        compute_node("ASC-ISR-COMPUTE", "侦察无人机融合节点", "UAV-ISR-01", host_device_type="isr_uav", compute_type="air_edge", cpu="24 cores", accelerator="radar_dsp", memory_gb=96, network="tactical_air_link"),
        compute_node("ASC-STRIKE-01-COMPUTE", "攻击无人机01协同节点", "UAV-STRIKE-01", host_device_type="strike_uav", compute_type="air_edge", cpu="8 cores", accelerator="embedded_ai", memory_gb=24, network="tactical_air_link"),
        compute_node("ASC-LOITER-01-COMPUTE", "空射巡飞载荷01预留智能节点", "LOITER-UAV-01", host_device_type="loitering_munition", compute_type="onboard_guidance", status="standby", cpu="4 cores", accelerator="embedded_ai", memory_gb=8, network="weapon_datalink"),
    ]
    deployments = [
        agent_deployment("A1", "ASC-SAT-COMPUTE", roles=["wide_area_detection", "spaceborne_sar_ingest"]),
        agent_deployment("A1", "ASC-SAT-COMPUTE-02", roles=["follow_on_revisit", "track_freshness_update"]),
        agent_deployment("A3", "ASC-SAT-RELAY-COMPUTE", roles=["persistent_satcom_relay", "store_and_forward"]),
        agent_deployment("A1", "ASC-ISR-COMPUTE", roles=["airfield_reconnaissance", "multi_source_perception"]),
        agent_deployment("A2", "ASC-ISR-COMPUTE", roles=["mobile_target_tracking", "evidence_fusion"]),
        agent_deployment("A2", "ASC-AEW-COMPUTE", roles=["airborne_track_coordination", "surface_surveillance_coordination"]),
        agent_deployment("A3", "ASC-CV-COMPUTE", roles=["mission_decomposition", "target_weapon_allocation"]),
        agent_deployment("A3", "ASC-AEW-COMPUTE", roles=["airborne_battle_management", "strike_coordination"]),
        agent_deployment("A3", "ASC-TANKER-UAV-COMPUTE", roles=["datalink_relay", "air_refueling_support"]),
        agent_deployment("A4", "ASC-CV-COMPUTE", roles=["two_wave_plan_generation", "conditional_replanning"]),
        agent_deployment("A5", "ASC-CV-COMPUTE", roles=["roe_review", "protected_zone_check"]),
        agent_deployment("A6", "ASC-CV-COMPUTE", roles=["weapon_chain_control", "resource_accounting"]),
        agent_deployment("A6", "ASC-STRIKE-01-COMPUTE", roles=["airfield_denial_execution", "post_launch_recovery"]),
        agent_deployment("A6", "ASC-LOITER-01-COMPUTE", roles=["mobile_coastal_ad_terminal_guidance"], runtime_status="standby", notes="空射前保持断开；预留弹载智能体只提供制导与状态监测，目标选择由上级节点完成且必须人工授权。"),
    ]
    capabilities, task_schedule, capture_plans = _capture_contract()
    timeline = _timeline()
    target_engagements = {
        "COASTAL-AIRFIELD-01": {"asset_id": "UAV-STRIKE-01", "weapon_name": "舰载无人机空地导弹", "wave": 1, "not_before_sec": 3360, "role": "airfield_runway_denial", "action_label": "AGM-01 实施机场跑道拒止"},
        "MOBILE-COASTAL-AD-01": {"asset_id": "LOITER-UAV-01", "weapon_name": "巡飞攻击载荷", "wave": 2, "not_before_sec": 4380, "requires_completed_target_ids": ["COASTAL-AIRFIELD-01"], "requires_damage_assessment": True, "role": "mobile_coastal_ad_attack", "action_label": "空射巡飞载荷01条件末端攻击机动岸防单元", "expend_source_asset": True},
    }
    for assignment in target_engagements.values():
        wave = int(assignment["wave"])
        assignment.update({
            "authorize_wave_as_group": True,
            "coordination_mode": "time_on_target",
            "chain_id": f"ASC-WAVE-{wave:02d}",
            "arrival_tolerance_sec": 30 if wave == 1 else 45,
            "max_launch_stagger_sec": 180 if wave == 1 else 600,
            "authorization_message": (
                "是否授权第一波攻击无人机按目标专用分配实施机场跑道拒止？"
                if wave == 1 else
                "是否依据第一波毁伤评估，授权第二波空射巡飞载荷攻击仍具威胁的机动岸防单元？"
            ),
            "wave_action_label": (
                "第一波：1枚空地导弹实施机场跑道拒止"
                if wave == 1 else
                "第二波：1枚空射巡飞载荷条件打击机动岸防单元"
            ),
        })
    return {
        "schema_version": "amos.scenario.v2", "id": SCENARIO_ID,
        "name": "空天海舰载无人航空联合对陆演示",
        "operator_brief": "本剧本按公开能力边界构造近未来联合演示，不把尚未成熟的“大规模无人机航母蜂群作战”写成既有实战能力。两颗低轨侦察卫星按先后访问窗口提供虚构滨海机场线索和区域复访更新，通信中继卫星保障星—舰链路。CV-01 在 DDG-01、FFG-01 屏护下，按甲板作业序列先放飞1架有人预警指挥机，再放飞1架无人加油/通信中继机、1架固定翼侦察无人机和1架携带空地导弹及空射巡飞载荷的固定翼攻击无人机。CV-01 保留任务级决策权，AEW-01 负责空中预警与战术协同，UAV-TANKER-01 只负责加油保障和链路中继。场景只保留疑似机场、疑似机动岸防雷达/防空单元及受保护民用渔港三个沿海接触；先实施机场跑道拒止，再依据毁伤评估决定是否授权空射巡飞载荷攻击仍具威胁的机动单元。",
        "description": "验证双低轨侦察星接力、通信卫星持续中继、有人预警指挥、无人加油/链路保障、舰载无人机甲板排序起降、星舰机情报共享、空射巡飞载荷、差异化目标分配、民用港区禁射约束、双波次授权与分批回收闭环。该流程属于公开试验与现役保障模式启发下的仿真化组合，而非对任何真实行动的复刻。",
        "scenario_type": "scripted_agent_demo",
        "concept_maturity": "public-capability-inspired_near-future_exercise",
        "realism_basis": {
            "model": "公开舰载无人机试验、舰载无人加油系统与现役有人预警指挥体系的组合仿真",
            "constraints": [
                "无人加油机不承担攻击决策或编队指挥，只执行加油保障与通信中继。",
                "空中战术协同由有人预警指挥机承担，航母任务节点保留任务级决策和授权链。",
                "固定翼无人机按单机、分时甲板作业建模，不以抽象蜂群代替独立平台。",
                "空射巡飞攻击载荷属于近未来演示设定，必须先分配目标、后释放、再人工授权末端攻击。",
            ],
        },
        "theater": {"theater_id": "north_luzon_east_coast_carrier_training", "name": "北吕宋东岸—菲律宾海西部虚构空天海训练区", "location_profile": "fictional_training_area", "center": {"lat": 18.20, "lng": 122.34}, "zoom": 9, "ao": {"north": 19.15, "south": 17.45, "east": 123.35, "west": 121.30}},
        "map_display": {"relief_manifest": "/static/assets/maps/taiwan-se-relief/manifest.json", "default_layers": {"sensors": False, "ao": True, "coordination": False}, "track_style": "tactical_joint", "base_surface": "coastal", "focus_bounds": {"north": 18.58, "south": 17.82, "east": 123.08, "west": 121.52}, "exclude_domains_from_focus": ["space"], "space_node_asset_ids": ["SAT-COM-A2S-01"], "space_visual_speed_factor": 0.035, "space_ground_tracks": [{"asset_id": "SAT-A2S-01", "label": "侦察卫星01预测星下轨迹", "access_start_sec": 240, "access_end_sec": 600, "color": "#9eb2c8", "points": [{"lat": 17.86, "lng": 121.34}, {"lat": 17.95, "lng": 121.46}, {"lat": 18.24, "lng": 121.88}, {"lat": 18.54, "lng": 122.30}]}, {"asset_id": "SAT-A2S-02", "label": "侦察卫星02接力星下轨迹", "access_start_sec": 1680, "access_end_sec": 2040, "color": "#9da7bf", "points": [{"lat": 17.74, "lng": 121.46}, {"lat": 17.88, "lng": 121.60}, {"lat": 18.20, "lng": 121.96}, {"lat": 18.58, "lng": 122.42}]}], "trail_window_sec": 360, "track_trail_window_sec": 480, "label_asset_ids": ["CV-01", "AEW-01", "UAV-TANKER-01", "UAV-ISR-01", "UAV-STRIKE-01", "LOITER-UAV-01"], "trail_asset_ids": ["UAV-ISR-01", "UAV-STRIKE-01", "LOITER-UAV-01"]},
        "space_operations": {
            "title": "空天支援 · 双星接力",
            "relay": {"asset_id": "SAT-COM-A2S-01", "label": "通信中继在线"},
            "passes": [
                {"asset_id": "SAT-A2S-01", "label": "侦察星01 · 首次SAR访问", "access_start_sec": 240, "capture_sec": 360, "access_end_sec": 600},
                {"asset_id": "SAT-A2S-02", "label": "侦察星02 · 接力复访", "access_start_sec": 1680, "capture_sec": 1740, "access_end_sec": 2040},
            ],
            "intelligence_products": [
                {"product_id": "ASC-MEDIA-01", "label": "机场宽域SAR线索", "source_asset_id": "SAT-A2S-01", "captured_at_sec": 360, "received_at_sec": 390, "valid_until_sec": 1740},
                {"product_id": "ASC-ORBITAL-REVISIT-01", "label": "接力复访航迹更新", "source_asset_id": "SAT-A2S-02", "captured_at_sec": 1740, "received_at_sec": 1800, "valid_until_sec": 2700},
            ],
            "note": "卫星离场不删除已下传产品；超过有效期后由舰载侦察无人机当前观测接续。",
        },
        "environment": {"weather": "partly_cloudy", "visibility_nm": 28, "wind_speed_kts": 14, "wind_direction_deg": 80, "sea_state": 3, "precipitation": "none", "intermittent_jamming": True, "terrain_profile": "tropical_coastal_airfield", "civilian_exclusion_zone": "ASC-PROTECTED-01"},
        "asset_routes": routes, "asset_route_modes": route_modes, "asset_behavior_phases": behavior_phases,
        "asset_motion_windows": {
            "SAT-A2S-01": {"start_sec": 240}, "SAT-A2S-02": {"start_sec": 1680},
            "AEW-01": {"start_sec": 720, "launch_from_asset": "CV-01", "align_route_heading": True},
            "UAV-TANKER-01": {"start_sec": 840, "launch_from_asset": "CV-01", "align_route_heading": True}, "UAV-ISR-01": {"start_sec": 960, "launch_from_asset": "CV-01", "align_route_heading": True},
            "UAV-STRIKE-01": {"start_sec": 1260, "launch_from_asset": "CV-01", "align_route_heading": True},
            "LOITER-UAV-01": {"start_sec": 3600, "launch_from_asset": "UAV-STRIKE-01", "align_route_heading": True},
        },
        "flight_deck_cycle": [
            {"sequence": 1, "at_sec": 720, "asset_id": "AEW-01", "operation": "launch", "purpose": "先建立空中预警与战术协同"},
            {"sequence": 2, "at_sec": 840, "asset_id": "UAV-TANKER-01", "operation": "launch", "purpose": "建立加油与通信保障轨道"},
            {"sequence": 3, "at_sec": 960, "asset_id": "UAV-ISR-01", "operation": "launch", "purpose": "沿安全航路实施目标复核"},
            {"sequence": 4, "at_sec": 1260, "asset_id": "UAV-STRIKE-01", "operation": "launch", "purpose": "携带两类目标专用武器进入待命区"},
            {"sequence": 5, "at_sec": 5040, "asset_id": "UAV-ISR-01", "operation": "recover", "purpose": "完成毁伤评估后优先回收"},
            {"sequence": 6, "at_sec": 5160, "asset_id": "UAV-STRIKE-01", "operation": "recover", "purpose": "攻击任务结束后回收"},
            {"sequence": 7, "at_sec": 5340, "asset_id": "UAV-TANKER-01", "operation": "recover", "purpose": "保障最后一个无人机回收窗口"},
            {"sequence": 8, "at_sec": 5580, "asset_id": "AEW-01", "operation": "recover", "purpose": "所有无人机回收后结束空中指挥"},
        ],
        "asset_visibility_windows": visibility_windows, "asset_ammo": asset_ammo,
        "asset_profiles": asset_profiles(assets), "physical_devices": physical_devices(assets, extra_devices),
        "compute_nodes": compute_nodes, "agent_deployments": deployments,
        "asset_capabilities": capabilities, "asset_task_schedule": task_schedule, "capture_plans": capture_plans,
        "evidence_classification_rules": [
            {"rule_id": "ASC-ID-AIRFIELD", "target_ref": "COASTAL-AIRFIELD-01", "required_media_ids": ["ASC-MEDIA-01", "ASC-MEDIA-02"], "classification": "AIRFIELD_RUNWAY", "retain_until_sec": 6000},
            {"rule_id": "ASC-ID-CIVILIAN-PORT", "target_ref": "CIVILIAN-PORT-01", "required_media_ids": ["ASC-MEDIA-01", "ASC-MEDIA-02"], "classification": "CIVILIAN_PORT", "retain_until_sec": 6000},
            {"rule_id": "ASC-ID-MOBILE-AD", "target_ref": "MOBILE-COASTAL-AD-01", "required_media_ids": ["ASC-MEDIA-04"], "classification": "MOBILE_COASTAL_AIR_DEFENSE", "retain_until_sec": 6000},
        ],
        "threat_observation_windows": {"COASTAL-AIRFIELD-01": {"start_sec": 300}, "MOBILE-COASTAL-AD-01": {"start_sec": 1800}, "CIVILIAN-PORT-01": {"start_sec": 1260}},
        "assets": assets, "threats": threats,
        "protected_assets": [{"asset_id": "ASC-PROTECTED-01", "asset_name": "虚构民用渔港与救援码头保护区", "asset_type": "civilian_port", "lat": 18.08, "lon": 121.82, "alt": 0, "protection_radius_m": 2500, "criticality": 0.94, "status": "protected", "metadata": {"simulation_only": True, "location_profile": "fictional_training_area"}}],
        "coordination_links": [
            {"link_id": "ASC-LINK-SAT1-RELAY", "link_type": "intelligence", "source_asset_id": "SAT-A2S-01", "target_asset_id": "SAT-COM-A2S-01", "active_from_sec": 240, "label": "侦察星01过境下传"},
            {"link_id": "ASC-LINK-SAT2-RELAY", "link_type": "intelligence", "source_asset_id": "SAT-A2S-02", "target_asset_id": "SAT-COM-A2S-01", "active_from_sec": 1680, "label": "侦察星02接力下传"},
            {"link_id": "ASC-LINK-RELAY-CV", "link_type": "intelligence", "source_asset_id": "SAT-COM-A2S-01", "target_asset_id": "CV-01", "active_from_sec": 240, "label": "通信卫星持续星—舰中继"},
            {"link_id": "ASC-LINK-ISR-CV", "link_type": "intelligence", "source_asset_id": "UAV-ISR-01", "target_asset_id": "CV-01", "active_from_sec": 1260, "label": "侦察数据回传"},
            {"link_id": "ASC-LINK-CV-AEW", "link_type": "command", "source_asset_id": "CV-01", "target_asset_id": "AEW-01", "active_from_sec": 720, "label": "航母任务级指挥"},
            {"link_id": "ASC-LINK-AEW-ISR", "link_type": "command", "source_asset_id": "AEW-01", "target_asset_id": "UAV-ISR-01", "active_from_sec": 960, "label": "侦察无人机战术协同"},
            {"link_id": "ASC-LINK-AEW-STRIKE", "link_type": "command", "source_asset_id": "AEW-01", "target_asset_id": "UAV-STRIKE-01", "active_from_sec": 1260, "label": "攻击无人机战术协同"},
            {"link_id": "ASC-LINK-TANKER-STRIKE", "link_type": "intelligence", "source_asset_id": "UAV-TANKER-01", "target_asset_id": "UAV-STRIKE-01", "active_from_sec": 1320, "label": "空中加油/链路保障"},
            {"link_id": "ASC-LINK-STRIKE-LOITER", "link_type": "command", "source_asset_id": "UAV-STRIKE-01", "target_asset_id": "LOITER-UAV-01", "active_from_sec": 3600, "label": "空射载荷分离与目标约束下传"},
            *[{"link_id": f"ASC-WEAPON-{index:02d}", "link_type": "weapon", "source_asset_id": assignment["asset_id"], "target_ref": target_id, "active_from_sec": 2700 if assignment["wave"] == 1 else 4200, "label": assignment["action_label"]} for index, (target_id, assignment) in enumerate(target_engagements.items(), start=1)],
        ],
        "timeline": timeline, "media_cues": bind_media_consumers(_media_cues(), timeline), "cover_media_id": "ASC-MEDIA-00",
        "demo_controls": {"recommended_speed": 8, "duration_sec": 6000, "auto_agent_interval_sec": 600, "show_truth": False, "latest_visual_only": True, "auto_stop": True, "advance_while_analyzing": True},
        "default_seed": 76091, "supported_modes": ["integration", "demonstration"],
        "functional_agents": scenario_agents(), "required_agents": required_backend_roles(),
        "algorithm_coverage": planned_algorithms("clustering", "association", "linear_regression", "logistic_regression", "random_forest", "neural_network", "naive_bayes_network", "large_language_model", "retrieval_augmented_generation", "agent_collaboration", "federated_learning", "reinforcement_learning", "explainable_ai", "multimodal_fusion", "time_series_prediction", "real_time_object_detection", "change_detection", "multi_target_tracking", "graph_neural_network"),
        "function_point_coverage": planned_function_points(*[f"KC-{index:02d}" for index in range(1, 29)]),
        "conditional_function_points": ["KC-03", "KC-26"],
        "function_runtime_triggers": {"KC-22": {"event": "authorized_fire_command", "offset_sec": 0}, "KC-23": {"event": "authorized_fire_command", "offset_sec": 5}, "KC-24": {"event": "authorized_fire_command", "offset_sec": 15}, "KC-25": {"event": "weapon_hit", "offset_sec": 0}, "KC-02": {"event": "weapon_hit", "offset_sec": 15}, "KC-27": {"event": "weapon_hit", "offset_sec": 30}, "KC-28": {"event": "damage_assessment_confirmed", "offset_sec": 0}},
        "demo_checkpoints": [
            {"checkpoint_id": "ASC-CP-SAT", "title": "卫星机场线索就绪", "min_elapsed_sec": 390, "conditions": {"stable_track_count_at_least": 1, "media_ids_released": ["ASC-MEDIA-01"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "ASC-CP-FIX", "title": "机场与民用港区复核输入就绪", "min_elapsed_sec": 1290, "conditions": {"stable_track_count_at_least": 2, "media_ids_released": ["ASC-MEDIA-02"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "ASC-CP-TRACK", "title": "机动岸防单元跟踪输入就绪", "min_elapsed_sec": 2190, "conditions": {"stable_track_count_at_least": 3, "media_ids_released": ["ASC-MEDIA-04"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "ASC-CP-PLAN", "title": "差异化火力分配就绪", "min_elapsed_sec": 2730, "conditions": {"stable_track_count_at_least": 3, "media_ids_released": ["ASC-MEDIA-04"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "ASC-CP-WAVE1", "title": "第一波固定目标打击等待授权", "min_elapsed_sec": 3390, "conditions": {"media_ids_released": ["ASC-MEDIA-06", "ASC-MEDIA-07"]}, "pause": True, "submit_analysis": True, "requires_operator_action": True},
            {"checkpoint_id": "ASC-CP-WAVE2", "title": "第二波机动目标补充打击决策", "min_elapsed_sec": 4230, "conditions": {"media_ids_released": ["ASC-MEDIA-08"]}, "pause": True, "submit_analysis": True, "requires_operator_action": True},
            {"checkpoint_id": "ASC-CP-CLOSE", "title": "综合评估与分批回收完成", "min_elapsed_sec": 5850, "conditions": {"media_ids_released": ["ASC-MEDIA-09"]}, "pause": True, "submit_analysis": True, "requires_operator_action": True},
        ],
        "fault_injections": [{"fault_id": "ASC-FAULT-DATALINK", "type": "communication_degradation", "target": "UAV-TANKER-01", "at_checkpoint": "ASC-CP-PLAN", "status": "available"}, {"fault_id": "ASC-FAULT-ISR", "type": "resource_unavailable", "target": "UAV-ISR-01", "at_checkpoint": "ASC-CP-FIX", "status": "available"}],
        "expected_branches": [
            {"branch_id": "standard", "name": "固定目标先打、机动目标后打", "description": "第一波打击固定目标，依据毁伤评估决定第二波巡飞攻击。"},
            {"branch_id": "mobile_target_relocated", "name": "机动目标转移", "description": "目标移动后废止旧坐标并重新规划巡飞区。"},
            {"branch_id": "emitter_silent", "name": "雷达关机", "description": "不得依据过期电子侦察直接攻击，保持巡飞并等待重新确认。"},
            {"branch_id": "protected_zone_abort", "name": "目标进入保护区", "description": "目标进入保护区或安全缓冲时中止末端攻击。"},
            {"branch_id": "communication_degraded", "name": "数据链降级", "description": "失联时执行安全巡飞或预设处置，不自主选择新目标。"},
            {"branch_id": "wave1_effect_insufficient", "name": "第一波效果不足", "description": "保留已在安全巡飞区等待的空射巡飞载荷作为有条件补充打击资源。"},
        ],
        "default_branch": "standard",
        "acceptance_profile": {"functional_agents": 6, "core_algorithms": 15, "engineering_models": 4, "function_points": 28, "evidence_policy": "backend_trace_only", "required_target_count": 3, "requires_explicit_fire_authorization": True, "requires_coordination_chain": True, "requires_damage_assessment": True},
        "strike_waves": [
            {"wave_id": "ASC-WAVE-01", "name": "机场跑道拒止", "not_before_sec": 3360, "target_ids": ["COASTAL-AIRFIELD-01"], "release_mode": "operator_authorized_target_specific", "time_on_target_tolerance_sec": 30},
            {"wave_id": "ASC-WAVE-02", "name": "机动岸防单元条件打击", "not_before_sec": 4380, "requires_bda_of_wave": "ASC-WAVE-01", "target_ids": ["MOBILE-COASTAL-AD-01"], "release_mode": "operator_reauthorized_target_specific", "abort_on_protected_zone": True, "abort_on_stale_track": True},
        ],
        "engagement_policy": {
            "decision_authority": "operator", "requires_backend_identification": True, "requires_explicit_authorization": True, "requires_prior_warning": False,
            "minimum_threat_levels": ["HIGH", "CRITICAL"], "eligible_kill_chain_phases": ["TARGET", "ENGAGE"],
            "authorized_asset_ids": ["UAV-STRIKE-01", "LOITER-UAV-01"],
            "authorized_weapons": ["舰载无人机空地导弹", "巡飞攻击载荷"],
            "protected_classifications": ["CIVILIAN", "HOSPITAL", "SCHOOL", "RESIDENTIAL", "FRIENDLY"], "protected_truth_ids": ["CIVILIAN-PORT-01"], "protected_asset_buffer_nm": 2.5,
            "max_salvos_per_track": 1, "bda_not_before_sec": 4140, "bda_observer_asset_ids": ["UAV-ISR-01"], "stochastic_damage_assessment": True,
            "action_label": "目标专用武器链", "authorization_message": "是否授权当前目标对应的专用火力单元执行模拟打击？",
            "target_engagements": target_engagements,
            "post_launch_routes": {"UAV-STRIKE-01": behavior_phases["UAV-STRIKE-01"][-2]["route"]},
            "post_launch_behaviors": {"UAV-STRIKE-01": {"behavior": "payload_release_and_recovery_hold", "label": "释放空地导弹后转入空射载荷分离与回收等待航线", "speed_kts": 125}},
            "post_bda_routes": {"UAV-ISR-01": behavior_phases["UAV-ISR-01"][-1]["route"], "CV-01": behavior_phases["CV-01"][-1]["route"]},
            "post_bda_behaviors": {"UAV-ISR-01": {"behavior": "carrier_recovery", "label": "完成毁伤评估后进入首个回收窗口", "speed_kts": 140}, "CV-01": {"behavior": "carrier_recovery_operations", "label": "保持航空作业航向直至全部资源回收", "speed_kts": 12}},
        },
        "agent_plan": {"mode": "commander_workflow", "steps": ["submit_current_snapshot", "execute_a1_a6_workflow", "fuse_space_air_intelligence", "coordinate_crewed_uncrewed_air_plan", "allocate_target_specific_weapons", "review_protected_zone", "request_wave1_authorization", "assess_wave1_effects", "conditionally_request_wave2_authorization", "recover_reusable_assets"], "commander_options": {"mission_type": "air_space_sea_carrier_airfield_strike", "task_goal": "依据卫星接力过境、舰载侦察无人机和融合航迹证据识别虚构滨海机场、机动岸防单元及民用港区；由航母保留任务级决策、有人预警机协调空中行动、无人加油机提供保障，为1架攻击无人机携带的空地导弹和1枚空射巡飞载荷生成目标唯一、可审计、分波次的对陆任务建议。", "analysis_guidance": "机场跑道拒止使用空地导弹；空射巡飞载荷仅在已完成目标分配、空中释放、持续跟踪、远离民用港区并取得第一波毁伤评估后用于机动岸防单元。禁止由无人加油机越权指挥，禁止依据剧本真值、过期坐标或重复分配执行。", "knowledge_base": ["本剧本属于公开能力启发的近未来联合演示，不宣称存在经过实战验证的大规模无人机航母蜂群模式。", "所有资源均按独立编号建模，不使用抽象群体代替实际单机。", "卫星只以短时过境形成线索，离开本地战区后不在地图悬停；已下传产品按有效期使用。", "CV-01保留任务级决策与授权链，AEW-01负责空中预警、识别和战术协同。", "UAV-TANKER-01仅负责加油保障和通信中继，不承担攻击决策；UAV-ISR-01负责持续侦察和毁伤评估。", "舰载航空器按照预警机、保障无人机、侦察无人机、攻击无人机的甲板序列升空，并按侦察、攻击、保障、预警的顺序回收。", "LOITER-UAV-01起初作为UAV-STRIKE-01的挂载载荷隐藏，完成目标分配后才在海上安全航路空射分离。", "空地导弹唯一分配给机场，空射巡飞载荷唯一预分配给机动岸防单元；民用港区始终禁射。", "巡飞载荷在攻击授权后转为已消耗武器，不得返航或再次攻击。", "任一目标进入保护区、当前航迹失效或链路中断时，必须中止攻击并返回重规划。", "未来弹载智能体只能在预留节点上提供制导与状态监测，不得越过上级目标分配和操作员授权。"]}},
        "events": [item["title"] for item in timeline],
    }


__all__ = ["SCENARIO_ID", "build_air_space_sea_carrier_strike_scenario"]
