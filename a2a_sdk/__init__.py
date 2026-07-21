"""A2A distributed-agent SDK.

Lightweight facades over the distributed-agent interfaces:

- :class:`AgentRuntimeSDK`  -- Agent side: registration, heartbeat, state
  reporting, skill/model registration and recovery handling in one entry point.
- :class:`SchedulerSDK`     -- Scheduler side: dynamic discovery, delayed
  binding, task dispatch/result, exception classification and recovery
  notification in one entry point.

Convenience re-exports of the building blocks are also provided.
"""

from a2a_sdk.agent_sdk import AgentRuntimeSDK
from a2a_sdk.scheduler_sdk import SchedulerSDK
from commander_agent.scheduling_policy import (
    JsonSchedulerFeedbackStore,
    SchedulerFeedbackStore,
    SchedulingPolicy,
)
from commander_agent.task_decomposer import PlannedActivity, TaskDecomposer, TaskPlan
from model_registry import AlgorithmModel, ModelRegistry, build_model
from skill_catalog import (
    PROFESSIONAL_SKILLS,
    build_skill,
    professional_skills_for_role,
    skills_for_capabilities,
)

__all__ = [
    "AgentRuntimeSDK",
    "SchedulerSDK",
    "TaskDecomposer",
    "TaskPlan",
    "PlannedActivity",
    "SchedulingPolicy",
    "SchedulerFeedbackStore",
    "JsonSchedulerFeedbackStore",
    "AlgorithmModel",
    "ModelRegistry",
    "build_model",
    "PROFESSIONAL_SKILLS",
    "build_skill",
    "professional_skills_for_role",
    "skills_for_capabilities",
]

__version__ = "0.1.0"
