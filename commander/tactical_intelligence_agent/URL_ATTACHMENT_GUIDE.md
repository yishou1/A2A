# URL 传图 / 存图机制说明

> 适用角色：`tactical_intelligence`  
> 相关代码：`workflow_payloads.py`、`attachment_fetcher.py`、`attachment_uploader.py`、`tactical_intelligence_agent/`

本文说明战术情报 Agent（TIA）如何通过 **对象存储 URI** 接收原图、处理后 **写回对象存储**，并让下游 Agent 通过 **URL 读取产物**，从而避免 HTTP 消息里 **base64 直传大图** 的问题。

---

## 1. 为什么要用 URL，不用直传

A2A 工作流对附件有统一约束（见 `workflow_payloads.py`）：

| 允许 | 禁止 |
|------|------|
| `https://...` 签名 URL | `base64` / `bytes` / `data` 等内联字段 |
| `s3://` / `minio://` 等逻辑 URI | `file://` 本地路径 |

校验逻辑：

```126:134:workflow_payloads.py
def normalize_attachment_ref(attachment: Any) -> Dict[str, Any]:
    attachment_mapping = dict(_ensure_mapping(attachment, "attachment"))

    inline_fields = [field for field in INLINE_ATTACHMENT_FIELDS if field in attachment_mapping and attachment_mapping[field] not in (None, "", [], {}, b"")]
    if inline_fields:
        raise ValueError(
            "attachments must reference object storage only; inline payload fields are not allowed: "
            + ", ".join(sorted(inline_fields))
        )
```

**好处：**

- HTTP 请求体只传 URI + checksum，体积小、可幂等重放  
- 图片存在 MinIO/S3，多 Agent 按需 GET，不必重复携带  
- 与 Commander / recon / artillery 的附件协议一致  

**例外（仅本地 demo）：** 设置 `TIA_ALLOW_INLINE_FRAMES=1` 时，可在 payload 里额外附 `sensor_frames[].image_base64`；生产环境应关闭。

---

## 2. 整体数据流

```
前端 / Commander
    │  POST /sendMessage
    │  attachments[].uri  ──────────────┐
    ▼                                   │ GET 原图
payload_adapter.py                      ▼
    │  转成 SensorBatch              对象存储 (MinIO/S3)
    ▼                                   │
decode_image_from_frame()  ◄────────────┘
    │
    ▼
三技能流水线 (感知 → 认知 → 通信)
    │
    ▼
artifact_publisher.py
    │  画标注框 → PUT 产物
    ▼
对象存储 (processed/)
    │
    ▼
响应 output_attachments[].uri  ──► 下游 Agent GET 读取
```

---

## 3. 输入：前端如何通过 URL 传图

### 3.1 Commander 任务 JSON 形态

```json
{
  "workflow_id": "wf-001",
  "work_item": "wf-001:activity-tia",
  "command": "process_intelligence",
  "attachments": [
    {
      "id": "att-1",
      "uri": "https://minio.example.local/a2a/recon/P0002.png",
      "kind": "image",
      "mime_type": "image/png",
      "checksum": {
        "algorithm": "sha256",
        "value": "abc123..."
      },
      "meta": {
        "sensor_id": "EO-1",
        "modality": "eo_ir"
      }
    }
  ],
  "context": {
    "output_storage_prefix": "https://minio.example.local/a2a/tia/processed"
  }
}
```

要点：

- **`attachments[].uri`**：原图在对象存储上的地址（推荐 https 可 GET 的 URL）  
- **`checksum`**：完整性校验（协议必填）  
- **`context.output_storage_prefix`**（可选）：本次任务产物上传前缀，会覆盖 yaml 里的 `uri_prefix`  

### 3.2 载荷适配：URI → SensorFrame

`payload_adapter.py` 把每个 attachment 变成一帧，**不在内存里塞图片字节**，只保留引用：

```32:50:tactical_intelligence_agent/payload_adapter.py
def _frame_from_attachment(attachment: dict[str, Any], index: int) -> SensorFrame:
    attachment_id = attachment.get("id") or f"att-{index:03d}"
    modality = _modality_for_attachment(attachment)
    meta = dict(attachment.get("meta") or {})
    meta["attachment_uri"] = attachment["uri"]
    meta["checksum"] = attachment.get("checksum")

    payload: dict[str, Any] = {"attachment_ref": attachment}
    if modality == SensorModality.TEXT_REPORT:
        payload["text"] = meta.get("text") or f"attachment:{attachment['uri']}"
    else:
        payload["image_uri"] = attachment["uri"]

    return SensorFrame(
        sensor_id=str(meta.get("sensor_id") or attachment_id),
        modality=modality,
        payload=payload,
        metadata=meta,
    )
```

视觉帧 payload 里会有：

- `image_uri`：快速读取用  
- `attachment_ref`：完整 attachment 结构（含 checksum、meta）  

### 3.3 按需拉取：GET 原图

推理层统一通过 `decode_image_from_frame()` 解码。生产路径走 URI：

```39:69:agent/inference/utils.py
def decode_image_from_frame(frame: dict[str, Any]) -> np.ndarray | None:
    """从帧 payload 解码为 RGB uint8 数组 (H,W,3)。

    支持：
    - ``image_base64``（仿真 / 本地 demo）
    - ``image_uri`` / ``attachment_ref.uri``（前端对象存储 URL，推荐生产路径）
    """
    ...
    uri = resolve_image_uri_from_frame(frame)
    if uri:
        raw = fetch_bytes_from_uri(uri)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.array(img)
```

URI 提取顺序（`attachment_fetcher.py`）：

1. `payload.image_uri`  
2. `payload.attachment_ref.uri`  
3. `metadata.attachment_uri`  

HTTP GET 实现：

```26:54:attachment_fetcher.py
def fetch_bytes_from_uri(
    uri: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: Mapping[str, str] | None = None,
) -> bytes:
    ...
    if parsed.scheme in {"http", "https"}:
        response = requests.get(
            normalized,
            headers=_fetch_headers(headers),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content
```

**鉴权：** 若对象存储需要 Bearer，设置环境变量：

```powershell
$env:TIA_ATTACHMENT_AUTH = "Bearer <token>"
```

**注意：** 当前版本对 `s3://` 逻辑 URI **不能直接 GET**，需前端或 Commander 提供 **https 预签名下载 URL**。

### 3.4 哪些模块会读图

以下模块都调用 `decode_image_from_frame()`，因此 **一旦 URI 配好，整条感知链都能用 URL 输入**：

| 模块 | 文件 | 用途 |
|------|------|------|
| RT-DETR 检测 | `agent/inference/vision.py` | 目标检测 |
| 毁伤评估 | `agent/inference/damage.py` | Siamese Mask2Former |
| 多目标跟踪 | `agent/inference/tracking.py` | MOTR + Kalman + 3D geo |
| ImageBind | `agent/inference/models/imagebind_model.py` | 多模态嵌入 |
| 产物发布 | `tactical_intelligence_agent/artifact_publisher.py` | 重新解码原图用于画框 |

---

## 4. 处理：Agent 内部做什么

URI 拉图进内存后，业务与直传 base64 **完全相同**：

```
SensorBatch
  → PerceptionSkill   (RT-DETR / 毁伤 / EDL / 跟踪+geo)
  → CognitionSkill    (ImageBind / Mamba / 分类 / RAG)
  → CommunicationSkill (语义压缩 / MARL 路由)
  → SemanticIntelligencePacket
```

编排器在通信技能之后，追加产物上传：

```55:74:agent/orchestrator.py
        packet = self.communication.execute(
            mission_id,
            perception_out,
            cognition_out,
            subscriber_agents=subscribers,
            jamming_level=jamming,
        )

        try:
            from tactical_intelligence_agent.artifact_publisher import publish_processed_artifacts

            packet.output_attachments = publish_processed_artifacts(
                batch,
                perception_out,
                config=self._config,
            )
        except Exception:
            pass

        return packet
```

情报包新增字段：

```107:110:agent/models/schemas.py
    output_attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="处理后产物（如标注图）的对象存储引用，供下游 Agent 通过 URI 读取",
    )
```

---

## 5. 输出：如何把处理后的图存回存储

### 5.1 产物是什么

默认产物为 **带检测框的标注 JPEG**，路径规则：

```
{uri_prefix}/{mission_id}/{sensor_id}_det.jpg
```

示例：

```
https://minio.example.local/a2a/tia/processed/wf-001/EO-1_det.jpg
```

### 5.2 发布流程（artifact_publisher.py）

```88:129:tactical_intelligence_agent/artifact_publisher.py
    for frame in batch.frames:
        if frame.modality not in VISUAL_MODALITIES:
            continue

        frame_dict = frame.model_dump(mode="json")
        rgb = decode_image_from_frame(frame_dict)
        ...
        annotated = draw_detections_on_image(rgb, dets)
        jpeg_bytes = encode_jpeg_bytes(annotated)

        object_name = f"{sensor_id}_det.jpg"
        object_uri = f"{uri_prefix}/{mission_id}/{object_name}"
        local_path = staging_dir / object_name
        local_path.write_bytes(jpeg_bytes)
        ...
        ref = upload_attachment_file(
            local_path,
            object_uri,
            upload_url=upload_url,
            ...
        )
```

步骤：

1. 再次从 **原图 URI** 解码 RGB（与检测用同一张图）  
2. 按 `sensor_id` 聚合检测框，调用 `agent/inference/annotate.py` 画框  
3. 写入本地暂存目录 `data/output/artifacts/{mission_id}/`  
4. 调用 `upload_attachment_file()` PUT 到对象存储  
5. 返回标准 `attachment_ref`（含 uri、checksum、meta）  

产物 meta 示例字段：

| 字段 | 含义 |
|------|------|
| `source_attachment_uri` | 输入原图 URI |
| `detection_count` | 该帧检测框数量 |
| `artifact_type` | 固定为 `annotated_detection` |
| `local_staging_path` | 仅在上传跳过时出现，本地备份路径 |

### 5.3 上传实现（attachment_uploader.py）

```55:127:attachment_uploader.py
def upload_attachment_file(
    source_path: str | Path,
    object_uri: str,
    *,
    upload_url: str | None = None,
    ...
) -> Dict[str, Any]:
    ...
    checksum_value = sha256_file(path)
    ...
    target_url = _default_upload_target(object_uri, upload_url)
    ...
    response = requests.put(target_url, data=file_handle, headers=headers, timeout=timeout)
    response.raise_for_status()

    return build_attachment_ref(
        object_uri,
        checksum={"algorithm": "sha256", "value": checksum_value},
        ...
    )
```

上传策略：

| `object_uri` 类型 | 行为 |
|-------------------|------|
| `https://...` 且可 PUT | 直接 PUT 到该 URL |
| `s3://bucket/key` | 必须提供 **`upload_url`（预签名 PUT）** 或自定义 `uploader` |
| 无法 PUT | 仅写本地暂存 + 返回带 `local_staging_path` 的逻辑 URI 引用 |

### 5.4 HTTP 响应里如何交给下游

`sendMessage` 响应除 `targets` 外，增加 `output_attachments`：

```140:152:tactical_intelligence_agent/service.py
            return {
                "work_item": work_item,
                ...
                "intelligence_packet_id": packet.packet_id,
                "target_count": len(packet.targets),
                "output_attachments": packet.output_attachments,
                ...
            }
```

SSE 流式接口在末条 `Completed` 事件的 `intelligence_packet` 里同样包含 `output_attachments`。

**下游 Agent 用法：** 读取 `output_attachments[i].uri`，用 GET 拉标注图；结构化目标仍读 `intelligence_packet.targets`（含 geo、威胁等）。

---

## 6. 配置说明

### 6.1 config/default.yaml

```yaml
artifact_storage:
  enabled: false                                          # 改为 true 开启产物上传
  uri_prefix: ""                                          # 输出 URI 前缀
  upload_url: null                                        # s3:// 逻辑 URI 时的预签名 PUT
  local_staging_dir: data/output/artifacts                # 本地暂存（备份 / 联调）
  mime_type: image/jpeg
  kind: image
```

### 6.2 环境变量（覆盖 yaml）

| 变量 | 作用 |
|------|------|
| `TIA_ARTIFACT_ENABLED=1` | 开启产物上传 |
| `TIA_ARTIFACT_URI_PREFIX` | 输出 URI 前缀，如 `https://minio.xxx/a2a/tia/processed` |
| `TIA_ARTIFACT_UPLOAD_URL` | 预签名 PUT 地址（`s3://` 逻辑 URI 时必填） |
| `TIA_ATTACHMENT_AUTH` | 拉原图时的 Authorization 头 |

任务级覆盖：在 Commander payload 的 `context.output_storage_prefix` 传入本次输出前缀。

---

## 7. 完整 JSON 示例

### 输入（前端 → TIA）

```json
{
  "workflow_id": "wf-001",
  "work_item": "wf-001:activity-tia",
  "command": "process_intelligence",
  "attachments": [{
    "id": "att-1",
    "uri": "https://minio.example.local/a2a/recon/P0002.png",
    "kind": "image",
    "checksum": { "algorithm": "sha256", "value": "deadbeef..." },
    "meta": { "sensor_id": "EO-1" }
  }],
  "context": {
    "output_storage_prefix": "https://minio.example.local/a2a/tia/processed"
  }
}
```

### 输出（TIA → 下游）

```json
{
  "status": "Accepted",
  "target_count": 12,
  "output_attachments": [{
    "id": "wf-001-EO-1-det",
    "uri": "https://minio.example.local/a2a/tia/processed/wf-001/EO-1_det.jpg",
    "kind": "image",
    "mime_type": "image/jpeg",
    "size_bytes": 98304,
    "checksum": { "algorithm": "sha256", "value": "..." },
    "meta": {
      "sensor_id": "EO-1",
      "modality": "eo_ir",
      "source_attachment_uri": "https://minio.example.local/a2a/recon/P0002.png",
      "detection_count": 12,
      "artifact_type": "annotated_detection"
    }
  }]
}
```

---

## 8. 联调步骤（MinIO 示例）

**1. 上传原图到 MinIO（前端或脚本）**

```powershell
# 使用项目自带 helper（需可 PUT 的 URL）
$env:PYTHONPATH = "."
python -c "
from attachment_uploader import upload_attachment_file
ref = upload_attachment_file(
    'datasets/battlefield/images/val/P0002.png',
    'https://minio.example.local/a2a/recon/P0002.png',
    upload_url='https://minio.example.local/a2a/recon/P0002.png?X-Amz-Signature=...',
)
print(ref)
"
```

**2. 启动 Agent 并开启产物上传**

```powershell
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
$env:TIA_ARTIFACT_ENABLED = "1"
$env:TIA_ARTIFACT_URI_PREFIX = "https://minio.example.local/a2a/tia/processed"
$env:TIA_ARTIFACT_UPLOAD_URL = "https://minio.example.local/...presigned-put..."
$env:TIA_NACOS_REGISTER = "0"
$env:TIA_PORT = "8016"
python tactical_intelligence_agent/main.py
```

**3. 发送带 attachment URI 的任务**

参考 `scripts/demo_tactical_intelligence_acceptance.py` 中的 payload 结构，把 `attachments[0].uri` 换成真实 MinIO 地址。

**4. 验证**

- 响应里 `output_attachments` 非空  
- 浏览器或 `curl` GET 该 URI 能打开标注图  
- 本地暂存目录 `data/output/artifacts/wf-001/` 有同名文件  

---

## 9. 与 export 脚本的区别

| | `scripts/export_rtdetr_detections.py` | Agent URL 链路 |
|--|--------------------------------------|----------------|
| 输入 | 本地文件夹 | 对象存储 URI |
| 输出 | 本地 `annotated/` + JSON | 对象存储 URI + `output_attachments` |
| 范围 | 仅 RT-DETR 检测 | 三技能 + geo + 敌我 + 路由 |
| 下游 | 人工查看 | 其他 Agent 通过 URL GET |

批量导出脚本适合 **离线验证模型**；正式联调用本文描述的 Agent 协议。

---

## 10. 代码文件索引

| 文件 | 职责 |
|------|------|
| `workflow_payloads.py` | 附件协议校验、禁止 base64 直传 |
| `attachment_fetcher.py` | 从 URI GET 原图字节 |
| `attachment_uploader.py` | 本地文件 PUT 到对象存储，生成 attachment_ref |
| `tactical_intelligence_agent/payload_adapter.py` | Commander JSON → SensorBatch |
| `agent/inference/utils.py` | `decode_image_from_frame()` 统一解码入口 |
| `agent/inference/annotate.py` | 画检测框、编码 JPEG |
| `tactical_intelligence_agent/artifact_publisher.py` | 产物生成 + 上传 |
| `agent/orchestrator.py` | 流水线末尾挂载 `output_attachments` |
| `tactical_intelligence_agent/service.py` | HTTP 响应透出 `output_attachments` |
| `agent/models/schemas.py` | `SemanticIntelligencePacket.output_attachments` |
| `config/default.yaml` | `artifact_storage` 配置块 |
| `tests/test_attachment_fetcher.py` | URI 拉取与解码单元测试 |

---

## 11. 测试

```powershell
cd D:\a2a_project\A2A-main
$env:PYTHONPATH = "."
python tests\test_attachment_fetcher.py
```

覆盖：URI 解析、HTTPS mock 拉取、base64 与 URI 两种解码路径。

---

## 12. 常见问题

**Q: 传了 URI 但检测结果为 0？**  
A: 检查 URI 是否 https 可 GET、`TIA_ATTACHMENT_AUTH` 是否正确、`decode_image_from_frame` 是否返回非空（可在感知 trace 里看检测数）。

**Q: `output_attachments` 为空？**  
A: 确认 `artifact_storage.enabled=true` 或 `TIA_ARTIFACT_ENABLED=1`，且配置了 `uri_prefix`。

**Q: 只有本地文件，没有上传到 MinIO？**  
A: `s3://` 逻辑 URI 必须配 `upload_url` 预签名 PUT；否则只会写 `local_staging_dir` 并返回带 `local_staging_path` 的引用。

**Q: demo 里为什么还能用 base64？**  
A: `TIA_ALLOW_INLINE_FRAMES=1` 时 demo 额外注入 `sensor_frames`，绕开 URI 拉图，仅供本地验收，生产应关闭。
