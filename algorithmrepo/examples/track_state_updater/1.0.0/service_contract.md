# Service Contract

- GET `/track_state_updater/health`
- GET `/track_state_updater/metadata`
- POST `/track_state_updater/predict`

Port: `9022`

Algorithm class: `M05 多目标跟踪与定位`

Input: structured `detections` and optional `existing_tracks`.

Output: updated `tracks`, update events, and active-track summary.

Safety boundary: demo-level track state maintenance only; no production sensor tracking, weapon control, or engagement advice.
