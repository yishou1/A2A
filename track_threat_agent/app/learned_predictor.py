"""Optional learned trajectory predictor used by the online Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import TrackState


class LearnedTrajectoryPredictor:
    def __init__(self, model_path: Path | str | None) -> None:
        self.model_path = Path(model_path) if model_path else None
        self.model = None
        self.load_error: str | None = None
        self._try_load()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def status(self) -> dict:
        return {
            "loaded": self.loaded,
            "model_path": str(self.model_path) if self.model_path else None,
            "model_type": getattr(self.model, "model_type", None),
            "load_error": self.load_error,
        }

    def refine_tracks(self, tracks: Iterable[TrackState]) -> List[TrackState]:
        track_list = list(tracks)
        if self.model is None:
            return track_list
        for track in track_list:
            self._refine_track(track)
        return track_list

    def _try_load(self) -> None:
        if self.model_path is None or not self.model_path.exists():
            return
        try:
            from .model_runtime import NumpySequencePredictor

            self.model = NumpySequencePredictor.load(self.model_path)
            self.load_error = None
        except Exception as exc:  # pragma: no cover - defensive startup guard
            self.model = None
            self.load_error = str(exc)

    def _refine_track(self, track: TrackState) -> None:
        assert self.model is not None
        history = [
            {
                "timestamp": float(point.get("timestamp", track.last_update_time)),
                "lat": float(point.get("lat", track.lat)),
                "lon": float(point.get("lon", track.lon)),
                "alt": float(point.get("alt", track.alt)),
                "speed": float(point.get("speed", track.speed)),
                "heading": float(point.get("heading", track.heading)),
            }
            for point in track.history_path
        ]
        template = [{"dt_s": float(offset)} for offset in self.model.future_offsets_s]
        learned_points = self.model.predict_history(history, template)
        if not learned_points:
            track.metadata["learned_predictor"] = {
                "loaded": True,
                "applied": False,
                "reason": "history length or future offsets do not match trained model",
                "model_type": self.model.model_type,
            }
            return

        by_dt = {float(point["dt_s"]): point for point in learned_points}
        refined = []
        for point in track.predicted_path:
            dt = float(point.get("dt_s", 0.0))
            learned = by_dt.get(dt)
            if learned is None:
                refined.append(point)
                continue
            next_point = dict(point)
            next_point.update(
                {
                    "lat": learned["lat"],
                    "lon": learned["lon"],
                    "alt": learned.get("alt", next_point.get("alt", track.alt)),
                    "speed": learned.get("speed", next_point.get("speed", track.speed)),
                    "heading": learned.get("heading", next_point.get("heading", track.heading)),
                    "model_used": "learned_numpy_sequence_predictor",
                    "prediction_model": "learned_numpy_sequence_predictor",
                    "learned_model": {
                        "loaded": True,
                        "model_type": self.model.model_type,
                        "model_path": str(self.model_path),
                        "dt_s": dt,
                    },
                }
            )
            st_gnn = dict(next_point.get("st_gnn", {}) or {})
            st_gnn["trained_model_loaded"] = True
            st_gnn["learned_model_provider"] = "numpy_ridge_sequence_predictor"
            next_point["st_gnn"] = st_gnn
            refined.append(next_point)
        track.predicted_path = refined
        track.metadata["learned_predictor"] = {
            "loaded": True,
            "applied": True,
            "model_type": self.model.model_type,
            "model_path": str(self.model_path),
        }
