"""TIA 11 算法包默认 HTTP 端点（与 examples/*/algorithm_card.yaml 端口一致）。"""

from __future__ import annotations

TIA_ALGORITHM_PORTS: dict[str, int] = {
    "battlefield_rtdetr_detector": 9020,
    "siamese_mask2former_damage": 9021,
    "edl_evidential_verifier": 9022,
    "motr_neural_kalman_tracker": 9023,
    "marl_ppo_task_scheduler": 9024,
    "imagebind_multimodal_encoder": 9025,
    "multimodal_mamba_fusion": 9026,
    "supcon_meta_classifier": 9027,
    "synapse_rag_retriever": 9028,
    "knowledge_semantic_comm": 9029,
    "marl_dynamic_router": 9030,
    "multimodal_feature_fuser": 9042,
    "target_type_classifier": 9042,
    "track_state_updater": 9042,
    "trajectory_predictor": 9042,
    "graph_relation_reasoner": 9042,
}

TIA_ALGORITHM_VERSIONS: dict[str, str] = {aid: "1.0.0" for aid in TIA_ALGORITHM_PORTS}

TIA_ALGORITHM_PATHS: dict[str, str] = {
    "multimodal_feature_fuser": "/multimodal_feature_fuser",
    "target_type_classifier": "/target_type_classifier",
    "track_state_updater": "/track_state_updater",
    "trajectory_predictor": "/trajectory_predictor",
    "graph_relation_reasoner": "/graph_relation_reasoner",
}


def default_predict_endpoint(algorithm_id: str, host: str = "127.0.0.1") -> str:
    port = TIA_ALGORITHM_PORTS[algorithm_id]
    path = TIA_ALGORITHM_PATHS.get(algorithm_id, "")
    return f"http://{host}:{port}{path}/predict"


def resolve_endpoint(algorithm_id: str, library_cfg: dict) -> str:
    endpoints = library_cfg.get("endpoints") or {}
    if algorithm_id in endpoints:
        return str(endpoints[algorithm_id])
    host = str(library_cfg.get("host", "127.0.0.1"))
    return default_predict_endpoint(algorithm_id, host=host)
