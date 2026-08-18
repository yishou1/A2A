# marl_ppo_task_scheduler_onnx

Native ONNX Runtime M13 package exported from the trained small-profile
`models/checkpoints/marl_ppo_scheduler_s.safetensors` checkpoint.

Each row contains one 104-dimensional agent observation and a nine-action mask.
Action zero means idle; actions one through eight reference target slots. The
graph returns deterministic actions, probabilities, masked logits, and critic
values. Use `marl_ppo_task_scheduler` when raw battlefield objects must be
converted to observations and assignments must be de-duplicated across agents.

Regenerate the M08 and M13 artifacts with:

```powershell
python scripts/export_policy_generative_onnx_models.py
```
