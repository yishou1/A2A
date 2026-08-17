"""战场传感器模拟：生成适配三技能流水线的 SensorBatch。"""

from .battlefield import BattlefieldSimulation, SimulationConfig
from .images import encode_image_b64, load_base_scene_rgb, make_damaged_scene_rgb

__all__ = [
    "BattlefieldSimulation",
    "SimulationConfig",
    "encode_image_b64",
    "load_base_scene_rgb",
    "make_damaged_scene_rgb",
]
