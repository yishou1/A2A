from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import struct
from typing import Any, Iterator

import pytest

from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
from amos_platform.agents.a2a.mapper import build_commander_workflow_payload
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.domain.policies.visibility import find_truth_leaks
from amos_platform.media.evidence_products import get_evidence_product_service
from amos_platform.sensors.coverage import bearing_deg, distance_nm
from amos_platform.simulation.engine import SimEngine


SCENARIOS = {
    "amphibious-landing-joint-operation": "standard",
    # This is the only branch on which the relay degradation product is due.
    "border-uav-evacuation": "communication_degraded",
    "maritime-convoy-air-defense": "standard",
}
SIMULATION_STEP_SEC = 30
BOR_LINK_FAULT_AT_SEC = 1110
ROOT = Path(__file__).resolve().parents[1]

GEOMETRY_ANCHORS = {
    "amphibious-landing-joint-operation": {
        "AMP-MEDIA-01": ((22.671530, 120.393248, 12000), 119.99, 17.70, [6.19], [6.50]),
        "AMP-MEDIA-07": ((22.582285, 120.402781, 9000), 64.90, 15.51, [5.34], [5.54]),
        "AMP-MEDIA-08": ((22.671610, 120.328781, 9000), 109.13, 8.90, [9.46], [9.57]),
        "AMP-MEDIA-09": ((22.569474, 120.382313, 8500), 63.06, 11.80, [6.70], [6.84]),
    },
    "border-uav-evacuation": {
        "BOR-MEDIA-01": ((23.745453, 121.241375, 5200), 213.25, 32.36, [1.35], [1.60]),
        "BOR-MEDIA-02": ((23.635755, 121.214146, 6000), 25.52, 5.49, [10.28], [10.33]),
        "BOR-MEDIA-07": ((23.680000, 121.150000, 4800), 54.51, 8.39, [5.36, 11.73], [5.41, 11.76]),
    },
    "maritime-convoy-air-defense": {
        "MAR-MEDIA-03": ((22.216446, 121.359430, 0), 141.30, 0.00, [7.61], [7.61]),
        "MAR-MEDIA-04": ((22.245648, 121.403350, 0), 171.33, 0.00, [13.09], [13.09]),
        "MAR-MEDIA-07": ((22.169479, 121.605630, 8000), 13.07, 33.86, [1.96], [2.36]),
    },
}


@dataclass(frozen=True)
class Replay:
    scenario: dict[str, Any]
    engine: SimEngine
    captures: dict[str, dict[str, Any]]
    context_at_capture: dict[str, dict[str, Any]]


def _angle_delta(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _due_on_branch(plan: dict[str, Any], branch: str) -> bool:
    branches = {str(value) for value in plan.get("branch_ids") or ["*"]}
    return "*" in branches or branch in branches


def _completed_commander_result(engine: SimEngine, *, workflow_id: str) -> dict[str, Any]:
    run_id = str(engine.clock["run_id"])
    tracking_rows = []
    threat_rows = []
    for track in engine.sensor_fusion.tracks.values():
        truth_id = engine._truth_target_for_track(track)
        if truth_id == "CONTACT-HOSTILE-01":
            tracking_rows.append({
                "track_id": track.id,
                "object_type": "ship",
                "metadata": {
                    "source_class": "fast_attack_craft",
                    "label": "hostile",
                    "affiliation": "red",
                    "threat_level": "high",
                },
                "lat": track.lat,
                "lon": track.lng,
            })
            threat_rows.append({"track_id": track.id, "score": 0.91, "level": "high"})
        elif truth_id == "CONTACT-FISHING-01":
            tracking_rows.append({
                "track_id": track.id,
                "object_type": "ship",
                "metadata": {
                    "source_class": "fishing_vessel",
                    "label": "civilian fishing vessel",
                    "affiliation": "civilian",
                    "threat_level": "low",
                },
                "lat": track.lat,
                "lon": track.lng,
            })
            threat_rows.append({"track_id": track.id, "score": 0.12, "level": "low"})
    result = apply_commander_assessments(
        engine,
        {
            "workflow_id": workflow_id,
            "status": "completed",
            "result": {
                "outputs": {
                    "tracking_result": [{
                        "value": {"tracks": tracking_rows, "threats": []},
                    }],
                    "threat_assessment_result": [{
                        "value": {"threats": threat_rows},
                    }],
                },
                "summary": {
                    "verification": "completed backend workflow fixture",
                    "simulation_only": True,
                },
            },
        },
        submission={
            "run_id": run_id,
            "transport": "gateway",
            "package": {"package_id": f"pkg-{workflow_id}", "verified": True},
            "snapshot_sequence": int(
                engine.sensor_fusion.last_observation_batch.get("tick_id", 0) or 0
            ),
            "simulation_time_sec": float(engine.clock["elapsed_sec"]),
        },
    )
    assert result["status"] == "completed"
    assert result["analysis"]["source"] == "commander_workflow"
    return result


def _record_new_capture_context(
    engine: SimEngine,
    known_media_ids: set[str],
    context: dict[str, dict[str, Any]],
) -> None:
    for capture in engine.media_capture.public_captures():
        media_id = str(capture["media_id"])
        if media_id in known_media_ids:
            continue
        known_media_ids.add(media_id)
        context[media_id] = {
            "assets": deepcopy(engine.assets),
            "threats": deepcopy(engine.threats),
        }


@lru_cache(maxsize=None)
def _replay(scenario_id: str) -> Replay:
    scenario = deepcopy(get_scenario(scenario_id))
    assert scenario is not None
    branch = SCENARIOS[scenario_id]
    run_id = f"run-capture-realism-{scenario_id}"
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_branch": branch,
        "director_mode": "integration",
    })

    known_media_ids: set[str] = set()
    context: dict[str, dict[str, Any]] = {}
    engine.evaluate_media_captures()
    _record_new_capture_context(engine, known_media_ids, context)

    plans = [
        plan for plan in scenario["capture_plans"]
        if _due_on_branch(plan, branch)
    ]
    last_capture_sec = max(float(plan["at_sec"]) for plan in plans)
    command_at = {
        float(plan["at_sec"])
        for plan in plans
        if plan["product_type"] == "command_product"
    }

    while float(engine.clock["elapsed_sec"]) < last_capture_sec:
        elapsed = float(engine.clock["elapsed_sec"])
        step = min(float(SIMULATION_STEP_SEC), last_capture_sec - elapsed)
        next_elapsed = elapsed + step
        if scenario_id == "border-uav-evacuation" and next_elapsed == BOR_LINK_FAULT_AT_SEC:
            engine.assets["RELAY-UAV-01"]["health"]["comms_strength"] = 24.0
        if next_elapsed in command_at:
            _completed_commander_result(
                engine,
                workflow_id=f"wf-capture-{scenario_id}",
            )
        engine._tick(step)
        if scenario_id == "maritime-convoy-air-defense":
            prompt = engine.get_operator_state().get("follow_launch_prompt")
            if prompt:
                authorized = engine.authorize_follow_asset(
                    str(prompt["asset_id"]),
                    str(prompt["track_id"]),
                    authorized=True,
                )
                assert authorized["status"] == "authorized"
            if next_elapsed in command_at and not engine._engagement_warnings:
                hostile = next(
                    track for track in engine.sensor_fusion.tracks.values()
                    if engine._truth_target_for_track(track) == "CONTACT-HOSTILE-01"
                )
                warning = engine.issue_warning_at_track(hostile.id, authorized=True)
                assert warning["status"] == "issued"
            if engine._engagement_warnings and not engine.weapons:
                warning = list(engine._engagement_warnings.values())[-1]
                if float(engine.clock["elapsed_sec"]) < float(warning["fire_not_before_sec"]):
                    _record_new_capture_context(engine, known_media_ids, context)
                    continue
                hostile = engine.sensor_fusion.tracks[str(warning["track_id"])]
                launched = engine.fire_weapon_at_track(
                    hostile.id,
                    asset_id="ESCORT-01",
                    weapon_name="舰载反舰导弹",
                    authorized=True,
                )
                assert launched["status"] == "launched"
        _record_new_capture_context(engine, known_media_ids, context)

    captures = {
        str(capture["media_id"]): capture
        for capture in engine.media_capture.public_captures()
    }
    return Replay(scenario, engine, captures, context)


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_every_due_capture_is_released_at_its_planned_simulation_tick(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    branch = SCENARIOS[scenario_id]
    expected = [
        plan for plan in replay.scenario["capture_plans"]
        if _due_on_branch(plan, branch)
    ]

    assert list(replay.captures) == [plan["media_id"] for plan in expected]
    for plan in expected:
        capture = replay.captures[plan["media_id"]]
        assert capture["capture_id"] == plan["capture_id"]
        assert capture["captured_at_sim_time"] == pytest.approx(plan["at_sec"])
        assert capture["at_sec"] == pytest.approx(plan["at_sec"])
        assert capture["run_id"] == replay.engine.clock["run_id"]
        assert capture["capture_provenance"]["frozen"] is True


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_targeted_raw_frames_match_capture_platform_sensor_and_geometry(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    plans = {
        plan["media_id"]: plan
        for plan in replay.scenario["capture_plans"]
        if plan["product_type"] == "raw_sensor_frame" and plan.get("target_refs")
    }

    for media_id, plan in plans.items():
        capture = replay.captures[media_id]
        context = replay.context_at_capture[media_id]
        platform = context["assets"][plan["platform_id"]]
        observations = list((capture.get("product_data") or {}).get("observations") or [])
        observation_ids = [str(item["observation_id"]) for item in observations]

        assert capture["platform_id"] == plan["platform_id"]
        assert capture["sensor_instance_id"] == plan["sensor_instance_id"]
        assert capture.get("platform_pose")
        assert capture.get("sensor_pose")
        assert capture.get("sensor_config")
        assert observations
        assert len(observation_ids) == len(set(observation_ids))
        assert set(observation_ids) == set(capture["observation_ids"])
        assert len(observation_ids) >= len(set(plan["target_refs"]))
        assert all(item["asset_id"] == plan["platform_id"] for item in observations)
        assert all(
            float(item["sim_time"]) == pytest.approx(capture["captured_at_sim_time"])
            for item in observations
        )

        pose = capture["platform_pose"]
        assert pose["lat"] == pytest.approx(platform["position"]["lat"], abs=1e-6)
        assert pose["lon"] == pytest.approx(platform["position"]["lng"], abs=1e-6)
        if platform["domain"] == "air":
            assert capture["sensor_pose"]["depression_angle_deg"] is not None
            assert abs(
                float(capture["sensor_pose"]["depression_angle_deg"])
                - float(capture["sensor_pose"]["planned_depression_angle_deg"])
            ) <= 1.0

        max_range_nm = float(capture["sensor_config"]["range_nm"])
        fov_deg = float(capture["sensor_config"]["fov_deg"])
        azimuth_deg = float(capture["sensor_pose"]["azimuth_deg"])
        for observation in observations:
            checked_range = float(
                observation.get("slant_range_nm", observation["range_nm"])
            )
            assert checked_range <= max_range_nm + 0.01
            assert _angle_delta(float(observation["bearing_deg"]), azimuth_deg) <= fov_deg / 2 + 0.01

        # Private truth is used only here, in the test, to prove that each
        # public bearing/range pair was measured from the platform pose frozen
        # at the same simulation tick.
        unmatched = observations.copy()
        for target_ref in plan["target_refs"]:
            target = context["threats"][target_ref]
            expected_range = distance_nm(
                pose["lat"], pose["lon"], target["lat"], target["lng"]
            )
            expected_bearing = bearing_deg(
                pose["lat"], pose["lon"], target["lat"], target["lng"]
            )
            match = next(
                (
                    item for item in unmatched
                    if abs(float(item["range_nm"]) - expected_range) <= 0.02
                    and _angle_delta(float(item["bearing_deg"]), expected_bearing) <= 0.02
                ),
                None,
            )
            assert match is not None, (scenario_id, media_id, target_ref)
            unmatched.remove(match)


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_documented_default_seed_geometry_anchors_do_not_drift(scenario_id: str) -> None:
    replay = _replay(scenario_id)
    for media_id, expected in GEOMETRY_ANCHORS[scenario_id].items():
        position, azimuth, depression, ground_ranges, slant_ranges = expected
        capture = replay.captures[media_id]
        pose = capture["platform_pose"]
        sensor_pose = capture["sensor_pose"]
        observations = list(capture["product_data"]["observations"])

        assert pose["lat"] == pytest.approx(position[0], abs=1e-6)
        assert pose["lon"] == pytest.approx(position[1], abs=1e-6)
        assert pose["alt_ft"] == pytest.approx(position[2], abs=0.1)
        assert sensor_pose["azimuth_deg"] == pytest.approx(azimuth, abs=0.01)
        assert sensor_pose["depression_angle_deg"] == pytest.approx(depression, abs=0.01)
        assert [item["range_nm"] for item in observations] == pytest.approx(
            ground_ranges, abs=0.01,
        )
        assert [item["slant_range_nm"] for item in observations] == pytest.approx(
            slant_ranges, abs=0.01,
        )


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_declared_png_resolution_matches_the_delivered_media(scenario_id: str) -> None:
    replay = _replay(scenario_id)
    plans = {str(item["media_id"]): item for item in replay.scenario["capture_plans"]}
    for media in replay.scenario["media_cues"]:
        if media.get("mime_type") != "image/png":
            continue
        payload = (ROOT / str(media["uri"]).removeprefix("/")).read_bytes()
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", payload[16:24])
        assert plans[str(media["media_id"])]["capture_parameters"]["resolution_px"] == [
            width, height,
        ]
        assert replay.captures[str(media["media_id"])]["sensor_config"]["resolution"] == [
            width, height,
        ]


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_instrument_products_contain_only_contemporaneous_observations(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    plans = {
        plan["media_id"]: plan
        for plan in replay.scenario["capture_plans"]
        if plan["product_type"] == "derived_sensor_product"
        and (plan.get("capture_parameters") or {}).get("data_source")
        == "sensor_observations"
    }

    for media_id, plan in plans.items():
        capture = replay.captures[media_id]
        observations = list((capture.get("product_data") or {}).get("observations") or [])
        assert observations, (scenario_id, media_id)
        assert all(item["asset_id"] == plan["platform_id"] for item in observations)
        window_sec = float((plan.get("capture_parameters") or {}).get("observation_window_sec", 0) or 0)
        captured_at = float(capture["captured_at_sim_time"])
        assert all(
            captured_at - window_sec <= float(item["sim_time"]) <= captured_at
            for item in observations
        )
        if window_sec:
            assert capture["product_data"]["observation_window"] == {
                "start_sec": pytest.approx(max(0.0, captured_at - window_sec)),
                "end_sec": pytest.approx(captured_at),
                "observation_count": len(observations),
            }
            assert len({float(item["sim_time"]) for item in observations}) > 1
        else:
            assert all(float(item["sim_time"]) == pytest.approx(captured_at) for item in observations)
        assert set(capture["observation_ids"]) == {
            str(item["observation_id"]) for item in observations
        }


def test_border_final_review_observes_people_and_vehicle_as_distinct_contacts() -> None:
    replay = _replay("border-uav-evacuation")
    capture = replay.captures["BOR-MEDIA-07"]
    expected_targets = {"CONTACT-PERSONNEL-01", "CONTACT-VEHICLE-01"}
    observation_ids = [str(value) for value in capture["observation_ids"]]
    associations = {
        str(item["observation_id"]): str(item["truth_id"])
        for item in replay.engine.sensor_fusion.truth_associations
        if str(item.get("observation_id") or "") in set(observation_ids)
    }

    assert len(observation_ids) == 2
    assert len(set(observation_ids)) == 2
    assert set(associations) == set(observation_ids)
    assert set(associations.values()) == expected_targets
    assert len((capture.get("product_data") or {}).get("observations") or []) == 2


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_external_precollected_media_never_fabricates_live_platform_pose(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    external_ids = {
        plan["media_id"]
        for plan in replay.scenario["capture_plans"]
        if plan["product_type"] == "external_precollected"
    }
    assert external_ids
    for media_id in external_ids:
        capture = replay.captures[media_id]
        assert "platform_pose" not in capture
        assert "sensor_pose" not in capture
        assert capture["observation_ids"] == []
        assert capture["track_ids"] == []
        assert capture["capture_provenance"] == {
            "source_kind": "external_precollected",
            "capability_source_kind": "external_source",
            "simulated": True,
            "frozen": True,
        }


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_runtime_svg_products_are_immutable_and_addressed_by_capture_tick(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    service = get_evidence_product_service()
    svg_captures = [
        capture for capture in replay.captures.values()
        if capture.get("mime_type") == "image/svg+xml"
    ]
    assert svg_captures

    for capture in svg_captures:
        token = str(round(float(capture["captured_at_sim_time"]) * 1000))
        expected_suffix = f"/{capture['media_id']}/{token}.svg"
        assert capture["dynamic_uri"].endswith(expected_suffix)
        assert capture["uri"] == capture["dynamic_uri"]
        assert capture["checksum"] == capture["dynamic_checksum"]
        stored = service.get(str(capture["run_id"]), str(capture["media_id"]), token)
        assert stored is not None
        assert stored.checksum == capture["dynamic_checksum"]
        assert hashlib.sha256(stored.content).hexdigest() == stored.checksum


def test_border_link_monitor_freezes_metrics_before_and_after_relay_degradation() -> None:
    replay = _replay("border-uav-evacuation")
    capture = replay.captures["BOR-MEDIA-04"]
    network = capture["product_data"]["network"]
    history = network["history_records"]
    before = [item for item in history if float(item["sim_time"]) < BOR_LINK_FAULT_AT_SEC]
    after = [item for item in history if float(item["sim_time"]) >= BOR_LINK_FAULT_AT_SEC]

    assert capture["product_data"]["renderer_type"] == "link_monitor"
    assert float(history[0]["sim_time"]) <= 1050
    assert float(history[-1]["sim_time"]) == pytest.approx(1470)
    assert before and after
    before_links = [link for sample in before for link in sample["links"]]
    after_links = [link for sample in after for link in sample["links"]]
    assert before_links and after_links
    assert all(
        "RELAY-UAV-01" in {link["from"], link["to"]}
        for link in before_links + after_links
    )
    metric_keys = {
        "quality", "bandwidth_mbps", "distance_km", "rssi_dbm", "snr_db",
        "packet_loss_pct", "latency_ms", "jitter_ms",
    }
    assert all(metric_keys.issubset(link) for link in before_links + after_links)
    assert sum(link["quality"] for link in after_links) / len(after_links) < (
        sum(link["quality"] for link in before_links) / len(before_links)
    )
    assert sum(link["packet_loss_pct"] for link in after_links) / len(after_links) > (
        sum(link["packet_loss_pct"] for link in before_links) / len(before_links)
    )


def test_command_product_waits_for_verified_completed_commander_result() -> None:
    scenario = deepcopy(get_scenario("amphibious-landing-joint-operation"))
    assert scenario is not None
    command_plan = next(
        plan for plan in scenario["capture_plans"]
        if plan["product_type"] == "command_product"
    )
    due_at = float(command_plan["at_sec"])
    engine = SimEngine(seed=701)
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-command-gate",
        "scenario_id": scenario["id"],
        "scenario_branch": "standard",
    })
    engine.evaluate_media_captures()
    while float(engine.clock["elapsed_sec"]) < due_at:
        engine._tick(min(SIMULATION_STEP_SEC, due_at - float(engine.clock["elapsed_sec"])))

    assert command_plan["media_id"] not in engine.media_capture.captured_media_ids
    task = next(
        item for item in engine.tasks
        if item.get("task_id") == command_plan["required_task_id"]
    )
    assert task["status"] == "awaiting_backend"

    projection = _completed_commander_result(engine, workflow_id="wf-command-gate")
    assert projection["analysis"]["status"] == "completed"
    newly_captured = engine.evaluate_media_captures()
    command_capture = next(
        item for item in newly_captured
        if item["media_id"] == command_plan["media_id"]
    )

    assert command_capture["captured_at_sim_time"] == pytest.approx(due_at)
    assert command_capture["capture_provenance"]["source_kind"] == "commander_workflow"
    assert command_capture["product_data"]["workflow"] == {
        "source": "commander_workflow",
        "status": "completed",
        "workflow_id": "wf-command-gate",
        "applied_assessment_count": 0,
        "source_run_id": "run-command-gate",
        "source_snapshot_sequence": int(
            engine.sensor_fusion.last_observation_batch.get("tick_id", 0) or 0
        ),
        "source_simulation_time_sec": due_at,
    }


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_operator_agent_and_commander_outputs_do_not_leak_private_targets_or_truth(
    scenario_id: str,
) -> None:
    replay = _replay(scenario_id)
    operator_state = replay.engine.get_operator_state()
    agent_state = replay.engine.get_agent_visible_state()
    payload = build_commander_workflow_payload(
        replay.scenario,
        operator_state,
        agent_state,
        workflow_mode="bpel",
        workflow_file="integrated_system/workflows/integrated_demo_workflow.bpel",
    )
    truth_terms = [str(item["threat_id"]) for item in replay.scenario["threats"]]

    for name, public_value in (
        ("operator", operator_state),
        ("agent", agent_state),
        ("commander", payload),
    ):
        keys = set(_walk_keys(public_value))
        assert "target_refs" not in keys, name
        assert not any(key.startswith("truth") for key in keys), name
        assert find_truth_leaks(public_value, truth_terms=truth_terms, context=name) == []
        serialized = repr(public_value)
        assert all(term not in serialized for term in truth_terms), name


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_each_media_declares_planned_consumers_without_claiming_execution(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    expected: dict[str, dict[str, set[str]]] = {}
    for cue in scenario["timeline"]:
        for media_id in cue.get("media_ids") or []:
            row = expected.setdefault(media_id, {"roles": set(), "models": set()})
            row["roles"].update(cue.get("functional_agent_ids") or [])
            row["models"].update(cue.get("model_requirement_ids") or [])
    for media in scenario["media_cues"]:
        consumer = media["consumer_context"]
        assert consumer["planned"] is True
        assert set(consumer["functional_role_ids"]) == expected[media["media_id"]]["roles"]
        assert set(consumer["model_requirement_ids"]) == expected[media["media_id"]]["models"]
        assert consumer["execution_evidence"] == "backend_trace_required"
        assert "verified" not in consumer
