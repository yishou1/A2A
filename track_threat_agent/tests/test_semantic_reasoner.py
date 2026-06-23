from app.models import TrackState
from app.semantic_reasoner import KGTransformerSemanticReasoner
from app.utils import speed_heading_to_velocity


def make_track() -> TrackState:
    vx, vy = speed_heading_to_velocity(90, 180)
    return TrackState(
        track_id="trk-semantic",
        object_type="unknown",
        lat=31.2,
        lon=121.4,
        alt=1200,
        speed=90,
        heading=180,
        vx=vx,
        vy=vy,
        track_quality=0.88,
        last_update_time=1000,
        history_path=[],
        predicted_path=[],
        metadata={
            "label": "hostile",
            "affiliation": "red",
            "threat_level": "high",
            "knowledge_relations": [{"predicate": "threat_of", "object": "protected_asset"}],
            "st_gnn_inspired": {"graph_influence": 0.5},
        },
    )


def test_kg_transformer_reasoner_returns_attention_and_intent_probabilities():
    result = KGTransformerSemanticReasoner().reason(make_track())

    assert result["algorithm"] == "KG+Transformer"
    assert result["semantic_factor"] > 0.8
    assert result["token_count"] >= 4
    assert result["attention"]
    assert "asset_approach" in result["intent_probabilities"]
    assert result["dominant_intent"] in result["intent_probabilities"]
