# Multimodal Mamba Fusion

TIA python_http_service package for `multimodal_mamba_fusion`.

The small CPU profile loads the trained
`models/checkpoints/mamba_fusion_s.safetensors` artifact. The 412,688-parameter
block combines depthwise temporal convolution, gated state projection, residual
mixing, and normalized pooling. Unequal input vector lengths are safely padded
or truncated to 256 dimensions.

Tracks with a matching `sensor_id` receive local-plus-global fusion. Tracks
without an explicit sensor association receive the global multimodal vector;
the service no longer assigns modalities to tracks by incidental list position.

On 480 deterministic synthetic holdout sequences, trained fusion reached mean
cosine similarity 0.771683 and RMSE 0.042234. Normalized mean fusion reached
0.679153 and 0.050066. These metrics cover embedding fusion only; the package
does not encode raw images/audio/text, and they are not operational sensor
performance claims.

```powershell
python scripts/train_multimodal_mamba_fusion.py
$env:TIA_USE_MOCK="0"
$env:TIA_COMPUTE_PROFILE="small"
python services/multimodal_mamba_fusion/app/main.py
```
