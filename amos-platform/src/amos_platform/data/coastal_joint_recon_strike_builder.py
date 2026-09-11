"""Formal scenario: coastal multi-domain reconnaissance and coordinated strike."""

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


SCENARIO_ID = "coastal-joint-recon-strike"
MEDIA_ROOT = f"/static/assets/scenarios/{SCENARIO_ID}"
CHECKSUMS = {
    "00-theater-overview.svg": "8e6f227a2ce5d73846e6868d802bab5fcec29bbf46cecc4224ec32f8928ca0e2",
    "01-satellite-sar-v2.png": "34812f03e6adc968a2b76b8df3f0d11829a4e38b77929804d8c2315645557b0e",
    "02-wz10-sar-v2.png": "6ff299cb8665844978db39c9df15cd63781b865972431fc00d79efbb1b3f1733",
    "03-wz10-eo-v2.png": "434e7433494fc27417acb7d6372b4d2505778158c14f0dc36071012daf415e31",
    "04-intelligence-fusion.svg": "483d8d5aebe304cba362aba173b782fa4450ca2c69418d09f931e3cbe4d284dd",
    "05-tactical-command.svg": "df2fe54634d402a8c57fba2066e182ec1feedae88f6c954006afe9a226145546",
    "06-weapon-chain.svg": "a522c73c9f0df96359fb7ff03423bd7833c0f6a0a5572e3515f7e039d8f624ec",
    "07-bda-sar-v2.png": "a3613e1a7b1f88130022bd444b9f0cac0834e51b1b3bdf61bf2bdb5d9f7a17f0",
    "08-uav01-eo-route.png": "7a6787e4b27dab1ca5f06025b1fbee36fc88ae6eb6c58dc6934790ddeb3914c6",
    "09-uav02-ir-route.png": "78e66b3494d5b9f84fea10d5020858e2b06df3bbf00b93ab9e066b81308d065f",
    "10-wz10-elint-spectrum.svg": "7242ec6709aa01ea1348d249cbdcedcf31f37a3475d9dec5d49238978fda610c",
    "11-datalink-transfer.svg": "9471029823bdaaf13e864d7676f6c81a02500bc74689743ea899d2351d072dbe",
}


def _media_cues() -> list[dict[str, Any]]:
    rows = (
        ("CJR-MEDIA-00", "00-theater-overview.svg", 0, "FIND", "临海任务区初始态势", "外部预采集任务区资料只给出己方部署和待搜索区域，不预设目标结论。", "EXTERNAL-THEATER/THEATER-MAP", "telemetry", "external_precollected"),
        ("CJR-MEDIA-01", "01-satellite-sar-v2.png", 360, "FIND", "卫星 SAR 宽域搜索", "天基 SAR 以近俯视宽域成像形成虚构离岛地表与疑似固定阵地的雷达强度产品，目标类型和敌我属性保持未知。", "SAT-RECON-01/ORBITAL-SAR", "radar", "derived_sensor_product"),
        ("CJR-MEDIA-02", "02-wz10-sar-v2.png", 930, "FIX", "无侦-10 SAR 精细复核", "无侦-10 单机从外海实施侧视条带 SAR 复核，通过金属散射、道路和地形阴影形成可与天基观测关联的当前产品。", "WZ10-01/SAR", "radar", "derived_sensor_product"),
        ("CJR-MEDIA-03", "03-wz10-eo-v2.png", 1500, "TRACK", "无侦-10光电外形复核", "无侦-10在安全距离形成稳定光电帧，记录海岸地形、道路、车辆化阵位外形；目标分类仍由后端依据当前证据完成。", "WZ10-01/EO-IR", "eo_ir", "raw_sensor_frame"),
        ("CJR-MEDIA-10", "10-wz10-elint-spectrum.svg", 1680, "TRACK", "无侦-10被动电子侦察频谱", "无侦-10截获一个中心频率约5.45 GHz的窄带脉冲辐射源并形成频谱、时间和测向数据，等待与SAR及光电观测关联。", "WZ10-01/ELINT", "telemetry", "raw_sensor_frame"),
        ("CJR-MEDIA-04", "04-intelligence-fusion.svg", 2100, "TRACK", "海上指挥节点完成情报融合", "卫星、无侦-10与海上联合指挥舰完成当前观测、航迹及链路状态打包，等待 A1–A6 工作流形成任务建议。", "SEA-C2-01/INTEL-FUSION", "telemetry", "command_product"),
        ("CJR-MEDIA-11", "11-datalink-transfer.svg", 2280, "TRACK", "多源情报数据链传输确认", "海上联合指挥舰完成卫星SAR、无侦-10 SAR/光电/电子侦察数据的时标对齐与校验，并向歼-16和两架攻击无人机分发当前航迹摘要、保护区约束与候选航路。", "SEA-C2-01/DATALINK-TRANSFER", "telemetry", "command_product"),
        ("CJR-MEDIA-05", "05-tactical-command.svg", 2700, "TARGET", "海空战术指挥与火力分配方案", "海上联合指挥舰完成任务级规划，歼-16单机承担空中战术指挥，并分别为攻击无人机01和02分配突入航路。", "J16-01/TACTICAL-C2", "telemetry", "command_product"),
        ("CJR-MEDIA-08", "08-uav01-eo-route.png", 3060, "TARGET", "攻击无人机01北路光电通视确认", "攻击无人机01从东北海面方向回传当前昼间光电帧，只用于确认北路突入通视条件、目标区障碍和安全间隔。", "ATTACK-UAV-01/EO-IR", "eo_ir", "raw_sensor_frame"),
        ("CJR-MEDIA-09", "09-uav02-ir-route.png", 3120, "TARGET", "攻击无人机02南路红外通视确认", "攻击无人机02从南侧航路回传当前白热红外帧，记录完整阵位的热特征并确认南路视线，不在传感器端给出目标结论。", "ATTACK-UAV-02/EO-IR", "eo_ir", "raw_sensor_frame"),
        ("CJR-MEDIA-06", "06-weapon-chain.svg", 3300, "ENGAGE", "海空协同武器链准备状态", "舰载对陆火力、歼-16以及两架攻击无人机的四条独立攻击通道已建立；只有在识别、保护区审查和人工授权满足后才执行模拟攻击。", "SEA-C2-01/WEAPON-CHAIN", "telemetry", "command_product"),
        ("CJR-MEDIA-07", "07-bda-sar-v2.png", 4500, "ASSESS", "攻击后SAR变化检测与毁伤评估", "无侦-10单机重新通过目标区，回传与攻击前同一地理对象的当前SAR帧；后端依据散射变化、结构扰动和实际命中事件评估效果。", "WZ10-01/SAR-BDA", "radar", "raw_sensor_frame"),
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
            capture_id=f"CJR-CAPTURE-{media_id.rsplit('-', 1)[-1]}",
            product_type=product_type,
        )
        for media_id, filename, at_sec, phase, title, caption, sensor_id, modality, product_type in rows
    ]


def _capture_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = [
        sensor_capability("CJR-CAP-EXT", "EXTERNAL-THEATER", "THEATER-MAP", sensor_type="precollected_map", modalities=["telemetry"], source_kind="external_source", parameters={"collection_age_sec": 300, "simulation_only": True}),
        sensor_capability("CJR-CAP-SAT-SAR", "SAT-RECON-01", "ORBITAL-SAR", sensor_type="spaceborne_sar", modalities=["radar"], configured_sensor="ORBITAL-SAR", parameters={"swath_width_km": 120, "ground_sample_distance_m": 3, "source_sensors": ["ORBITAL-SAR"]}),
        sensor_capability("CJR-CAP-WZ-SAR", "WZ10-01", "SAR", sensor_type="airborne_sar", modalities=["radar"], configured_sensor="SAR", parameters={"effective_range_nm": 40, "ground_sample_distance_m": 1.2, "source_sensors": ["SAR"]}),
        sensor_capability("CJR-CAP-WZ-EO", "WZ10-01", "EO-IR", sensor_type="electro_optical", modalities=["eo_ir"], configured_sensor="EO/IR", parameters={"effective_range_nm": 15, "horizontal_fov_deg": 18, "resolution_px": [1672, 941]}),
        sensor_capability("CJR-CAP-WZ-ELINT", "WZ10-01", "ELINT", sensor_type="passive_elint", modalities=["telemetry"], configured_sensor="ELINT", parameters={"effective_range_nm": 40, "frequency_range_mhz": [5200, 5700], "source_sensors": ["ELINT"]}),
        sensor_capability("CJR-CAP-C2-FUSION", "SEA-C2-01", "INTEL-FUSION", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "input_sources": ["SAT-RECON-01", "SAT-RECON-02", "SAT-COM-01", "WZ10-01"]}),
        sensor_capability("CJR-CAP-C2-TRANSFER", "SEA-C2-01", "DATALINK-TRANSFER", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "input_sources": ["SAT-RECON-01", "SAT-RECON-02", "SAT-COM-01", "WZ10-01"], "recipients": ["J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02"]}),
        sensor_capability("CJR-CAP-J16-C2", "J16-01", "TACTICAL-C2", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "tactical_command": True}),
        sensor_capability("CJR-CAP-UAV1-EO", "ATTACK-UAV-01", "EO-IR", sensor_type="electro_optical", modalities=["eo_ir"], configured_sensor="EO/IR", parameters={"effective_range_nm": 12, "horizontal_fov_deg": 24, "resolution_px": [1672, 941]}),
        sensor_capability("CJR-CAP-UAV2-IR", "ATTACK-UAV-02", "EO-IR", sensor_type="infrared", modalities=["eo_ir"], configured_sensor="EO/IR", parameters={"effective_range_nm": 12, "horizontal_fov_deg": 24, "resolution_px": [1672, 941], "polarity": "white_hot"}),
        sensor_capability("CJR-CAP-WEAPON-CHAIN", "SEA-C2-01", "WEAPON-CHAIN", sensor_type="command_status", modalities=["telemetry"], source_kind="command_system", parameters={"current_time_only": True, "simulation_execution_only": True}),
        sensor_capability("CJR-CAP-WZ-BDA", "WZ10-01", "SAR-BDA", sensor_type="airborne_sar", modalities=["radar"], configured_sensor="SAR", parameters={"effective_range_nm": 40, "ground_sample_distance_m": 1.2, "source_sensors": ["SAR"]}),
    ]
    by_id = {row["capability_id"]: row for row in capabilities}
    specs = (
        ("CJR-MEDIA-00", "CJR-TASK-00", "CJR-CAP-EXT", "ingest", 0, 1, []),
        ("CJR-MEDIA-01", "CJR-TASK-01", "CJR-CAP-SAT-SAR", "derive", 300, 420, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-02", "CJR-TASK-02", "CJR-CAP-WZ-SAR", "derive", 840, 960, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-03", "CJR-TASK-03", "CJR-CAP-WZ-EO", "capture", 1440, 1560, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-10", "CJR-TASK-10", "CJR-CAP-WZ-ELINT", "capture", 1620, 1740, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-04", "CJR-TASK-04", "CJR-CAP-C2-FUSION", "command_product", 2040, 2160, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-11", "CJR-TASK-11", "CJR-CAP-C2-TRANSFER", "command_product", 2220, 2340, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-05", "CJR-TASK-05", "CJR-CAP-J16-C2", "command_product", 2640, 2760, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-08", "CJR-TASK-08", "CJR-CAP-UAV1-EO", "capture", 3000, 3090, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-09", "CJR-TASK-09", "CJR-CAP-UAV2-IR", "capture", 3060, 3150, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-06", "CJR-TASK-06", "CJR-CAP-WEAPON-CHAIN", "command_product", 3240, 3360, ["COASTAL-SITE-01"]),
        ("CJR-MEDIA-07", "CJR-TASK-07", "CJR-CAP-WZ-BDA", "derive", 4440, 4560, ["COASTAL-SITE-01"]),
    )
    tasks_by_media = {
        media_id: sensor_task(task_id, by_id[capability_id], task_type=task_type, start_sec=start, end_sec=end, target_refs=targets, branch_ids=["*"])
        for media_id, task_id, capability_id, task_type, start, end, targets in specs
    }
    tasks = list(tasks_by_media.values())
    media_by_id = {row["media_id"]: row for row in _media_cues()}
    parameters = {
        "CJR-MEDIA-00": {"precollected": True, "collection_age_sec": 300, "simulation_only": True},
        "CJR-MEDIA-01": {"renderer_type": "radar_ppi", "swath_width_km": 120, "ground_sample_distance_m": 3, "resolution_px": [1672, 941], "data_source": "sensor_observations"},
        "CJR-MEDIA-02": {"renderer_type": "radar_ppi", "effective_range_nm": 40, "ground_sample_distance_m": 1.2, "registration_group": "CJR-SITE-01", "observation_window_sec": 330, "resolution_px": [1672, 941], "data_source": "sensor_observations"},
        "CJR-MEDIA-03": {"effective_range_nm": 15, "horizontal_fov_deg": 18, "look_angle_deg": 36.46, "point_at_target": True, "resolution_px": [1672, 941]},
        "CJR-MEDIA-04": {"renderer_type": "network_topology", "input_cutoff_sec": 2100, "data_source": "network_and_track_state"},
        "CJR-MEDIA-05": {"renderer_type": "resource_status", "input_cutoff_sec": 2700, "data_source": "task_state"},
        "CJR-MEDIA-06": {"renderer_type": "execution_state", "input_cutoff_sec": 3300, "simulation_execution_only": True, "data_source": "task_state"},
        "CJR-MEDIA-07": {"effective_range_nm": 40, "ground_sample_distance_m": 1.2, "look_angle_deg": 18.97, "point_at_target": True, "registration_group": "CJR-SITE-01", "reference_media_id": "CJR-MEDIA-02", "required_damage_state": "destroyed", "resolution_px": [1672, 941], "data_source": "sensor_observations"},
        "CJR-MEDIA-08": {"effective_range_nm": 12, "horizontal_fov_deg": 24, "look_angle_deg": 6.58, "point_at_target": True, "resolution_px": [1672, 941]},
        "CJR-MEDIA-09": {"effective_range_nm": 12, "horizontal_fov_deg": 24, "look_angle_deg": 6.07, "point_at_target": True, "polarity": "white_hot", "resolution_px": [1672, 941]},
        "CJR-MEDIA-10": {"renderer_type": "elint_spectrum", "effective_range_nm": 40, "frequency_range_mhz": [5200, 5700], "look_angle_deg": 19.55, "point_at_target": True, "data_source": "sensor_observations"},
        "CJR-MEDIA-11": {"renderer_type": "network_topology", "input_cutoff_sec": 2280, "required_source_media_ids": ["CJR-MEDIA-01", "CJR-MEDIA-02", "CJR-MEDIA-03", "CJR-MEDIA-10"], "data_source": "network_and_track_state"},
    }
    captures = []
    for media_id, task in tasks_by_media.items():
        item = media_by_id[media_id]
        captures.append(capture_plan(
            item["capture_id"],
            media_id,
            by_id[task["capability_id"]],
            task,
            product_type=item["product_type"],
            at_sec=item["at_sec"],
            target_refs=task["target_refs"],
            parameters=parameters[media_id],
            branch_ids=item["branch_ids"],
        ))
    return capabilities, tasks, captures


def _timeline() -> list[dict[str, Any]]:
    return [
        {"cue_id": "CJR-CUE-01", "at_sec": 0, "phase": "FIND", "level": "INFO", "title": "海上指挥与无人机保障舰进入任务海域", "description": "SEA-C2-01 抵达集结点；无侦-10与歼-16在空中待命，两架舰载攻击无人机仍在保障舰甲板待命。", "media_ids": ["CJR-MEDIA-00"], "functional_agent_ids": ["A1", "A3"], "model_requirement_ids": ["M17"], "function_ids": []},
        {"cue_id": "CJR-CUE-02", "at_sec": 360, "phase": "FIND", "level": "INFO", "title": "卫星发现疑似沿海固定阵地", "description": "天基 SAR 形成一个待识别地面接触，后端不得依据剧本名称直接推断目标类型。", "media_ids": ["CJR-MEDIA-01"], "functional_agent_ids": ["A1"], "model_requirement_ids": ["M01", "M15", "M17"], "function_ids": ["KC-01", "KC-08"]},
        {"cue_id": "CJR-CUE-03", "at_sec": 930, "phase": "FIX", "level": "INFO", "title": "无侦-10完成 SAR 精细复核", "description": "空基 SAR 与天基提示完成跨源关联，固定目标位置并继续保持身份未知。", "media_ids": ["CJR-MEDIA-02"], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M06", "M15", "M19"], "function_ids": ["KC-04", "KC-05", "KC-08", "KC-09", "KC-10"]},
        {"cue_id": "CJR-CUE-04", "at_sec": 1500, "phase": "TRACK", "level": "WARNING", "title": "阵地外形与辐射源分时复核", "description": "无侦-10先回传当前光电外形帧，再于T+1680秒回传被动电子侦察频谱和测向结果，供后端与SAR证据关联。", "media_ids": ["CJR-MEDIA-03", "CJR-MEDIA-10"], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M05", "M07", "M19", "M20"], "function_ids": ["KC-06", "KC-07", "KC-11", "KC-12", "KC-13", "KC-15", "KC-19"]},
        {"cue_id": "CJR-CUE-04A", "at_sec": 1740, "phase": "TRACK", "level": "INFO", "title": "第二颗低轨卫星接力复访", "description": "侦察卫星02进入后续访问窗口，复访目标区并经通信卫星下传航迹更新；首颗卫星产品仍按时效保留，不因平台离场而删除。", "media_ids": [], "functional_agent_ids": ["A1", "A2"], "model_requirement_ids": ["M15", "M19"], "function_ids": []},
        {"cue_id": "CJR-CUE-05", "at_sec": 2100, "phase": "TRACK", "level": "INFO", "title": "多源情报完成融合与校验传输", "description": "海上联合指挥舰汇集当前观测、航迹、网络和资源状态；T+2280秒形成带时标、校验状态和接收确认的数据链产品，再提交后端工作流。", "media_ids": ["CJR-MEDIA-04", "CJR-MEDIA-11"], "functional_agent_ids": ["A1", "A2", "A3"], "model_requirement_ids": ["M02", "M10", "M11", "M15"], "function_ids": ["KC-14"]},
        {"cue_id": "CJR-CUE-06", "at_sec": 2700, "phase": "TARGET", "level": "WARNING", "title": "歼-16接管空中战术指挥", "description": "海上联合指挥舰向歼-16下达任务级指令；歼-16分别向攻击无人机01和02分配北、南两条低空突入航路。", "media_ids": ["CJR-MEDIA-05"], "functional_agent_ids": ["A3", "A4", "A5"], "model_requirement_ids": ["M04", "M09", "M10", "M11", "M13", "M14"], "function_ids": ["KC-16", "KC-17", "KC-18", "KC-20", "KC-21"]},
        {"cue_id": "CJR-CUE-06A", "at_sec": 3000, "phase": "TARGET", "level": "INFO", "title": "两架攻击无人机由保障舰分路抵达释放阵位", "description": "攻击无人机01、02从 SEA-C2-01 依次起飞，沿北、南两条独立航路抵达释放阵位并保持安全间隔。", "media_ids": [], "functional_agent_ids": ["A3", "A6"], "model_requirement_ids": ["M13"], "function_ids": ["KC-20"]},
        {"cue_id": "CJR-CUE-06B", "at_sec": 3150, "phase": "TARGET", "level": "INFO", "title": "双路无人机回传独立通视画面", "description": "攻击无人机01回传北路昼间光电帧，攻击无人机02回传南路白热红外帧；两路画面只确认当前通视和热特征，不替代后端识别与人工授权。", "media_ids": ["CJR-MEDIA-08", "CJR-MEDIA-09"], "functional_agent_ids": ["A2", "A3", "A6"], "model_requirement_ids": ["M05", "M13", "M20"], "function_ids": ["KC-07", "KC-20"]},
        {"cue_id": "CJR-CUE-07", "at_sec": 3300, "phase": "ENGAGE", "level": "CRITICAL", "title": "四节点武器链建立并等待授权", "description": "舰载巡航导弹、歼-16防区外弹药、攻击无人机01和02的四条独立通道已完成同一到达时刻规划；仅在保护区核验和人工授权后执行。", "media_ids": ["CJR-MEDIA-06"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M03", "M13", "M16"], "function_ids": ["KC-22", "KC-23", "KC-24"]},
        {"cue_id": "CJR-CUE-08", "at_sec": 4200, "phase": "ASSESS", "level": "INFO", "title": "无侦-10实施攻击后复查", "description": "无侦-10保持目标区观测，只有实际命中和当前 SAR/光电证据同时存在时才确认毁伤。", "media_ids": [], "functional_agent_ids": ["A2", "A6"], "model_requirement_ids": ["M18"], "function_ids": ["KC-25", "KC-27"]},
        {"cue_id": "CJR-CUE-09", "at_sec": 4500, "phase": "ASSESS", "level": "INFO", "title": "形成毁伤评估与闭环建议", "description": "后端基于无侦-10的攻击前后变化产品评估效果，并决定结束、补充侦察或重新规划。", "media_ids": ["CJR-MEDIA-07"], "functional_agent_ids": ["A6"], "model_requirement_ids": ["M14", "M18"], "function_ids": ["KC-02", "KC-28"]},
        {"cue_id": "CJR-CUE-10", "at_sec": 4800, "phase": "ASSESS", "level": "INFO", "title": "任务资源按独立返航线撤离", "description": "海上联合指挥舰离开发射阵位，无侦-10完成复查后返航；歼-16和两架攻击无人机已沿各自脱离航线撤出。", "media_ids": [], "functional_agent_ids": ["A3", "A6"], "model_requirement_ids": ["M14"], "function_ids": ["KC-28"]},
    ]


def build_coastal_joint_recon_strike_scenario() -> dict[str, Any]:
    assets = [
        AssetSnapshot("SAT-RECON-01", "space", "低轨SAR侦察卫星01（星下点投影）", "active", 20.12, 121.12, alt_ft=1640420, heading=65, speed_kts=14500, sensors=["ORBITAL-SAR", "ELINT", "SATCOM"], endurance_hr=9999, autonomy_tier=4, health={"battery_pct": 96, "comms_strength": 98}, formation_role="first_orbital_access", network_role="intelligence_source"),
        AssetSnapshot("SAT-RECON-02", "space", "低轨SAR侦察卫星02（接力星下点投影）", "active", 20.04, 121.24, alt_ft=1706037, heading=64, speed_kts=14400, sensors=["ORBITAL-SAR", "ELINT", "SATCOM"], endurance_hr=9999, autonomy_tier=4, health={"battery_pct": 95, "comms_strength": 97}, formation_role="follow_on_orbital_access", network_role="intelligence_source"),
        AssetSnapshot("SAT-COM-01", "space", "地球同步通信中继卫星", "active", 20.49, 122.13, alt_ft=117421260, heading=0, speed_kts=0, sensors=["SATCOM"], endurance_hr=9999, autonomy_tier=3, health={"battery_pct": 98, "comms_strength": 99}, formation_role="persistent_communications_relay", network_role="communications_relay"),
        AssetSnapshot("SEA-C2-01", "maritime", "海上联合指挥与无人机保障舰", "active", 20.19, 122.28, heading=285, speed_kts=14, sensors=["SEA_SURVEILLANCE_RADAR", "C2-FUSION", "SATCOM", "DATALINK"], weapons=["舰载对陆巡航导弹"], endurance_hr=720, autonomy_tier=2, health={"fuel_pct": 93, "comms_strength": 99}, formation_role="maritime_mission_command_uav_support", network_role="command_hub"),
        AssetSnapshot("WZ10-01", "air", "无侦-10侦察机", "active", 20.36, 122.33, alt_ft=24000, heading=270, speed_kts=190, sensors=["SAR", "EO/IR", "ELINT", "SATCOM", "DATALINK"], endurance_hr=12, autonomy_tier=4, health={"fuel_pct": 88, "comms_strength": 96}, formation_role="reconnaissance", network_role="intelligence_relay"),
        AssetSnapshot("J16-01", "air", "歼-16战术指挥打击机", "active", 20.58, 122.53, alt_ft=28000, heading=240, speed_kts=320, sensors=["AESA_RADAR", "EO/IR", "RWR", "DATALINK"], weapons=["空地防区外弹药"], endurance_hr=4, autonomy_tier=3, health={"fuel_pct": 91, "comms_strength": 97}, formation_role="tactical_command_and_strike", network_role="tactical_command"),
        AssetSnapshot("ATTACK-UAV-01", "air", "攻击无人机01", "active", 20.19, 122.28, alt_ft=6000, heading=300, speed_kts=130, sensors=["EO/IR", "DATALINK"], weapons=["无人机协同攻击弹药"], endurance_hr=8, autonomy_tier=5, health={"battery_pct": 94, "comms_strength": 93}, formation_role="north_axis_strike", network_role="weapon_chain_member"),
        AssetSnapshot("ATTACK-UAV-02", "air", "攻击无人机02", "active", 20.19, 122.28, alt_ft=5200, heading=295, speed_kts=130, sensors=["EO/IR", "DATALINK"], weapons=["无人机协同攻击弹药"], endurance_hr=8, autonomy_tier=5, health={"battery_pct": 92, "comms_strength": 92}, formation_role="south_axis_strike", network_role="weapon_chain_member"),
    ]
    threats = [
        ThreatSnapshot("COASTAL-SITE-01", "疑似虚构离岛导弹阵地", "ground", 20.45, 121.98, risk_level="UNKNOWN", rf_freq_mhz=5450, power_dbm=-42, rcs_dbsm=18, ir_signature="medium"),
    ]
    routes = {
        "SAT-RECON-01": [{"lat": 20.30, "lng": 121.55, "label": "GROUND-TRACK-SW"}, {"lat": 20.45, "lng": 121.98, "label": "SAR-SWATH-CENTER"}, {"lat": 20.68, "lng": 122.38, "label": "AO-EXIT-PROJECTION"}],
        "SAT-RECON-02": [{"lat": 20.22, "lng": 121.66, "label": "REVISIT-GROUND-TRACK-SW"}, {"lat": 20.43, "lng": 122.04, "label": "REVISIT-SWATH-CENTER"}, {"lat": 20.72, "lng": 122.46, "label": "REVISIT-AO-EXIT"}],
        "SAT-COM-01": [],
        "SEA-C2-01": [{"lat": 20.21, "lng": 122.30, "label": "C2-PATROL-NE"}, {"lat": 20.24, "lng": 122.28, "label": "C2-PATROL-NW"}, {"lat": 20.21, "lng": 122.24, "label": "C2-PATROL-SW"}, {"lat": 20.17, "lng": 122.25, "label": "C2-PATROL-SE"}],
        "WZ10-01": [{"lat": 20.40, "lng": 122.34, "label": "WZ10-HOLD-EAST"}, {"lat": 20.45, "lng": 122.28, "label": "WZ10-HOLD-NORTH"}, {"lat": 20.38, "lng": 122.23, "label": "WZ10-HOLD-WEST"}, {"lat": 20.31, "lng": 122.29, "label": "WZ10-HOLD-SOUTH"}],
        "J16-01": [{"lat": 20.66, "lng": 122.56, "label": "J16-CAP-EAST"}, {"lat": 20.72, "lng": 122.46, "label": "J16-CAP-NORTH"}, {"lat": 20.63, "lng": 122.37, "label": "J16-CAP-WEST"}, {"lat": 20.53, "lng": 122.45, "label": "J16-CAP-SOUTH"}],
        "ATTACK-UAV-01": [{"lat": 20.13, "lng": 122.34, "label": "UAV01-HOLD-NE"}, {"lat": 20.16, "lng": 122.30, "label": "UAV01-HOLD-NW"}, {"lat": 20.12, "lng": 122.25, "label": "UAV01-HOLD-SW"}, {"lat": 20.08, "lng": 122.27, "label": "UAV01-HOLD-SE"}],
        "ATTACK-UAV-02": [{"lat": 20.07, "lng": 122.41, "label": "UAV02-HOLD-NE"}, {"lat": 20.11, "lng": 122.38, "label": "UAV02-HOLD-NW"}, {"lat": 20.08, "lng": 122.33, "label": "UAV02-HOLD-SW"}, {"lat": 20.04, "lng": 122.36, "label": "UAV02-HOLD-SE"}],
    }
    route_modes = {"SAT-RECON-01": "hold", "SAT-RECON-02": "hold", "SAT-COM-01": "hold", "SEA-C2-01": "loop", "WZ10-01": "loop", "J16-01": "loop", "ATTACK-UAV-01": "loop", "ATTACK-UAV-02": "loop"}
    behavior_phases = {
        "SAT-RECON-01": [
            {"at_sec": 0, "behavior": "awaiting_orbital_access", "label": "等待首个轨道访问窗口", "speed_kts": 0, "status": "staged", "mode": "hold", "route": []},
            {"at_sec": 240, "behavior": "orbital_ground_track_pass", "label": "首颗低轨卫星过境成像", "speed_kts": 14500, "status": "active", "mode": "hold", "route": routes["SAT-RECON-01"]},
            {"at_sec": 600, "behavior": "outside_local_map", "label": "已越出本地视区，星上任务继续", "speed_kts": 14500, "status": "off_station", "mode": "hold", "route": []},
        ],
        "SAT-RECON-02": [
            {"at_sec": 0, "behavior": "awaiting_follow_on_access", "label": "等待接力轨道访问窗口", "speed_kts": 0, "status": "staged", "mode": "hold", "route": []},
            {"at_sec": 1680, "behavior": "follow_on_orbital_pass", "label": "第二颗低轨卫星接力复访", "speed_kts": 14400, "status": "active", "mode": "hold", "route": routes["SAT-RECON-02"]},
            {"at_sec": 2040, "behavior": "outside_local_map", "label": "接力卫星越出本地视区", "speed_kts": 14400, "status": "off_station", "mode": "hold", "route": []},
        ],
        "SAT-COM-01": [
            {"at_sec": 0, "behavior": "persistent_satcom_relay", "label": "持续承担星—舰通信中继", "speed_kts": 0, "status": "active", "mode": "hold", "route": []},
        ],
        "SEA-C2-01": [
            {"at_sec": 0, "behavior": "maritime_command_patrol", "label": "海上指挥区低速巡逻", "speed_kts": 12, "status": "active", "mode": "loop", "route": routes["SEA-C2-01"]},
            {"at_sec": 2400, "behavior": "fire_position_transit", "label": "向舰载火力机动区转场", "speed_kts": 14, "status": "active", "mode": "hold", "route": [{"lat": 20.22, "lng": 122.26, "label": "FIRE-AREA-INGRESS"}, {"lat": 20.24, "lng": 122.25, "label": "SEA-FIRE-POSITION"}]},
            {"at_sec": 2700, "behavior": "fire_area_patrol", "label": "发射阵位低速巡逻", "speed_kts": 8, "status": "active", "mode": "loop", "route": [{"lat": 20.25, "lng": 122.24, "label": "FIRE-PATROL-NORTH"}, {"lat": 20.23, "lng": 122.22, "label": "FIRE-PATROL-WEST"}, {"lat": 20.21, "lng": 122.24, "label": "FIRE-PATROL-SOUTH"}]},
        ],
        "WZ10-01": [
            {"at_sec": 0, "behavior": "airborne_standby_orbit", "label": "侦察待命区标准盘旋", "speed_kts": 175, "status": "active", "mode": "loop", "route": routes["WZ10-01"]},
            {"at_sec": 600, "behavior": "sar_search_pattern", "label": "SAR搜索航线", "speed_kts": 195, "status": "active", "mode": "loop", "route": [{"lat": 20.45, "lng": 122.19, "label": "SAR-INGRESS"}, {"lat": 20.58, "lng": 122.07, "label": "RECON-NORTH"}, {"lat": 20.50, "lng": 121.89, "label": "SAR-WEST"}, {"lat": 20.34, "lng": 121.97, "label": "SAR-SOUTH"}, {"lat": 20.33, "lng": 122.17, "label": "RECON-EGRESS"}]},
            {"at_sec": 1320, "behavior": "eo_elint_standoff_orbit", "label": "光电/电子侦察侧视盘旋", "speed_kts": 165, "status": "active", "mode": "loop", "route": [{"lat": 20.53, "lng": 122.14, "label": "EO-NORTH"}, {"lat": 20.43, "lng": 122.18, "label": "EO-EAST"}, {"lat": 20.34, "lng": 122.10, "label": "ELINT-SOUTH"}, {"lat": 20.39, "lng": 121.99, "label": "ELINT-WEST"}]},
            {"at_sec": 4200, "behavior": "post_strike_bda_pattern", "label": "攻击后SAR复查航线", "speed_kts": 185, "status": "active", "mode": "loop", "route": [{"lat": 20.38, "lng": 122.14, "label": "BDA-EAST"}, {"lat": 20.51, "lng": 122.12, "label": "BDA-NORTH"}, {"lat": 20.57, "lng": 121.98, "label": "BDA-OVERLOOK"}, {"lat": 20.45, "lng": 121.83, "label": "BDA-WEST"}]},
        ],
        "J16-01": [
            {"at_sec": 0, "behavior": "combat_air_patrol", "label": "高空战斗空中巡逻待命", "speed_kts": 300, "status": "active", "mode": "loop", "route": routes["J16-01"]},
            {"at_sec": 2100, "behavior": "airborne_tactical_command", "label": "空中战术指挥盘旋", "speed_kts": 330, "status": "active", "mode": "loop", "route": [{"lat": 20.60, "lng": 122.31, "label": "COMMAND-NORTH"}, {"lat": 20.52, "lng": 122.22, "label": "COMMAND-WEST"}, {"lat": 20.42, "lng": 122.30, "label": "COMMAND-SOUTH"}, {"lat": 20.49, "lng": 122.44, "label": "COMMAND-EAST"}]},
            {"at_sec": 3000, "behavior": "standoff_strike_hold", "label": "防区外攻击等待航线", "speed_kts": 360, "status": "active", "mode": "loop", "route": [{"lat": 20.50, "lng": 122.25, "label": "AIR-RELEASE-LINE"}, {"lat": 20.44, "lng": 122.30, "label": "STRIKE-HOLD-SOUTH"}, {"lat": 20.51, "lng": 122.40, "label": "STRIKE-HOLD-EAST"}]},
        ],
        "ATTACK-UAV-01": [
            {"at_sec": 0, "behavior": "deck_standby", "label": "保障舰甲板待命", "speed_kts": 0, "status": "staged", "mode": "hold", "route": []},
            {"at_sec": 2400, "behavior": "north_axis_ingress", "label": "北路低空突入", "speed_kts": 130, "status": "active", "mode": "hold", "route": [{"lat": 20.19, "lng": 122.23, "label": "NORTH-INGRESS-1"}, {"lat": 20.26, "lng": 122.15, "label": "NORTH-INGRESS-2"}, {"lat": 20.34, "lng": 122.06, "label": "NORTH-RELEASE-STATION"}]},
            {"at_sec": 3000, "behavior": "north_release_station_orbit", "label": "北路释放阵位盘旋", "speed_kts": 95, "status": "active", "mode": "loop", "route": [{"lat": 20.34, "lng": 122.06, "label": "NORTH-RELEASE-STATION"}, {"lat": 20.36, "lng": 122.09, "label": "NORTH-HOLD-NE"}, {"lat": 20.32, "lng": 122.11, "label": "NORTH-HOLD-SE"}, {"lat": 20.30, "lng": 122.07, "label": "NORTH-HOLD-SW"}]},
        ],
        "ATTACK-UAV-02": [
            {"at_sec": 0, "behavior": "deck_standby", "label": "保障舰甲板待命", "speed_kts": 0, "status": "staged", "mode": "hold", "route": []},
            {"at_sec": 2460, "behavior": "south_axis_ingress", "label": "南路低空突入", "speed_kts": 130, "status": "active", "mode": "hold", "route": [{"lat": 20.14, "lng": 122.28, "label": "SOUTH-INGRESS-1"}, {"lat": 20.27, "lng": 122.14, "label": "SOUTH-INGRESS-2"}, {"lat": 20.38, "lng": 122.09, "label": "SOUTH-RELEASE-STATION"}]},
            {"at_sec": 3000, "behavior": "south_release_station_orbit", "label": "南路释放阵位盘旋", "speed_kts": 95, "status": "active", "mode": "loop", "route": [{"lat": 20.38, "lng": 122.09, "label": "SOUTH-RELEASE-STATION"}, {"lat": 20.40, "lng": 122.12, "label": "SOUTH-HOLD-NE"}, {"lat": 20.36, "lng": 122.14, "label": "SOUTH-HOLD-SOUTH"}, {"lat": 20.34, "lng": 122.10, "label": "SOUTH-HOLD-SW"}]},
        ],
    }
    extra_devices = [
        {"device_id": "SEA-LACM-WEAPON-01", "name": "舰载对陆巡航导弹", "device_type": "ship_launched_land_attack_weapon", "status": "stowed", "asset_ref": "SEA-C2-01"},
        {"device_id": "J16-STRIKE-WEAPON-01", "name": "歼-16空地防区外弹药", "device_type": "air_to_ground_weapon", "status": "stowed", "asset_ref": "J16-01"},
        {"device_id": "UAV-WEAPON-01", "name": "攻击无人机01协同弹药", "device_type": "uav_weapon", "status": "stowed", "asset_ref": "ATTACK-UAV-01"},
        {"device_id": "UAV-WEAPON-02", "name": "攻击无人机02协同弹药", "device_type": "uav_weapon", "status": "stowed", "asset_ref": "ATTACK-UAV-02"},
    ]
    compute_nodes = [
        compute_node("SAT-COMPUTE", "卫星任务处理节点", "SAT-RECON-01", host_device_type="spacecraft", compute_type="space_edge", cpu="16 cores", accelerator="sar_processor", memory_gb=64, network="satcom"),
        compute_node("SAT-COMPUTE-02", "接力卫星任务处理节点", "SAT-RECON-02", host_device_type="spacecraft", compute_type="space_edge", cpu="16 cores", accelerator="sar_processor", memory_gb=64, network="satcom"),
        compute_node("SAT-RELAY-COMPUTE", "通信中继卫星路由节点", "SAT-COM-01", host_device_type="spacecraft", compute_type="space_relay", cpu="8 cores", accelerator="link_processor", memory_gb=32, network="satcom"),
        compute_node("C2-COMPUTE", "海上联合指挥舰融合节点", "SEA-C2-01", host_device_type="command_ship", compute_type="maritime_command", cpu="64 cores", accelerator="server_gpu", memory_gb=256, network="multi_domain_fabric"),
        compute_node("WZ10-COMPUTE", "无侦-10边缘处理节点", "WZ10-01", host_device_type="recon_aircraft", compute_type="air_edge", cpu="24 cores", accelerator="radar_dsp", memory_gb=96, network="tactical_air_link"),
        compute_node("J16-COMPUTE", "歼-16战术指挥节点", "J16-01", host_device_type="fighter", compute_type="airborne_command", cpu="24 cores", accelerator="mission_gpu", memory_gb=96, network="tactical_air_link"),
        compute_node("UAV1-COMPUTE", "攻击无人机01协同节点", "ATTACK-UAV-01", host_device_type="attack_uav", compute_type="air_edge", cpu="6 cores", accelerator="embedded_ai", memory_gb=16, network="tactical_air_link"),
        compute_node("UAV2-COMPUTE", "攻击无人机02协同节点", "ATTACK-UAV-02", host_device_type="attack_uav", compute_type="air_edge", cpu="6 cores", accelerator="embedded_ai", memory_gb=16, network="tactical_air_link"),
        compute_node("SEA-WEAPON-COMPUTE", "舰载巡航导弹制导节点", "SEA-LACM-WEAPON-01", host_device_type="ship_launched_land_attack_weapon", compute_type="onboard_guidance", status="standby", cpu="4 cores", accelerator="signal_processor", memory_gb=8, network="weapon_datalink"),
        compute_node("J16-WEAPON-COMPUTE", "歼-16弹载制导节点", "J16-STRIKE-WEAPON-01", host_device_type="air_to_ground_weapon", compute_type="onboard_guidance", status="standby", cpu="4 cores", accelerator="signal_processor", memory_gb=8, network="weapon_datalink"),
        compute_node("UAV1-WEAPON-COMPUTE", "攻击无人机01载荷制导节点", "UAV-WEAPON-01", host_device_type="uav_weapon", compute_type="onboard_guidance", status="standby", cpu="4 cores", accelerator="embedded_ai", memory_gb=8, network="weapon_datalink"),
        compute_node("UAV2-WEAPON-COMPUTE", "攻击无人机02载荷制导节点", "UAV-WEAPON-02", host_device_type="uav_weapon", compute_type="onboard_guidance", status="standby", cpu="4 cores", accelerator="embedded_ai", memory_gb=8, network="weapon_datalink"),
    ]
    agent_deployments = [
        agent_deployment("A1", "SAT-COMPUTE", roles=["wide_area_detection", "satellite_intelligence_ingest"]),
        agent_deployment("A1", "SAT-COMPUTE-02", roles=["follow_on_revisit", "track_freshness_update"]),
        agent_deployment("A3", "SAT-RELAY-COMPUTE", roles=["persistent_satcom_relay", "store_and_forward"]),
        agent_deployment("A1", "WZ10-COMPUTE", roles=["sar_eo_fusion", "intelligence_sharing"]),
        agent_deployment("A1", "C2-COMPUTE", roles=["multi_source_perception", "tactical_intelligence_fusion"]),
        agent_deployment("A2", "WZ10-COMPUTE", roles=["ground_track_update", "target_validation"]),
        agent_deployment("A2", "J16-COMPUTE", roles=["threat_assessment", "track_maintenance"]),
        agent_deployment("A3", "C2-COMPUTE", roles=["mission_decomposition", "resource_allocation"]),
        agent_deployment("A3", "J16-COMPUTE", roles=["uav_tasking", "tactical_rescheduling"]),
        agent_deployment("A4", "C2-COMPUTE", roles=["candidate_plan_generation", "mission_decision"]),
        agent_deployment("A4", "J16-COMPUTE", roles=["tactical_attack_option_selection"]),
        agent_deployment("A5", "C2-COMPUTE", roles=["roe_review", "protected_site_check"]),
        agent_deployment("A6", "J16-COMPUTE", roles=["coordinated_fire_command", "weapon_monitor"]),
        agent_deployment("A6", "UAV1-COMPUTE", roles=["north_axis_execution", "cooperative_guidance"]),
        agent_deployment("A6", "UAV2-COMPUTE", roles=["south_axis_execution", "cooperative_guidance"]),
        agent_deployment("A6", "WZ10-COMPUTE", roles=["post_strike_observation", "damage_assessment"]),
        agent_deployment("A6", "SEA-WEAPON-COMPUTE", roles=["terrain_contour_guidance"], runtime_status="standby"),
        agent_deployment("A6", "J16-WEAPON-COMPUTE", roles=["terminal_guidance"], runtime_status="standby"),
        agent_deployment("A6", "UAV1-WEAPON-COMPUTE", roles=["individual_terminal_guidance"], runtime_status="standby"),
        agent_deployment("A6", "UAV2-WEAPON-COMPUTE", roles=["individual_terminal_guidance"], runtime_status="standby"),
    ]
    asset_capabilities, asset_task_schedule, capture_plans = _capture_contract()
    timeline = _timeline()
    return {
        "schema_version": "amos.scenario.v2",
        "id": SCENARIO_ID,
        "name": "临海多域协同侦察与精确打击",
        "operator_brief": "1艘海上联合指挥舰进入巴士海峡虚构任务海域后，低轨侦察卫星01在首个访问窗口形成待识别离岛接触，侦察卫星02在后续窗口接力复访，通信中继卫星持续保障星—舰链路；已经下传的情报产品在有效期内继续参与融合，不因卫星离开地图而消失。1架无侦-10按SAR、光电和电子侦察顺序复核；1架歼-16承担空中战术指挥和防区外打击；攻击无人机01、02沿北、南两条独立航路抵达释放阵位。后端完成识别与保护区审查、操作员授权后，四个火力节点按同一到达时刻实施模拟攻击，随后歼-16与两架无人机分路撤离，由无侦-10单机完成毁伤复查。",
        "description": "验证两颗低轨侦察卫星接力过境、通信卫星持续中继、情报产品时效管理、单机侦察、海上任务指挥、空中战术指挥、四节点协同武器链、人工授权、分路撤离和毁伤评估闭环。",
        "scenario_type": "scripted_agent_demo",
        "theater": {"theater_id": "bashi_channel_adjacent_joint_training", "name": "台湾南部—菲律宾北部毗邻海域虚构联合训练区", "location_profile": "fictional_training_area", "center": {"lat": 20.49, "lng": 122.13}, "zoom": 8, "ao": {"north": 21.06, "south": 20.01, "east": 122.93, "west": 121.07}},
        "map_display": {"relief_manifest": "/static/assets/maps/taiwan-se-relief/manifest.json", "default_layers": {"sensors": False, "ao": True, "coordination": False}, "track_style": "tactical_joint", "base_surface": "coastal", "focus_bounds": {"north": 22.10, "south": 19.70, "east": 122.80, "west": 120.80}, "exclude_domains_from_focus": ["space"], "space_node_asset_ids": ["SAT-COM-01"], "space_visual_speed_factor": 0.035, "space_ground_tracks": [{"asset_id": "SAT-RECON-01", "label": "侦察卫星01预测星下轨迹", "access_start_sec": 240, "access_end_sec": 600, "color": "#9eb2c8", "points": [{"lat": 20.12, "lng": 121.12}, {"lat": 20.30, "lng": 121.55}, {"lat": 20.45, "lng": 121.98}, {"lat": 20.68, "lng": 122.38}]}, {"asset_id": "SAT-RECON-02", "label": "侦察卫星02接力星下轨迹", "access_start_sec": 1680, "access_end_sec": 2040, "color": "#9da7bf", "points": [{"lat": 20.04, "lng": 121.24}, {"lat": 20.22, "lng": 121.66}, {"lat": 20.43, "lng": 122.04}, {"lat": 20.72, "lng": 122.46}]}], "trail_window_sec": 480, "track_trail_window_sec": 600, "label_asset_ids": ["SEA-C2-01", "WZ10-01", "J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02"], "trail_asset_ids": ["WZ10-01", "J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02"]},
        "space_operations": {
            "title": "空天支援 · 双星接力",
            "relay": {"asset_id": "SAT-COM-01", "label": "通信中继在线"},
            "passes": [
                {"asset_id": "SAT-RECON-01", "label": "侦察星01 · 首次SAR访问", "access_start_sec": 240, "capture_sec": 360, "access_end_sec": 600},
                {"asset_id": "SAT-RECON-02", "label": "侦察星02 · 接力复访", "access_start_sec": 1680, "capture_sec": 1740, "access_end_sec": 2040},
            ],
            "intelligence_products": [
                {"product_id": "CJR-MEDIA-01", "label": "首次SAR宽域线索", "source_asset_id": "SAT-RECON-01", "captured_at_sec": 360, "received_at_sec": 390, "valid_until_sec": 1740},
                {"product_id": "CJR-ORBITAL-REVISIT-01", "label": "接力复访航迹更新", "source_asset_id": "SAT-RECON-02", "captured_at_sec": 1740, "received_at_sec": 1800, "valid_until_sec": 2700},
            ],
            "note": "卫星离场不删除已下传产品；超过有效期后由无侦-10当前观测接续。",
        },
        "environment": {"weather": "partly_cloudy", "visibility_nm": 24, "wind_speed_kts": 12, "wind_direction_deg": 70, "sea_state": 2, "precipitation": "none", "intermittent_jamming": True, "terrain_profile": "coastal_hills", "civilian_exclusion_zone": "SIM-PROTECTED-COAST-01"},
        "asset_routes": routes,
        "asset_route_modes": route_modes,
        "asset_behavior_phases": behavior_phases,
        "asset_motion_windows": {
            "SAT-RECON-01": {"start_sec": 240},
            "SAT-RECON-02": {"start_sec": 1680},
            "ATTACK-UAV-01": {"start_sec": 2400, "launch_from_asset": "SEA-C2-01", "align_route_heading": True},
            "ATTACK-UAV-02": {"start_sec": 2460, "launch_from_asset": "SEA-C2-01", "align_route_heading": True},
        },
        "asset_visibility_windows": {
            "SAT-RECON-01": {"visible_from_sec": 240, "visible_until_sec": 600},
            "SAT-RECON-02": {"visible_from_sec": 1680, "visible_until_sec": 2040},
            "ATTACK-UAV-01": {"visible_from_sec": 2400},
            "ATTACK-UAV-02": {"visible_from_sec": 2460},
        },
        "asset_profiles": asset_profiles(assets),
        "physical_devices": physical_devices(assets, extra_devices),
        "compute_nodes": compute_nodes,
        "agent_deployments": agent_deployments,
        "asset_capabilities": asset_capabilities,
        "asset_task_schedule": asset_task_schedule,
        "capture_plans": capture_plans,
        "evidence_classification_rules": [
            {
                "rule_id": "CJR-ID-COASTAL-SITE",
                "target_ref": "COASTAL-SITE-01",
                "required_media_ids": ["CJR-MEDIA-03", "CJR-MEDIA-10"],
                "classification": "COASTAL_MISSILE_SITE",
                "retain_until_sec": 5100,
            },
        ],
        "threat_observation_windows": {"COASTAL-SITE-01": {"start_sec": 300}},
        "assets": assets,
        "threats": threats,
        "protected_assets": [{"asset_id": "PROTECTED-COASTAL-FACILITY-01", "asset_name": "虚构离岛民用设施保护区", "asset_type": "civilian_facility", "lat": 20.38, "lon": 121.92, "alt": 0, "protection_radius_m": 3500, "criticality": 0.88, "status": "protected", "metadata": {"simulation_only": True, "location_profile": "fictional_training_area"}}],
        "coordination_links": [
            {"link_id": "CJR-LINK-SAT1-RELAY", "link_type": "intelligence", "source_asset_id": "SAT-RECON-01", "target_asset_id": "SAT-COM-01", "active_from_sec": 240, "label": "侦察星01过境下传"},
            {"link_id": "CJR-LINK-SAT2-RELAY", "link_type": "intelligence", "source_asset_id": "SAT-RECON-02", "target_asset_id": "SAT-COM-01", "active_from_sec": 1680, "label": "侦察星02接力下传"},
            {"link_id": "CJR-LINK-RELAY-C2", "link_type": "intelligence", "source_asset_id": "SAT-COM-01", "target_asset_id": "SEA-C2-01", "active_from_sec": 240, "label": "通信卫星持续星—舰中继"},
            {"link_id": "CJR-LINK-WZ-C2", "link_type": "intelligence", "source_asset_id": "WZ10-01", "target_asset_id": "SEA-C2-01", "active_from_sec": 900, "label": "无侦-10情报共享"},
            {"link_id": "CJR-LINK-C2-J16", "link_type": "command", "source_asset_id": "SEA-C2-01", "target_asset_id": "J16-01", "active_from_sec": 2100, "label": "海上任务级指挥"},
            {"link_id": "CJR-LINK-J16-UAV1", "link_type": "command", "source_asset_id": "J16-01", "target_asset_id": "ATTACK-UAV-01", "active_from_sec": 2700, "label": "北路战术指令"},
            {"link_id": "CJR-LINK-J16-UAV2", "link_type": "command", "source_asset_id": "J16-01", "target_asset_id": "ATTACK-UAV-02", "active_from_sec": 2700, "label": "南路战术指令"},
            {"link_id": "CJR-LINK-SEA-TARGET", "link_type": "weapon", "source_asset_id": "SEA-C2-01", "target_ref": "COASTAL-SITE-01", "active_from_sec": 2700, "label": "舰载对陆火力通道"},
            {"link_id": "CJR-LINK-J16-TARGET", "link_type": "weapon", "source_asset_id": "J16-01", "target_ref": "COASTAL-SITE-01", "active_from_sec": 2700, "label": "歼-16防区外攻击通道"},
            {"link_id": "CJR-LINK-UAV1-TARGET", "link_type": "weapon", "source_asset_id": "ATTACK-UAV-01", "target_ref": "COASTAL-SITE-01", "active_from_sec": 3000, "label": "无人机01北路攻击通道"},
            {"link_id": "CJR-LINK-UAV2-TARGET", "link_type": "weapon", "source_asset_id": "ATTACK-UAV-02", "target_ref": "COASTAL-SITE-01", "active_from_sec": 3000, "label": "无人机02南路攻击通道"},
        ],
        "timeline": timeline,
        "media_cues": bind_media_consumers(_media_cues(), timeline),
        "cover_media_id": "CJR-MEDIA-00",
        "demo_controls": {"recommended_speed": 8, "duration_sec": 5100, "auto_agent_interval_sec": 600, "show_truth": False, "latest_visual_only": True, "auto_stop": True, "advance_while_analyzing": True},
        "default_seed": 61023,
        "supported_modes": ["integration", "demonstration"],
        "functional_agents": scenario_agents(),
        "required_agents": required_backend_roles(),
        "algorithm_coverage": planned_algorithms("clustering", "association", "linear_regression", "logistic_regression", "random_forest", "neural_network", "naive_bayes_network", "large_language_model", "retrieval_augmented_generation", "agent_collaboration", "federated_learning", "reinforcement_learning", "explainable_ai", "multimodal_fusion", "time_series_prediction", "real_time_object_detection", "change_detection", "multi_target_tracking", "graph_neural_network"),
        "function_point_coverage": planned_function_points(*[f"KC-{index:02d}" for index in range(1, 29)]),
        "conditional_function_points": ["KC-03", "KC-26"],
        "function_runtime_triggers": {
            "KC-22": {"event": "authorized_fire_command", "offset_sec": 0},
            "KC-23": {"event": "authorized_fire_command", "offset_sec": 5},
            "KC-24": {"event": "authorized_fire_command", "offset_sec": 15},
            "KC-25": {"event": "weapon_hit", "offset_sec": 0},
            "KC-02": {"event": "weapon_hit", "offset_sec": 15},
            "KC-27": {"event": "weapon_hit", "offset_sec": 30},
            "KC-28": {"event": "damage_assessment_confirmed", "offset_sec": 0},
        },
        "demo_checkpoints": [
            {"checkpoint_id": "CJR-CP-CUE", "title": "天基提示与空基复核输入就绪", "min_elapsed_sec": 930, "conditions": {"stable_track_count_at_least": 1, "minimum_track_confidence": 0.5, "minimum_track_samples": 2, "media_ids_released": ["CJR-MEDIA-01", "CJR-MEDIA-02"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "CJR-CP-IDENTIFY", "title": "地面目标多模态识别输入就绪", "min_elapsed_sec": 1530, "conditions": {"stable_track_count_at_least": 1, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["CJR-MEDIA-03"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "CJR-CP-FUSION", "title": "情报融合与共享输入就绪", "min_elapsed_sec": 2130, "conditions": {"stable_track_count_at_least": 1, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["CJR-MEDIA-03", "CJR-MEDIA-10"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "CJR-CP-PLAN", "title": "战术指挥与协同方案输入就绪", "min_elapsed_sec": 2730, "conditions": {"stable_track_count_at_least": 1, "minimum_track_confidence": 0.55, "minimum_track_samples": 2, "media_ids_released": ["CJR-MEDIA-03", "CJR-MEDIA-10"]}, "pause": True, "submit_analysis": True},
            {"checkpoint_id": "CJR-CP-ENGAGE", "title": "协同武器链等待明确授权", "min_elapsed_sec": 3330, "conditions": {"media_ids_released": ["CJR-MEDIA-06", "CJR-MEDIA-08", "CJR-MEDIA-09"]}, "pause": True, "submit_analysis": True, "requires_operator_action": True},
            {"checkpoint_id": "CJR-CP-CLOSE", "title": "毁伤评估与闭环建议就绪", "min_elapsed_sec": 4530, "conditions": {"media_ids_released": ["CJR-MEDIA-07"]}, "pause": True, "submit_analysis": True, "requires_operator_action": True},
        ],
        "fault_injections": [
            {"fault_id": "CJR-FAULT-LINK", "type": "communication_degradation", "target": "J16-01", "at_checkpoint": "CJR-CP-PLAN", "status": "available"},
            {"fault_id": "CJR-FAULT-RECON", "type": "resource_unavailable", "target": "WZ10-01", "at_checkpoint": "CJR-CP-IDENTIFY", "status": "available"},
        ],
        "expected_branches": [
            {"branch_id": "standard", "name": "标准协同链", "description": "完成天基提示、空基复核、分级指挥、协同攻击和毁伤评估。"},
            {"branch_id": "communication_degraded", "name": "战术链路降级", "description": "注入歼-16战术链路质量下降，检验后端重路由和重规划。"},
            {"branch_id": "resource_unavailable", "name": "侦察资源不可用", "description": "无侦-10不可用时检验卫星与其他资源的补充调度。"},
            {"branch_id": "behavior_changed", "name": "目标状态变化", "description": "目标辐射状态变化后触发重新评估。"},
            {"branch_id": "compliance_rejected", "name": "合规审查拒绝", "description": "保护区约束不满足时禁止攻击并返回补充规划。"},
        ],
        "default_branch": "standard",
        "acceptance_profile": {"functional_agents": 6, "core_algorithms": 15, "engineering_models": 4, "function_points": 28, "evidence_policy": "backend_trace_only", "required_target_count": 1, "requires_explicit_fire_authorization": True, "requires_coordination_chain": True, "requires_damage_assessment": True},
        "engagement_policy": {
            "decision_authority": "operator",
            "requires_backend_identification": True,
            "requires_explicit_authorization": True,
            "requires_prior_warning": False,
            "minimum_threat_levels": ["HIGH", "CRITICAL"],
            "eligible_kill_chain_phases": ["TARGET", "ENGAGE"],
            "authorized_asset_ids": ["SEA-C2-01", "J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02"],
            "authorized_weapons": ["舰载对陆巡航导弹", "空地防区外弹药", "无人机协同攻击弹药"],
            "protected_classifications": ["CIVILIAN", "HOSPITAL", "SCHOOL", "RESIDENTIAL", "FRIENDLY"],
            "protected_truth_ids": [],
            "protected_asset_buffer_nm": 3.0,
            "max_salvos_per_track": 1,
            "bda_not_before_sec": 4440,
            "bda_observer_asset_ids": ["WZ10-01"],
            "stochastic_damage_assessment": True,
            "action_label": "指挥舰 + 歼-16 + 攻击无人机01/02四节点协同武器链",
            "authorization_message": "是否授权海上联合指挥舰、歼-16单机、攻击无人机01和02组成四节点武器链，对确认的陆上导弹阵地实施同一到达时刻协同打击？",
            "post_launch_routes": {
                "J16-01": [
                    {"lat": 20.65, "lng": 122.55, "label": "J16-EGRESS"},
                    {"lat": 20.82, "lng": 122.70, "label": "J16-RTB"},
                ],
                "ATTACK-UAV-01": [
                    {"lat": 20.20, "lng": 122.25, "label": "UAV01-EGRESS"},
                    {"lat": 20.05, "lng": 122.42, "label": "UAV01-RECOVERY"},
                ],
                "ATTACK-UAV-02": [
                    {"lat": 20.10, "lng": 122.15, "label": "UAV02-EGRESS"},
                    {"lat": 20.04, "lng": 122.28, "label": "UAV02-RECOVERY"},
                ],
            },
            "post_launch_behaviors": {
                "J16-01": {"behavior": "post_launch_egress", "label": "发射后高速脱离返航", "speed_kts": 480},
                "ATTACK-UAV-01": {"behavior": "north_axis_egress", "label": "北路攻击后独立脱离", "speed_kts": 130},
                "ATTACK-UAV-02": {"behavior": "south_axis_egress", "label": "南路攻击后独立脱离", "speed_kts": 130},
            },
            "post_bda_routes": {
                "WZ10-01": [
                    {"lat": 20.55, "lng": 122.35, "label": "WZ10-EGRESS"},
                    {"lat": 20.65, "lng": 122.55, "label": "WZ10-RTB"},
                ],
                "SEA-C2-01": [
                    {"lat": 20.20, "lng": 122.40, "label": "C2-DEPART-FIRE-POSITION"},
                    {"lat": 20.10, "lng": 122.55, "label": "C2-RECOVERY"},
                ],
            },
            "post_bda_behaviors": {
                "WZ10-01": {"behavior": "reconnaissance_return", "label": "完成毁伤复查后返航", "speed_kts": 190},
                "SEA-C2-01": {"behavior": "command_ship_recovery", "label": "离开发射阵位并撤收", "speed_kts": 14},
            },
            "coordinated_engagement": {
                "chain_id": "CJR-WEAPON-CHAIN-01",
                "coordination_mode": "time_on_target",
                "arrival_tolerance_sec": 20,
                "participants": [
                    {"asset_id": "SEA-C2-01", "weapon_name": "舰载对陆巡航导弹", "role": "maritime_strike_lead"},
                    {"asset_id": "J16-01", "weapon_name": "空地防区外弹药", "role": "air_tactical_command_and_strike"},
                    {"asset_id": "ATTACK-UAV-01", "weapon_name": "无人机协同攻击弹药", "role": "north_axis_strike"},
                    {"asset_id": "ATTACK-UAV-02", "weapon_name": "无人机协同攻击弹药", "role": "south_axis_strike"},
                ],
            },
        },
        "agent_plan": {
            "mode": "commander_workflow",
            "steps": ["submit_current_snapshot", "execute_a1_a6_workflow", "project_run_scoped_evidence", "review_coordination_chain", "request_operator_fire_authorization", "execute_coordinated_fire_command", "assess_effects"],
            "commander_options": {
                "mission_type": "coastal_joint_reconnaissance_and_strike",
                "task_goal": "依据当前卫星、无侦-10和融合航迹证据识别陆上接触，评估保护区约束，为1艘海上联合指挥舰、1架歼-16及攻击无人机01、02生成可审计的对陆协同任务建议；不得绕过操作员授权。",
                "analysis_guidance": "仅使用当前已释放的卫星、SAR、光电、电子侦察、航迹和网络证据；先完成陆上接触分类与保护区校验，再为指挥舰、歼-16和两架独立编号无人机生成由海向陆的四条火力通道、同一到达时刻及各自脱离航线建议，不得把计划当作已执行结果。",
                "knowledge_base": [
                    "所有目标属性必须来自当前已释放的可观测证据和后端算法输出。",
                    "海上联合指挥舰承担任务级指挥和舰载对陆主火力；歼-16长机承担空中战术指挥及防区外打击角色。",
                    "攻击无人机01和02分别执行北、南突入，不得合并为抽象群体；两机只能在目标确认、保护区审查和操作员授权后进入执行状态。",
                    "四个火力节点必须按同一到达时刻协调，任一节点超出射程或到达时差超限均不得执行。",
                    "发射后歼-16、攻击无人机01和02立即执行各自脱离航线；毁伤评估完成后无侦-10与指挥舰分别返航和撤离。",
                ],
            },
        },
        "events": [row["title"] for row in timeline],
    }


__all__ = ["SCENARIO_ID", "build_coastal_joint_recon_strike_scenario"]
