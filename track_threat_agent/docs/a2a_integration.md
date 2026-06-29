# A2A Gateway Integration

This document explains how an A2A Gateway can discover and call `track-threat-group-agent-demo`.

当前版本是一个独立后端 Agent，不内置前端，也不依赖 AMOS 源码。A2A Gateway 的职责是发现 Agent、调用 Agent，并把返回的 artifact/events 转给态势前端、消息总线、AMOS 适配器或其他下游模块。

## Role

`track-threat-group-agent-demo` acts as a downstream situation-awareness Agent. It receives perception results, maintains simulated tracks, predicts short-term trajectories, detects likely groups, analyzes protected-asset impact, and returns a combined artifact with integration-friendly events.

Safety boundary: all `threat` and `risk` fields mean demo attention priority only. The Agent does not perform weapon control, attack recommendation, guidance, or engagement decisions.

## Discovery

The Gateway can discover this Agent in either of two ways.

### Option 1: Nacos Service Metadata

When `NACOS_ENABLED=true`, the service tries to register itself with Nacos. Registration is best-effort. If the Nacos SDK is missing or the server is unavailable, the FastAPI service still starts.

Expected metadata:

```text
agent_id=track-threat-group-agent-01
role=track_threat
status=idle
a2a_endpoint=http://{SERVICE_IP}:{SERVICE_PORT}/a2a/perception-result
send_message_endpoint=http://{SERVICE_IP}:{SERVICE_PORT}/sendMessage
send_message_stream_endpoint=http://{SERVICE_IP}:{SERVICE_PORT}/sendMessageStream
work_list_endpoint=http://{SERVICE_IP}:{SERVICE_PORT}/workflows/{workflow_id}/work-list
health_endpoint=http://{SERVICE_IP}:{SERVICE_PORT}/health
agent_card=http://{SERVICE_IP}:{SERVICE_PORT}/.well-known/agent-card.json
legacy_agent_card=http://{SERVICE_IP}:{SERVICE_PORT}/agent-card
skills=trajectory_tracking,trajectory_prediction,threat_ranking,group_detection,group_threat_ranking,protected_asset_impact_analysis
algorithm_levels=small,medium,large
asset_events=asset.updated,asset.relationship.updated
artifact_events=track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,protected.asset.updated,asset.impact.updated
```

Relevant environment variables:

```bash
NACOS_ENABLED=false
NACOS_SERVER=127.0.0.1:8848
NACOS_NAMESPACE=public
SERVICE_NAME=A2A-Agent
SERVICE_IP=127.0.0.1
SERVICE_PORT=8102
AGENT_ROLE=track_threat
AGENT_STATUS=idle
HEARTBEAT_INTERVAL=5
```

When integrating with the shared `yishou1/A2A` repository, prefer `SERVICE_NAME=A2A-Agent` and discover by metadata:

```text
role=track_threat
status=idle
```

### Option 2: Direct Agent Card

The Gateway can call:

```http
GET /.well-known/agent-card.json
GET /agent-card
```

The well-known path is the preferred discovery path. `/agent-card` is retained as a simple compatibility endpoint. The response describes capabilities, supported algorithm levels, A2A endpoint, output event types, safety boundary, and Nacos discovery metadata.

## Invocation

### Direct Perception Result

The Gateway calls:

```http
POST /a2a/perception-result
Content-Type: application/json
```

Example:

```json
{
  "task_id": "a2a-task-001",
  "message_type": "perception_result",
  "algorithm_level": "medium",
  "scene": {
    "protected_zone_lat": 31.2304,
    "protected_zone_lon": 121.4737,
    "protected_radius_m": 30000,
    "protected_assets": [
      {
        "asset_id": "blue-c2-node",
        "asset_name": "Blue C2 Node",
        "asset_type": "command_post",
        "lat": 31.2304,
        "lon": 121.4737,
        "protection_radius_m": 9000,
        "criticality": 0.95,
        "status": "protected"
      }
    ]
  },
  "detections": [
    {
      "detection_id": "det-001",
      "object_type": "aircraft",
      "timestamp": 1000,
      "lat": 31.42,
      "lon": 121.30,
      "alt": 7600,
      "speed": 210,
      "heading": 132,
      "confidence": 0.94,
      "source_agent": "gateway",
      "metadata": {}
    }
  ]
}
```

### A2A Workflow Envelope

The Agent also supports the workflow-style payload used by the shared A2A repository:

```http
POST /sendMessage
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "command": "analyze_perception_result",
  "role": "track_threat",
  "work_list": [
    {"activity": "perception_fusion", "role": "recon"},
    {"activity": "track_threat_analysis", "role": "track_threat"},
    {"activity": "situation_display", "role": "commander"}
  ],
  "payload": {
    "task_id": "task-001",
    "message_type": "perception_result",
    "algorithm_level": "medium",
    "scene": {},
    "detections": []
  }
}
```

`work_item` is an idempotency key. If the same work item is retried, the Agent returns the cached artifact and does not update track history or DBN state again.

The Agent stores the latest `work_list` snapshot per workflow:

```http
GET /workflows/{workflow_id}/work-list
```

`POST /sendMessageStream` provides SSE progress events and ends with a completed artifact event.

## Minimum Detection Fields

Each detection should include at least:

- `detection_id`
- `object_type`: `aircraft`, `ship`, `uav`, or `unknown`
- `timestamp`
- `lat`
- `lon`
- `speed`
- `heading`
- `confidence`

Recommended fields:

- `alt`
- `source_agent`
- `metadata`

## Output Artifact

The response is:

```json
{
  "task_id": "a2a-task-001",
  "message_type": "track_threat_group_artifact",
  "status": "completed",
  "artifact": {
    "tracks": [],
    "threats": [],
    "groups": [],
    "unified_threat_ranking": [],
    "events": [],
    "summary": {}
  }
}
```

Artifact sections:

- `tracks`: current track states, history paths, predicted paths, quality, metadata, and anomaly flags.
- `threats`: per-track attention-priority scores, levels, ranks, factors, and evidence.
- `groups`: likely formations/groups, envelopes, centroid predictions, cohesion, group scores, and evidence.
- `protected_assets`: protected blue-side assets included in the scene.
- `asset_impacts`: simulation-only impact-priority assessments between tracks and protected assets.
- `unified_threat_ranking`: a combined list of tracks and groups sorted by score descending. Each item includes `rank`, `item_type`, `item_id`, `entity_type`, `entity_id`, `score`, and `level`.
- `events`: AMOS-style event payloads.
- `summary`: counts and top scores.

## AMOS Event Writeback

The Gateway can emit each `artifact.events[]` item into the AMOS Event Bus.

Supported event types:

- `asset.updated`
- `asset.relationship.updated`
- `track.updated`
- `threat.updated`
- `track.group.updated`
- `threat.group.updated`
- `threat.ranking.updated`
- `protected.asset.updated`
- `asset.impact.updated`

For AMOS asset management, process `asset.updated` as an upsert. Track assets use `asset_category=tracked_object`; group assets use `asset_category=track_group`. `asset.relationship.updated` links a group asset to member track assets.

The `threat.updated` event is shaped to be compatible with places that expect a `ThreatReport`-like payload:

```json
{
  "event_type": "threat.updated",
  "threat_id": "thr-...",
  "track_id": "trk-...",
  "threat_type": "aircraft",
  "lat": 31.42,
  "lon": 121.30,
  "alt": 7600,
  "heading": 132,
  "speed": 210,
  "confidence": 0.92,
  "source": "sim-radar",
  "timestamp": 1000,
  "metadata": {
    "threat_score": 0.61,
    "level": "medium",
    "rank": 1,
    "evidence": [],
    "factors": {}
  }
}
```

Again, `threat` here means simulated attention priority, not target selection or engagement advice.

## AMOS Bridge Option

If the team later uses an AMOS web app directly, the integration can also bypass an Event Bus during demos:

```text
AMOS C2 Console
  -> /api/v1/track-threat/start or /pull
  -> Track-Threat Agent /demo/start or /demo/state
  -> artifact
  -> AMOS sim_assets / sim_threats
  -> AMOS native map
```

The bridge belongs in the AMOS repository, not in this standalone Agent repository. A typical AMOS-side bridge would include files like:

```text
web/routes/track_threat_agent.py
web/static/js/track_threat_overlay.js
```

This is useful for classroom or local integration demos. For a multi-team deployment, prefer the A2A Gateway/Event Bus path so every team can consume the same events.
