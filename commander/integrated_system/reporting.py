from __future__ import annotations

from typing import Any, Dict, List

from integrated_system.capability_map import CAPABILITY_SEQUENCE


CAPABILITY_LABELS = {
    "cognition": "认知识别",
    "tracking": "航迹跟踪",
    "threat_assessment": "威胁评估",
    "decision_planning": "方案规划",
    "compliance_authorization": "合规授权",
    "execution_control": "执行控制",
    "effect_evaluation": "效果评估",
}

EVENT_LABELS = {
    "mission_started": "任务开始",
    "workflow_cycle_started": "流程轮次开始",
    "capability_started": "能力开始执行",
    "capability_completed": "能力执行完成",
    "mission_success_threshold_met": "达到成功阈值",
    "mission_success_threshold_met_after_replan": "重规划后达到成功阈值",
    "mission_replan_requested": "触发重规划",
    "mission_replan_budget_exhausted": "重规划预算耗尽",
    "operator_adjustment_received": "收到人工调整",
    "operator_adjustment_applied": "人工调整已生效",
    "mission_paused": "任务暂停",
    "mission_waiting_for_resume": "任务等待恢复",
    "mission_resumed": "任务恢复",
    "mission_aborted": "任务终止",
    "mission_failed": "任务失败",
    "branch_capability_fallback": "分支能力失败，回退到演示逻辑",
}

OBJECT_TYPE_LABELS = {
    "ship": "水面目标",
    "uav": "无人机",
    "aircraft": "空中目标",
    "unknown": "不明目标",
}

GROUP_TYPE_LABELS = {
    "air_formation": "空中编组",
    "surface_group": "水面编组",
    "mixed_group": "混合编组",
    "unknown_group": "未知编组",
}

PLAN_NAME_LABELS = {
    "Priority monitoring and reassessment": "重点目标优先监视",
    "Broad surveillance coverage": "广域持续覆盖监视",
    "Resource-sparing watch": "节约资源持续监视",
}

PLAN_SUMMARY_LABELS = {
    "PLAN-PRIORITY-MONITOR": "把资源优先压到最高优先级目标，先盯住最危险对象，再滚动复核。",
    "PLAN-BROAD-SURVEILLANCE": "把资源铺开到全部目标，优先保证覆盖面和连续观测。",
    "PLAN-RESOURCE-SPARING": "只保留最低必要监视，尽量节约资源，等待后续再分配。",
}

ACTION_TRANSLATIONS = {
    "focus available resources on highest-priority targets": "把可用资源优先压到最高优先级目标",
    "increase observation cadence for selected targets": "提高重点目标的观察频率",
    "reassess risk ranking after the next review window": "在下一轮复核窗口后重新评估威胁排序",
    "spread available resources across all scheduled targets": "把可用资源分散到全部已排任务目标",
    "maintain broad-area monitoring continuity": "保持广域监视连续性",
    "defer prioritization changes until updated risk evidence arrives": "在拿到新威胁证据前暂缓大幅调整优先级",
    "monitor only the top-priority target with minimum viable resources": "只用最低必要资源监视头号目标",
    "hold remaining resources for follow-up tasking": "保留其余资源，等待后续再指派",
    "escalate to broader coverage if risk increases": "如果风险继续上升，再扩大覆盖范围",
}

EFFECT_TRANSLATIONS = {
    "improves confidence on the highest-risk items": "提升对最高风险对象的识别把握度",
    "keeps the plan in decision-support mode": "保持方案处于仿真决策支持边界内",
    "maximizes target coverage": "尽量扩大对目标的覆盖面",
    "reduces chance of losing lower-priority targets": "降低对低优先级目标失跟的概率",
    "preserves resource availability": "保留后续机动与分配余量",
    "accepts reduced coverage for lower-priority targets": "接受对低优先级目标覆盖下降的代价",
}

ASSUMPTION_TRANSLATIONS = {
    "highest-risk targets should receive first attention": "默认最高风险目标优先获得资源关注",
    "coverage is preferred over concentrated monitoring": "默认当前更强调覆盖面，而不是集中盯单一目标",
    "resource conservation is valuable in the current window": "默认当前窗口应优先节约资源并保留后续余量",
}

COMPLIANCE_CONCLUSION_MAP = {
    "RULE-SIM-001": "当前方案还缺少“仅用于仿真/决策支持”的边界说明，建议补一句安全边界声明。",
    "LOW-AUTH-SCOPE-001": "当前审批范围写得偏笼统，建议把规则审查与授权范围补充清楚。",
    "AUTH-STATE-APPROVED": "虽然系统里已有 approved 状态，但还没有明确绑定到这套具体方案，仍建议人工确认。",
}

EXECUTION_ACTION_LABELS = {
    "re_attack": "对高优先级目标再次模拟压制",
    "reallocate_sensor": "重新分配传感器观察资源",
    "coordinated_suppression": "组织多平台协同模拟压制",
    "confirm_effect_and_shift": "确认效果后转入下一目标",
    "continue_tracking": "保持持续跟踪",
}

ASSESSMENT_LABELS = {
    "mission_effective": "当前闭环效果达到任务阈值",
    "mission_requires_replan": "当前闭环效果不足，需继续重规划",
}

DEFAULT_COGNITION_ALGORITHMS = [
    "RT-DETR+ODConv detection",
    "Siamese Mask2Former damage reasoning",
    "EDL evidential confidence estimation",
    "MOTR + neural Kalman motion continuation",
    "ImageBind cross-modal embedding",
    "Multimodal Mamba cognition fusion",
    "SupCon + meta-learning target characterization",
    "SynapseRAG knowledge retrieval",
    "knowledge-semantic communication routing",
]


def _value_or(value: Any, fallback: Any) -> Any:
    return fallback if value in (None, "") else value


def _nested(source: Dict[str, Any], path: List[str], fallback: Any) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return fallback
        current = current.get(key)
        if current is None:
            return fallback
    return current


def _short_text(text: Any, limit: int = 120) -> str:
    value = str(_value_or(text, ""))
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _contact_lookup(mission_input: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("contact_id")): item
        for item in mission_input.get("contacts", [])
        if item.get("contact_id")
    }


def _contact_name(contact_id: str, contacts_by_id: Dict[str, Dict[str, Any]]) -> str:
    contact = contacts_by_id.get(contact_id, {})
    metadata = contact.get("metadata", {}) if isinstance(contact, dict) else {}
    return str(
        metadata.get("display_name")
        or contact.get("contact_id")
        or contact_id
    )


def _type_label(value: str) -> str:
    return OBJECT_TYPE_LABELS.get(str(value), str(value))


def _group_type_label(value: str) -> str:
    return GROUP_TYPE_LABELS.get(str(value), str(value))


def _algorithm_lines(item: Dict[str, Any], capability: str) -> List[str]:
    catalog = _nested(item, ["meta", "algorithm_catalog"], {}) or {}
    lines: List[str] = []
    if isinstance(catalog, dict):
        for values in catalog.values():
            if isinstance(values, list):
                lines.extend(str(value) for value in values if value)
            elif isinstance(values, str) and values:
                lines.append(values)
    if capability == "cognition" and not lines:
        return DEFAULT_COGNITION_ALGORITHMS
    deduped: List[str] = []
    for line in lines:
        if line not in deduped:
            deduped.append(line)
    return deduped


def _track_lookup(artifact: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(track.get("track_id")): track
        for track in artifact.get("tracks", [])
        if track.get("track_id")
    }


def _group_lookup(artifact: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(group.get("group_id")): group
        for group in artifact.get("groups", [])
        if group.get("group_id")
    }


def _impact_lookup(artifact: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(impact.get("impact_id")): impact
        for impact in artifact.get("asset_impacts", [])
        if impact.get("impact_id")
    }


def _track_contact_id(track: Dict[str, Any]) -> str:
    metadata = track.get("metadata") or {}
    return str(metadata.get("source_contact_id") or track.get("track_id") or "unknown")


def _track_display(track: Dict[str, Any], contacts_by_id: Dict[str, Dict[str, Any]]) -> str:
    contact_id = _track_contact_id(track)
    return "{name}（{kind}，内部航迹 {track_id}）".format(
        name=_contact_name(contact_id, contacts_by_id),
        kind=_type_label(str(track.get("object_type"))),
        track_id=_value_or(track.get("track_id"), "unknown"),
    )


def _planning_target_label(
    target_id: str,
    tracking_result: Dict[str, Any],
    contacts_by_id: Dict[str, Dict[str, Any]],
) -> str:
    artifact = tracking_result.get("artifact") or {}
    tracks_by_id = _track_lookup(artifact)
    groups_by_id = _group_lookup(artifact)
    impacts_by_id = _impact_lookup(artifact)

    if target_id in impacts_by_id:
        impact = impacts_by_id[target_id]
        track = tracks_by_id.get(str(impact.get("source_track_id")), {})
        return "{track} 对 {asset} 的影响".format(
            track=_track_display(track, contacts_by_id) if track else target_id,
            asset=_value_or(impact.get("protected_asset_name"), "保护目标"),
        )
    if target_id in groups_by_id:
        group = groups_by_id[target_id]
        members = []
        for member_id in group.get("member_track_ids") or []:
            track = tracks_by_id.get(str(member_id))
            if track:
                members.append(_track_display(track, contacts_by_id))
        return "{kind}（{members}）".format(
            kind=_group_type_label(str(group.get("group_type"))),
            members="、".join(members) if members else target_id,
        )
    if target_id in tracks_by_id:
        return _track_display(tracks_by_id[target_id], contacts_by_id)
    return _contact_name(target_id, contacts_by_id)


def _tracking_brief_lines(result: Dict[str, Any], contacts_by_id: Dict[str, Dict[str, Any]]) -> List[str]:
    artifact = result.get("artifact") or {}
    lines: List[str] = []
    for track in (artifact.get("tracks") or [])[:3]:
        prediction_model = _nested(track, ["metadata", "prediction", "model"], "unknown")
        lines.append(
            "{track} | 航迹质量={quality} | 当前主预测模型={model}".format(
                track=_track_display(track, contacts_by_id),
                quality=_value_or(track.get("track_quality"), "-"),
                model=prediction_model,
            )
        )
    groups = artifact.get("groups") or []
    if groups:
        group = groups[0]
        member_names = []
        tracks_by_id = _track_lookup(artifact)
        for member_id in group.get("member_track_ids") or []:
            track = tracks_by_id.get(member_id)
            if track:
                member_names.append(_track_display(track, contacts_by_id))
        if member_names:
            lines.append(
                "编组 {group_id} | 类型={kind} | 成员={members}".format(
                    group_id=_value_or(group.get("group_id"), "unknown"),
                    kind=_group_type_label(str(group.get("group_type"))),
                    members="、".join(member_names),
                )
            )
    return lines


def _threat_bullets(
    result: Dict[str, Any],
    tracking_result: Dict[str, Any],
    contacts_by_id: Dict[str, Dict[str, Any]],
) -> List[str]:
    artifact = tracking_result.get("artifact") or {}
    impacts_by_id = _impact_lookup(artifact)
    groups_by_id = _group_lookup(artifact)
    tracks_by_id = _track_lookup(artifact)
    ranked = result.get("ranked_threats") or []
    lines: List[str] = []
    for item in ranked[:5]:
        entity_type = str(item.get("entity_type", "track"))
        entity_id = str(item.get("contact_id") or item.get("item_id") or "unknown")
        score = _value_or(item.get("priority_score"), "-")
        rank = _value_or(item.get("rank"), "?")
        if entity_type == "asset_impact":
            impact = impacts_by_id.get(entity_id, {})
            track = tracks_by_id.get(str(impact.get("source_track_id")), {})
            asset_name = _value_or(
                impact.get("protected_asset_name") or item.get("protected_asset_name"),
                "任务保护目标",
            )
            current_distance = impact.get("closest_distance_m")
            predicted_distance = impact.get("predicted_closest_distance_m")
            lines.append(
                "优先级#{rank}：{track} 对保护目标“{asset}”影响最高；当前最近距离约 {current} 米，预测最近距离约 {predicted} 米，影响分 {score}。".format(
                    rank=rank,
                    track=_track_display(track, contacts_by_id) if track else entity_id,
                    asset=asset_name,
                    current=_value_or(current_distance, "-"),
                    predicted=_value_or(predicted_distance, "-"),
                    score=score,
                )
            )
        elif entity_type == "group":
            group = groups_by_id.get(entity_id, {})
            members = []
            for member_id in group.get("member_track_ids") or []:
                track = tracks_by_id.get(str(member_id))
                if track:
                    members.append(_track_display(track, contacts_by_id))
            lines.append(
                "优先级#{rank}：编组 {group_id} 由 {members} 组成，类型={kind}，编组关注分 {score}。".format(
                    rank=rank,
                    group_id=entity_id,
                    members="、".join(members) if members else "若干航迹",
                    kind=_group_type_label(str(group.get("group_type"))),
                    score=score,
                )
            )
        else:
            track = tracks_by_id.get(entity_id, {})
            lines.append(
                "优先级#{rank}：{track} 本体威胁分 {score}，表示该目标本身就需要优先关注。".format(
                    rank=rank,
                    track=_track_display(track, contacts_by_id) if track else entity_id,
                    score=score,
                )
            )
    return lines


def _decision_plan_cards(
    result: Dict[str, Any],
    tracking_result: Dict[str, Any],
    contacts_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    recommended_id = result.get("recommended_plan_id")
    for plan in result.get("candidate_plans", [])[:6]:
        target_labels = [
            _planning_target_label(str(target_id), tracking_result, contacts_by_id)
            for target_id in plan.get("target_ids", [])
        ]
        lines = [
            "关注对象：{targets}".format(targets="、".join(target_labels) if target_labels else "未显式指定"),
            "分配资源：{resources}".format(
                resources="、".join(plan.get("assigned_resources", [])) if plan.get("assigned_resources") else "未分配"
            ),
            "关键动作：{actions}".format(
                actions="；".join(ACTION_TRANSLATIONS.get(action, action) for action in plan.get("actions", []))
                if plan.get("actions")
                else "未提供"
            ),
            "预期效果：{effects}".format(
                effects="；".join(EFFECT_TRANSLATIONS.get(effect, effect) for effect in plan.get("expected_effects", []))
                if plan.get("expected_effects")
                else "未提供"
            ),
        ]
        assumptions = [
            ASSUMPTION_TRANSLATIONS.get(str(item), str(item))
            for item in plan.get("assumptions", [])
            if not str(item).startswith("RAG evidence considered:")
        ]
        if assumptions:
            lines.append("假设前提：{text}".format(text="；".join(assumptions[:2])))
        cards.append(
            {
                "title": PLAN_NAME_LABELS.get(plan.get("name"), plan.get("name") or plan.get("id")),
                "subtitle": "推荐候选" if plan.get("id") == recommended_id else "备选方案",
                "score": _value_or(plan.get("score"), "-"),
                "summary": PLAN_SUMMARY_LABELS.get(plan.get("id"), "当前分支内置模板方案。"),
                "lines": lines,
            }
        )
    return cards


def _compliance_conclusions(result: Dict[str, Any]) -> List[str]:
    conclusions: List[str] = []
    for violation in result.get("violations", [])[:6]:
        rule_id = str(violation.get("rule_id") or "")
        conclusion = COMPLIANCE_CONCLUSION_MAP.get(rule_id)
        if conclusion and conclusion not in conclusions:
            conclusions.append(conclusion)
    for suggestion in result.get("adjustment_suggestions", [])[:3]:
        text = "建议补充：{text}".format(text=_short_text(suggestion, 80))
        if text not in conclusions:
            conclusions.append(text)
    if not conclusions:
        decision = str(result.get("decision", "review_required"))
        if decision == "approved":
            conclusions.append("当前方案在规则检查层面可以进入下一环节，但仍保持仿真闭环，不下发真实控制。")
        elif decision == "blocked":
            conclusions.append("当前方案在规则或授权边界上仍有明显缺口，暂不建议继续。")
        else:
            conclusions.append("当前方案总体可作为演示参考，但建议先做人工确认后再继续。")
    return conclusions


def _execution_bullets(result: Dict[str, Any]) -> List[str]:
    output = result.get("closed_loop_output") or {}
    commands = _nested(output, ["execution_control", "commands"], []) or []
    lines: List[str] = []
    for command in commands[:4]:
        action = EXECUTION_ACTION_LABELS.get(str(command.get("action")), str(command.get("action")))
        lines.append(
            "目标 {target} | 动作={action} | 优先级={priority}".format(
                target=_value_or(command.get("target_id"), "unknown"),
                action=action,
                priority=_value_or(command.get("priority"), "-"),
            )
        )
    return lines


def _effect_bullets(result: Dict[str, Any]) -> List[str]:
    breakdown = result.get("score_breakdown") or {}
    lines = [
        "完成度贡献：{value} x {weight}".format(
            value=_value_or(breakdown.get("completion_ratio_value"), "-"),
            weight=_value_or(breakdown.get("completion_ratio_weight"), "-"),
        ),
        "任务精度贡献：{value} x {weight}".format(
            value=_value_or(breakdown.get("task_accuracy_value"), "-"),
            weight=_value_or(breakdown.get("task_accuracy_weight"), "-"),
        ),
        "达标率贡献：{value} x {weight}".format(
            value=_value_or(breakdown.get("requirement_score_value"), "-"),
            weight=_value_or(breakdown.get("requirement_score_weight"), "-"),
        ),
    ]
    return lines


def _capability_cards(snapshot: Dict[str, Any], blackboard: Dict[str, Any]) -> List[Dict[str, Any]]:
    mission_input = blackboard.get("mission_input") or {}
    contacts_by_id = _contact_lookup(mission_input)
    results = blackboard.get("results") or {}
    tracking_result = _nested(results, ["tracking", "result"], {})
    cards: List[Dict[str, Any]] = []

    for capability in CAPABILITY_SEQUENCE:
        item = results.get(capability) or {}
        label = CAPABILITY_LABELS[capability]
        if not item:
            cards.append(
                {
                    "capability": capability,
                    "label": label,
                    "status": "pending",
                    "headline": "尚未执行",
                    "summary": "当前任务还没有运行到这一步。",
                    "bullets": [],
                    "meaning": "",
                    "algorithms": [],
                    "detail_sections": [],
                }
            )
            continue

        result = item.get("result") or {}
        algorithms = _algorithm_lines(item, capability)
        card: Dict[str, Any] = {
            "capability": capability,
            "label": label,
            "status": item.get("status"),
            "headline": "",
            "summary": "",
            "bullets": [],
            "meaning": "",
            "algorithms": algorithms,
            "detail_sections": [],
        }

        if capability == "cognition":
            targets = _nested(result, ["intelligence_packet", "targets"], []) or []
            card["headline"] = "识别目标 {count} 个".format(count=len(targets))
            card["summary"] = "把任务文本、目标清单和上游情报整理成统一情报包，供后续跟踪、威胁排序和方案规划使用。"
            card["bullets"] = [
                "{name} | 类型={kind} | 位置={location} | 意图={intent}".format(
                    name=_contact_name(str(target.get("source_contact_id") or target.get("track_id")), contacts_by_id),
                    kind=_type_label(str(target.get("class"))),
                    location=_value_or(target.get("source_location"), "未知"),
                    intent=_value_or(target.get("intent"), "未知"),
                )
                for target in targets[:4]
            ]
            card["meaning"] = "认知识别负责把输入任务和上游探测信息整理成可共享的结构化情报包。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "关键结论", "lines": card["bullets"]},
            ]

        elif capability == "tracking":
            frames_processed = int(result.get("frames_processed", 1) or 1)
            card["headline"] = "基于 {frames} 帧连续点迹形成 {tracks} 条航迹 / {groups} 个编组".format(
                frames=frames_processed,
                tracks=_value_or(result.get("track_count"), 0),
                groups=_value_or(result.get("group_count"), 0),
            )
            card["summary"] = "这一步的输出不是一句结论，而是一套连续航迹中间结果，包括航迹、预测轨迹、编组和保护目标影响项。"
            card["bullets"] = _tracking_brief_lines(result, contacts_by_id)
            card["meaning"] = "航迹跟踪负责把多帧点迹变成可持续维护、可预测的目标轨迹，并识别编组关系。"
            frame_summaries = result.get("frame_summaries") or []
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "航迹与编组要点", "lines": card["bullets"]},
                {
                    "title": "连续点迹处理情况",
                    "lines": [
                        "第 {idx} 帧：形成 {tracks} 条航迹 / {groups} 个编组".format(
                            idx=item.get("frame_index", 0) + 1,
                            tracks=item.get("track_count", 0),
                            groups=item.get("group_count", 0),
                        )
                        for item in frame_summaries[:5]
                    ],
                },
            ]

        elif capability == "threat_assessment":
            ranked = result.get("ranked_threats") or []
            card["headline"] = "已形成统一威胁排序"
            card["summary"] = "这里不是只排单个目标，而是同时比较单航迹、目标编组和对保护目标的影响项。"
            card["bullets"] = _threat_bullets(result, tracking_result, contacts_by_id)
            card["meaning"] = "威胁评估回答的是“当前最该先盯谁、先处理谁”。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "排序前列对象", "lines": card["bullets"]},
                {
                    "title": "对象解释",
                    "lines": [
                        "保护目标影响项：表示某条航迹对某个保护对象的潜在影响，不是新目标，而是一条“目标对保护对象”的关系记录。",
                        "目标编组：表示多个目标在空间位置、速度和航向上满足协同行动判据，被视作一个整体进行关注。",
                        "单航迹：表示目标本体就值得优先关注，不依赖是否成组或是否已经贴近保护目标。",
                    ],
                },
            ]

        elif capability == "decision_planning":
            plan_cards = _decision_plan_cards(result, tracking_result, contacts_by_id)
            recommended = next((card_info for card_info in plan_cards if card_info.get("subtitle") == "推荐候选"), None)
            card["headline"] = recommended.get("title") if recommended else "已生成候选方案"
            card["summary"] = "当前分支内置 3 类模板方案，与分支实现保持一致；这一步会先生成 3 个候选方案，再按打分排序给出推荐。"
            card["bullets"] = [
                "当前分支内置 3 类模板方案：重点目标优先监视、广域持续覆盖监视、节约资源持续监视。",
                "推荐方案：{name}".format(name=recommended.get("title") if recommended else "未给出"),
                "方案数量：{count}".format(count=len(plan_cards)),
            ]
            card["meaning"] = "方案规划负责基于威胁排序和资源情况，生成多套监视/压制思路并给出推荐。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "方案总览", "text": card["summary"]},
                {"title": "候选方案卡片", "cards": plan_cards},
            ]

        elif capability == "compliance_authorization":
            conclusions = _compliance_conclusions(result)
            decision = str(result.get("decision", "review_required"))
            headline = "建议人工确认后继续"
            if decision == "approved":
                headline = "形式上可继续，但仍建议人工确认"
            elif decision == "blocked":
                headline = "当前不建议继续"
            card["headline"] = headline
            card["summary"] = "这一步不是否定方案，而是检查方案是否把仿真边界、审批范围和方案级授权说清楚。"
            card["bullets"] = conclusions[:4]
            card["meaning"] = "合规授权负责判断方案能不能进入下一步演示交接，以及还缺哪些边界说明。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "汇报口径结论", "lines": conclusions},
            ]

        elif capability == "execution_control":
            card["headline"] = "生成模拟命令 {count} 条".format(count=_value_or(result.get("command_count"), 0))
            card["summary"] = "这里输出的是仿真闭环动作序列，不会下发真实控制链路。"
            card["bullets"] = _execution_bullets(result)
            card["meaning"] = "执行控制负责把前面方案转成仿真动作序列，并更新模拟完成度。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "模拟动作摘要", "lines": card["bullets"]},
            ]

        elif capability == "effect_evaluation":
            assessment = ASSESSMENT_LABELS.get(str(result.get("assessment")), str(result.get("assessment")))
            card["headline"] = "评估分 {score}".format(score=_value_or(result.get("overall_score"), "-"))
            card["summary"] = "闭环评估分来自完成度、任务精度和达标率的综合打分。当前结论：{assessment}。".format(
                assessment=assessment
            )
            card["bullets"] = _effect_bullets(result)
            card["meaning"] = "效果评估负责判断这次仿真闭环是否达到了任务阈值，以及是否需要重规划。"
            card["detail_sections"] = [
                {"title": "本步作用", "text": card["meaning"]},
                {"title": "打分拆解", "lines": card["bullets"]},
            ]

        cards.append(card)
    return cards


def _overview(snapshot: Dict[str, Any], mission_input: Dict[str, Any]) -> Dict[str, Any]:
    metadata = mission_input.get("metadata") or {}
    return {
        "display_name": metadata.get("display_name") or snapshot.get("display_name") or mission_input.get("scenario_name"),
        "display_summary": metadata.get("display_summary") or "",
        "objective": mission_input.get("objective"),
        "contacts_count": len(mission_input.get("contacts", [])),
        "friendly_count": len(mission_input.get("friendly_platforms", [])),
        "success_threshold": mission_input.get("success_threshold"),
        "max_replans": mission_input.get("max_replans"),
        "planning_focus": metadata.get("planning_focus") or _nested(mission_input, ["metadata", "planning_focus"], "default"),
        "frame_count": len(mission_input.get("perception_frames", [])),
        "template_id": metadata.get("template_id") or snapshot.get("template_id"),
        "scenario_name": mission_input.get("scenario_name"),
    }


def _performance(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    results = blackboard.get("results") or {}
    effect = _nested(results, ["effect_evaluation", "result"], {})
    execution = _nested(results, ["execution_control", "result"], {})
    completed_count = sum(1 for capability in CAPABILITY_SEQUENCE if capability in results)
    tracking = _nested(results, ["tracking", "result"], {})
    return {
        "overall_score": effect.get("overall_score"),
        "completion_ratio": effect.get("completion_ratio") or execution.get("completion_ratio"),
        "completed_capabilities_count": completed_count,
        "total_capabilities": len(CAPABILITY_SEQUENCE),
        "replan_count": _nested(blackboard, ["summary", "replan_count"], 0),
        "frames_processed": tracking.get("frames_processed") or 0,
    }


def _task_text(snapshot: Dict[str, Any], blackboard: Dict[str, Any]) -> Dict[str, Any]:
    mission_input = blackboard.get("mission_input") or {}
    metadata = mission_input.get("metadata") or {}
    contacts = mission_input.get("contacts") or []
    platforms = mission_input.get("friendly_platforms") or []
    adjustments = _nested(blackboard, ["operator", "adjustments"], []) or []

    overview_lines = [
        "任务名称：{name}".format(name=metadata.get("display_name") or mission_input.get("scenario_name") or snapshot.get("workflow_id")),
        "任务目标：{objective}".format(objective=_value_or(mission_input.get("objective"), "未提供")),
        "示例任务说明：{summary}".format(summary=metadata.get("display_summary") or "未提供"),
        "连续点迹帧数：{count} 帧".format(count=len(mission_input.get("perception_frames", []))),
    ]
    hostile_contacts = [
        "{name} | 类型={kind} | 位置={location} | 威胁等级={score} | 意图={intent}".format(
            name=_value_or(_nested(item, ["metadata", "display_name"], item.get("contact_id")), "unknown"),
            kind=_type_label(_nested(item, ["metadata", "object_type"], item.get("kind"))),
            location=_value_or(item.get("location"), "未知"),
            score=_value_or(item.get("threat_level"), "-"),
            intent=_value_or(item.get("intent"), "未知"),
        )
        for item in contacts
    ]
    friendly_platforms = [
        "{platform} | 类型={kind} | 就绪度={readiness} | 弹药={munitions} | 位置={location}".format(
            platform=_value_or(item.get("platform_id"), "unknown"),
            kind=_value_or(item.get("platform_type"), "generic"),
            readiness=_value_or(item.get("readiness"), "-"),
            munitions=_value_or(item.get("munitions"), "-"),
            location=_value_or(item.get("location"), "未知"),
        )
        for item in platforms
    ]
    environment = [
        "{key}: {value}".format(key=key, value=value)
        for key, value in (mission_input.get("environment") or {}).items()
    ]
    constraints = [
        "{key}: {value}".format(key=key, value=value)
        for key, value in (mission_input.get("constraints") or {}).items()
    ]
    operator = [
        "已注入人工调整 {count} 次".format(count=len(adjustments)),
    ]
    if adjustments:
        for adjustment in adjustments[-3:]:
            note = adjustment.get("note") or "未写说明"
            operator.append("最近调整：{note}".format(note=note))
    else:
        operator.append("当前没有人工调整。")
    return {
        "overview_lines": overview_lines,
        "hostile_contacts": hostile_contacts,
        "friendly_platforms": friendly_platforms,
        "environment": environment,
        "constraints": constraints,
        "operator": operator,
    }


def _capability_flow(blackboard: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = blackboard.get("results") or {}
    flow: List[Dict[str, Any]] = []
    for capability in CAPABILITY_SEQUENCE:
        item = results.get(capability) or {}
        flow.append(
            {
                "capability": capability,
                "label": CAPABILITY_LABELS[capability],
                "status": item.get("status", "pending"),
                "next_suggestion": item.get("next_suggestion"),
            }
        )
    return flow


def _trace_summary(blackboard: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for item in (blackboard.get("trace") or [])[-18:]:
        detail_parts = []
        for key, value in item.items():
            if key in {"timestamp", "event"}:
                continue
            display_value = value
            if key == "capability":
                display_value = CAPABILITY_LABELS.get(str(value), value)
            elif key == "adjustment" and isinstance(value, dict):
                display_value = "；".join("{k}={v}".format(k=k, v=v) for k, v in value.items())
            detail_parts.append("{key}={value}".format(key=key, value=display_value))
        summary.append(
            {
                "timestamp": item.get("timestamp"),
                "event": item.get("event"),
                "label": EVENT_LABELS.get(str(item.get("event")), str(item.get("event"))),
                "detail": " | ".join(detail_parts),
            }
        )
    return summary


def build_mission_report(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    blackboard = snapshot.get("blackboard") or {}
    mission_input = blackboard.get("mission_input") or {}
    overview = _overview(snapshot, mission_input)
    performance = _performance(blackboard)
    capabilities = _capability_cards(snapshot, blackboard)
    return {
        "workflow_id": snapshot.get("workflow_id"),
        "status": snapshot.get("status"),
        "submitted_at": snapshot.get("submitted_at"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "current_capability": snapshot.get("current_capability"),
        "last_error": snapshot.get("last_error"),
        "overview": overview,
        "performance": performance,
        "task_text": _task_text(snapshot, blackboard),
        "capability_flow": _capability_flow(blackboard),
        "capability_cards": capabilities,
        "trace": _trace_summary(blackboard),
        "raw_available": True,
    }
