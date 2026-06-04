"""Algorithm provider boundary for the Track Threat Agent.

The current demo intentionally keeps algorithms in-process. This provider
creates a stable seam for replacing the built-in implementation with a shared
algorithm library or remote algorithm service later without changing A2A/Nacos
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .models import Detection, ProtectedAsset, TrackState


@dataclass
class LocalBuiltInAlgorithmProvider:
    tracker: Any
    graph_predictor: Any
    ranker: Any
    impact_analyzer: Any
    group_detector: Any

    mode: str = "local_builtin"

    def update_tracks(self, detections: List[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        tracks = self.tracker.update(detections, algorithm_level=algorithm_level)
        return self.graph_predictor.refine(tracks)

    def rank_threats(self, tracks: List[TrackState], scene: Dict[str, Any]) -> List[Any]:
        return self.ranker.rank(tracks, scene)

    def analyze_asset_impacts(
        self,
        tracks: List[TrackState],
        threats: List[Any],
        protected_assets: List[ProtectedAsset],
    ) -> List[Any]:
        return self.impact_analyzer.assess(tracks, threats, protected_assets)

    def detect_groups(self, tracks: List[TrackState], threats: List[Any], scene: Dict[str, Any]) -> List[Any]:
        return self.group_detector.detect(tracks, threats, scene)

    def reset(self) -> None:
        self.tracker.reset()
        self.ranker.reset()
        self.group_detector.reset()
